import socket
import tornado.websocket
from tornado.ioloop import IOLoop
from tornado.iostream import _ERRNO_CONNRESET
from tornado.util import errno_from_exception

from gru.utils import LOG
from gru.utils import MINIONS


class Minion:
    BUFFER_SIZE = 64 * 1024

    def __init__(self, loop, ssh, chan, remote_addr):
        self.id = str(id(self))
        self.chan = chan
        self.ssh = ssh
        self.loop = loop
        self.remote_addr = remote_addr
        self.fd = chan.fileno()
        self.data2send = []
        self.ws_handler = None
        self.mode = IOLoop.READ

    def __call__(self, fd, events):
        LOG.debug(f"[Minion.__call__]: {fd}, {events}")
        if events & IOLoop.READ:
            self.do_read()
        if events & IOLoop.WRITE:
            self.do_write()
        if events & IOLoop.ERROR:
            self.close(msg='IOLOOP ERROR')

    def update_event_handler(self, mode):
        if self.mode != mode:
            self.mode = mode
            self.loop.update_handler(self.fd, mode)
        if mode == IOLoop.WRITE:
            self.loop.call_later(0.1, self, self.fd, IOLoop.WRITE)

    def do_read(self):
        LOG.debug('minion {} on read'.format(self.id))
        try:
            data = self.chan.recv(self.BUFFER_SIZE)
        except socket.timeout as err:
            LOG.error(err)
            if errno_from_exception(err) in _ERRNO_CONNRESET:
                self.close(msg='do_read: chan error')
        else:
            LOG.debug(f'{data} from [{self.remote_addr[0]}:{self.remote_addr[1]}]')
            if not data:
                self.close(msg='BYE ~')
                return

            LOG.debug(f'{data} to [{self.ws_handler.src_addr[0]}:{self.ws_handler.src_addr[1]}]')
            try:
                self.ws_handler.write_message(data, binary=True)
            except tornado.websocket.WebSocketClosedError:
                self.close(msg='websocket closed')

    def do_write(self):
        LOG.debug(f'Minion {self.id} on write')
        if not self.data2send:
            return

        data = ''.join(self.data2send)
        LOG.debug(f'{data} to {self.remote_addr}')

        try:
            sent = self.chan.send(data)
        except socket.timeout as err:
            LOG.error(err)
            if errno_from_exception(err) in _ERRNO_CONNRESET:
                self.close(msg='do_write: chan error')
            else:
                self.update_event_handler(IOLoop.WRITE)
        else:
            self.data2send = []
            data = data[sent:]
            if data:
                self.data2send.append(data)
                self.update_event_handler(IOLoop.WRITE)
            else:
                self.update_event_handler(IOLoop.READ)

    def close(self, msg=None):
        LOG.info(f'Closing minion {self.id}: {msg}')
        if self.ws_handler:
            self.loop.remove_handler(self.fd)
            self.ws_handler.close(reason=msg)
        self.chan.close()
        self.ssh.close()
        LOG.info('Connection to {}:{} lost'.format(*self.remote_addr))

        m = MINIONS.pop(self.id, None)
        LOG.info(f"Minion(id: {self.id}) is popped out")
        LOG.debug(f"Minion details: {m}")
        LOG.debug(MINIONS)
