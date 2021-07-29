import tornado.websocket
from tornado.ioloop import IOLoop
from tornado.iostream import _ERRNO_CONNRESET
from tornado.util import errno_from_exception

from gru.utils import LOG
from gru.utils import MINIONS


class Minion:
    BUFFER_SIZE = 64 * 1024

    def __init__(self, loop, ssh, chan, remote_addr):
        self.loop = loop
        self.ssh = ssh
        self.chan = chan
        self.remote_addr = remote_addr
        self.fd = chan.fileno()
        self.id = str(id(self))
        self.data2send = []
        self.handler = None
        self.mode = IOLoop.READ

    def __call__(self, fd, events):
        LOG.debug(f"[Minion.__call__]: {fd}, {events}")
        if events & IOLoop.READ:
            self.do_read()
        if events & IOLoop.WRITE:
            self.do_write()
        if events & IOLoop.ERROR:
            self.close(msg='IOLOOP ERROR')

    def set_handler(self, handler):
        if not self.handler:
            self.handler = handler

    def update_handler(self, mode):
        if self.mode != mode:
            self.loop.update_handler(self.fd, mode)
            self.mode = mode
        if mode == IOLoop.WRITE:
            self.loop.call_later(0.1, self, self.fd, IOLoop.WRITE)

    def do_read(self):
        LOG.debug('minion {} on read'.format(self.id))
        try:
            data = self.chan.recv(self.BUFFER_SIZE)
        except (OSError, IOError) as e:
            LOG.error(e)
            if errno_from_exception(e) in _ERRNO_CONNRESET:
                self.close(msg='CHAN ERROR DOING READ ')
        else:
            LOG.debug(f'{data} from [{self.remote_addr[0]}:{self.remote_addr[1]}]')
            if not data:
                self.close(msg='BYE ~')
                return

            LOG.debug(f'{data} to [{self.handler.src_addr[0]}:{self.handler.src_addr[1]}]')
            try:
                self.handler.write_message(data, binary=True)
            except tornado.websocket.WebSocketClosedError:
                self.close(msg='WEBSOCKET CLOSED')

    def do_write(self):
        LOG.debug(f'Minion {self.id} on write')
        if not self.data2send:
            return

        data = ''.join(self.data2send)
        LOG.debug(f'{data} to {self.remote_addr}')

        try:
            sent = self.chan.send(data)
        except (OSError, IOError) as e:
            LOG.error(e)
            if errno_from_exception(e) in _ERRNO_CONNRESET:
                self.close(msg='chan error on writing')
            else:
                self.update_handler(IOLoop.WRITE)
        else:
            self.data2send = []
            data = data[sent:]
            if data:
                self.data2send.append(data)
                self.update_handler(IOLoop.WRITE)
            else:
                self.update_handler(IOLoop.READ)

    def close(self, msg=None):
        LOG.info(f'Closing minion {self.id}: {msg}')
        if self.handler:
            self.loop.remove_handler(self.fd)
            self.handler.close(reason=msg)
        self.chan.close()
        self.ssh.close()
        LOG.info('Connection to {}:{} lost'.format(*self.remote_addr))

        m = MINIONS.pop(self.id, None)
        LOG.info(f"Minion(id: {self.id}) is popped out")
        LOG.debug(f"Minion details: {m}")
        LOG.debug(MINIONS)
