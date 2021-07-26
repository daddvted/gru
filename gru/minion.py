import tornado.websocket
from tornado.ioloop import IOLoop
from tornado.iostream import _ERRNO_CONNRESET
from tornado.util import errno_from_exception

from gru.utils import LOG
from gru.utils import MINIONS


class Minion:
    BUFFER_SIZE = 64 * 1024

    def __init__(self, loop, ssh, chan, dst_addr):
        self.loop = loop
        self.ssh = ssh
        self.chan = chan
        self.dst_addr = dst_addr
        self.fd = chan.fileno()
        self.id = str(id(self))
        self.data_to_dst = []
        self.handler = None
        self.mode = IOLoop.READ
        self.closed = False

    def __call__(self, fd, events):
        LOG.debug(f"[Minion.__call__]: {fd}, {events}")
        if events & IOLoop.READ:
            self.do_read()
        if events & IOLoop.WRITE:
            self.do_write()
        if events & IOLoop.ERROR:
            self.close(reason='IOLOOP ERROR')

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
                self.close(reason='CHAN ERROR DOING READ ')
        else:
            LOG.debug(f'{data} from {self.dst_addr}')
            if not data:
                self.close(reason='BYE ~')
                return

            LOG.debug(f'{data} to {self.handler.src_addr}')
            try:
                self.handler.write_message(data, binary=True)
            except tornado.websocket.WebSocketClosedError:
                self.close(reason='WEBSOCKET CLOSED')

    def do_write(self):
        LOG.debug('minion {} on write'.format(self.id))
        if not self.data_to_dst:
            return

        data = ''.join(self.data_to_dst)
        LOG.debug(f'{data} to {self.dst_addr}')

        try:
            sent = self.chan.send(data)
        except (OSError, IOError) as e:
            LOG.error(e)
            if errno_from_exception(e) in _ERRNO_CONNRESET:
                self.close(reason='chan error on writing')
            else:
                self.update_handler(IOLoop.WRITE)
        else:
            self.data_to_dst = []
            data = data[sent:]
            if data:
                self.data_to_dst.append(data)
                self.update_handler(IOLoop.WRITE)
            else:
                self.update_handler(IOLoop.READ)

    def close(self, reason=None):
        if self.closed:
            return
        self.closed = True

        LOG.info(f'Closing minion {self.id}: {reason}')
        if self.handler:
            self.loop.remove_handler(self.fd)
            self.handler.close(reason=reason)
        self.chan.close()
        self.ssh.close()
        LOG.info('Connection to {}:{} lost'.format(*self.dst_addr))

        remove_minion(self)
        LOG.debug(MINIONS)


def remove_minion(minion):
    mid = MINIONS.pop(minion.id, None)
    LOG.info(f"Minion(id: {mid}) is popped out")


