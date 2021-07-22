import re
import copy
import json
import base64
import socket
import struct
import os.path
import binascii
import weakref
import paramiko
import tornado.web
from json.decoder import JSONDecodeError
from concurrent.futures import ThreadPoolExecutor
import tornado
from tornado.ioloop import IOLoop
from tornado.process import cpu_count
from tornado.escape import json_decode

from gru.conf import conf
from gru.minion import Minion, recycle_minion, MINIONS
from gru.utils import LOG, run_async_func, find_free_port, get_cache, set_cache, delete_cache, get_redis_keys, \
    is_port_open, fix_padding


class InvalidValueError(Exception):
    pass


class BaseMixin:
    def initialize(self, loop):
        print("[BaseMixin] initialize")
        self.context = self.request.connection.context
        self.loop = loop
        self.transport_channel = None
        self.stream_idx = 0
        self.ssh_client = None
        self.minion_id = None

    @staticmethod
    def create_ssh_client(args) -> paramiko.SSHClient:
        print(f"[create_ssh_client]args: {args}")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.client.MissingHostKeyPolicy)
        try:
            ssh.connect(*args, allow_agent=False, look_for_keys=False, timeout=conf.timeout)
        except socket.error:
            print(args[:2])
            raise ValueError('Unable to connect to {}:{}'.format(*args[:2]))
        except (paramiko.AuthenticationException, paramiko.ssh_exception.AuthenticationException):
            raise ValueError('Authentication failed.')
        except EOFError:
            LOG.error("Got EOFError, retry")
            ssh.connect(*args, allow_agent=False, look_for_keys=False, timeout=conf.timeout)
        return ssh

    def detect_file_existense(self, filepath: str):
        chan = self.ssh_client.get_transport().open_session()
        chan.exec_command(f"ls {filepath}")
        ext = (chan.recv_exit_status())
        if ext:
            raise tornado.web.HTTPError(404, "Not found")

    def exec_remote_cmd(self, cmd):
        """
        Execute command(cmd or probe-command) on remote host

        :param cmd: Command to execute
        :return: None
        """

        # self.transport_channel = MINIONS[self.minion_id].get("transchan", None)
        # print(self.transport_channel)
        # if self.transport_channel:
        #     LOG.info(f"Transport channel found for Minion({self.minion_id})!")
        #     self.transport_channel.exec_command(cmd)
        # else:
        #     LOG.info(f"No transport channel, create one for Minion({self.minion_id}) !")
        #     transport = self.ssh_client.get_transport()
        #     self.transport_channel = transport.open_channel(kind='session')
        #     # self.transport_channel.setblocking(0)
        #
        #     MINIONS[self.minion_id]["transchan"] = self.transport_channel
        #     minion_copy = copy.copy(MINIONS[self.minion_id])
        #     print(minion_copy)
        #     minion_copy["transchan"] = self.transport_channel
        #     print(minion_copy)
        #     MINIONS[self.minion_id] = minion_copy
        #     print(MINIONS)
        #     self.transport_channel.exec_command(cmd)

        transport = self.ssh_client.get_transport()
        print(transport)
        chan = transport.accept(0.01)
        print("chan:", chan)
        if chan:
            self.transport_channel = chan
        else:
            LOG.info(f"No transport channel, create one for Minion({self.minion_id}) !")
            self.transport_channel = transport.open_channel(kind='session')



            # self.transport_channel.setblocking(0)


        self.transport_channel.exec_command(cmd)

    def get_value(self, name, arg_type=""):

        if arg_type == "query":
            value = self.get_query_argument(name)
        else:
            value = self.get_argument(name)

        if not value:
            raise InvalidValueError(f'{name} is missing')
        return value

    def get_client_endpoint(self) -> tuple:
        """
        Return client endpoint

        :return: (IP, Port) tuple
        """
        ip = self.request.remote_ip

        if ip == self.request.headers.get("X-Real-Ip"):
            port = self.request.headers.get("X-Real-Port")
        elif ip in self.request.headers.get("X-Forwarded-For", ""):
            port = self.request.headers.get("X-Forwarded-Port")
        else:
            return self.context.address[:2]
        port = int(port)
        return ip, port



class IndexHandler(BaseMixin, tornado.web.RequestHandler):
    executor = ThreadPoolExecutor()

    # executor = ThreadPoolExecutor(max_workers=cpu_count() * 6)

    def initialize(self, loop):
        super(IndexHandler, self).initialize(loop=loop)
        # self.ssh_client = self.get_ssh_client()
        self.debug = self.settings.get('debug', False)
        self.result = dict(id=None, status=None, encoding=None)

    def get_args(self):
        data = json_decode(self.request.body)
        LOG.debug(data)

        # Minion login won't pass hostname in form data
        hostname = data.get("hostname", "localhost")
        username = data["username"]
        password = data["password"]
        port = int(data["port"])
        args = (hostname, port, username, password)
        LOG.debug(f"Args for SSH: {args}")
        return args

    def get_server_encoding(self, ssh):
        try:
            _, stdout, _ = ssh.exec_command("locale charmap")
        except paramiko.SSHException as err:
            LOG.error(str(err))
        else:
            result = stdout.read().decode().strip()
            if result:
                return result

        LOG.warning('!!! Unable to detect default encoding')
        return 'utf-8'.upper()

    def create_minion(self, args):
        ssh_endpoint = args[:2]
        LOG.info('Connecting to {}:{}'.format(*ssh_endpoint))

        term = self.get_argument('term', '') or 'xterm'
        print("**** debug 0")
        shell_channel = self.ssh_client.invoke_shell(term=term)
        print("**** debug 1")
        shell_channel.setblocking(0)
        print("**** debug 2")
        minion = Minion(self.loop, self.ssh_client, shell_channel, ssh_endpoint)
        print("**** debug 3")
        minion.encoding = conf.encoding if conf.encoding else self.get_server_encoding(self.ssh_client)
        print("**** debug 4")
        return minion

    def get(self):
        LOG.debug(f"MINIONS: {MINIONS}")
        self.render('index.html', mode=conf.mode)

    async def post(self):
        args = self.get_args()
        try:
            self.ssh_client = self.create_ssh_client(args)
            minion = await run_async_func(self.create_minion, args)
        except InvalidValueError as err:
            # Catch error in self.get_args()
            raise tornado.web.HTTPError(400, str(err))
        except (ValueError, paramiko.SSHException, paramiko.ssh_exception.SSHException,
                paramiko.ssh_exception.AuthenticationException, socket.timeout) as err:
            LOG.error("====================")
            LOG.error(err)
            # Delete dangling cache
            if str(err).lower().startswith("unable to") and conf.mode != "term":
                delete_cache(str(args[1]))

            self.result.update(status=str(err))
        else:
            # if not minions:
            # GRU[ip] = minions
            # minion.src_addr = (ip, port)
            MINIONS[minion.id] = {
                "minion": minion,
                "args": args,
                "ssh": self.ssh_client,
            }
            self.loop.call_later(2, recycle_minion, minion)
            self.result.update(id=minion.id, encoding=minion.encoding)
            # self.set_secure_cookie("minion", minion.id)
        self.write(self.result)


class WSHandler(BaseMixin, tornado.websocket.WebSocketHandler):

    def initialize(self, loop):
        super(WSHandler, self).initialize(loop=loop)
        self.minion_ref = None

    def open(self):
        self.src_addr = self.get_client_endpoint()
        LOG.info('Connected from {}:{}'.format(*self.src_addr))

        try:
            # Get id from query argument from
            minion_id = self.get_value('id')
            LOG.debug(f"############ minion id: {minion_id}")

            minion = MINIONS.get(minion_id)
            if not minion:
                self.close(reason='Websocket failed.')
                return

            minion_obj = minion.get('minion', None)
            if minion_obj:
                # minions[minion_id]["minion"] = None
                self.set_nodelay(True)
                minion_obj.set_handler(self)
                self.minion_ref = weakref.ref(minion_obj)
                self.loop.add_handler(minion_obj.fd, minion_obj, IOLoop.READ)
            else:
                self.close(reason='Websocket authentication failed.')

        except (tornado.web.MissingArgumentError, InvalidValueError) as err:
            self.close(reason=str(err))

    def on_message(self, message):
        LOG.debug(f'{message} from {self.src_addr}')
        minion = self.minion_ref()
        try:
            msg = json.loads(message)
        except JSONDecodeError:
            return

        if not isinstance(msg, dict):
            return

        resize = msg.get('resize')
        if resize and len(resize) == 2:
            try:
                minion.chan.resize_pty(*resize)
            except (TypeError, struct.error, paramiko.SSHException):
                pass

        data = msg.get('data')
        if data and isinstance(data, str):
            minion.data_to_dst.append(data)
            minion.do_write()

    def on_close(self):
        LOG.info('Disconnected from {}:{}'.format(*self.src_addr))
        if not self.close_reason:
            self.close_reason = 'client disconnected'

        minion = self.minion_ref() if self.minion_ref else None
        if minion:
            minion.close(reason=self.close_reason)


@tornado.web.stream_request_body
class UploadHandler(BaseMixin, tornado.web.RequestHandler):
    filename = ""
    total = 0

    def initialize(self, loop):
        LOG.debug(MINIONS)
        super(UploadHandler, self).initialize(loop=loop)
        self.data = b''

    def prepare(self):
        self.minion_id = self.get_value("minion", arg_type="query")
        m = MINIONS.get(self.minion_id)
        print(m)
        # self.transport_channel = m["transchan"]
        self.ssh_client = m["ssh"]
        self.filename = self.get_value("file", arg_type="query")

    async def data_received(self, chunk: bytes):
        # print(f"Chunk length: {len(chunk)}")
        # self.total += len(chunk)
        self.data += chunk

    async def post(self):
        print(f"total length: {self.total}")
        tmp = await run_async_func(self.exec_remote_cmd, f'cat >> /tmp/shit')
        # print('===========================', tmp)
        await run_async_func(self._write_chunk, base64.urlsafe_b64decode(self.data))
        # with open("/tmp/shit", "ab") as f:
        #     f.write(base64.urlsafe_b64decode(self.data))
        #     f.flush()

    def _write_chunk(self, chunk: bytes) -> None:
        # f = self.ssh_client.open_sftp().file("/tmp/shit", mode="a", bufsize=0)
        # f.write(chunk)
        # f.flush()
        # f.close()

        # stdin, stdout, stderr = self.ssh_client.exec_command(f'cat >> /tmp/shit')
        # stdin.write(chunk)

        self.transport_channel.sendall(chunk)




class DownloadHandler(BaseMixin, tornado.web.RequestHandler):
    def initialize(self, loop):
        super(DownloadHandler, self).initialize(loop=loop)

    async def get(self):
        chunk_size = 1024 * 1024 * 1  # 1 MiB

        remote_file_path = self.get_value("filepath", arg_type="query")
        filename = os.path.basename(remote_file_path)
        LOG.debug(remote_file_path)

        try:
            self.detect_file_existense(remote_file_path)
            # self.exec_remote_cmd(cmd=f'cat {remote_file_path}', probe_cmd=f'ls {remote_file_path}')
            self.exec_remote_cmd(cmd=f'cat {remote_file_path}')
        except tornado.web.HTTPError:
            self.write(f'Not found: {remote_file_path}')
            await self.finish()
            return

        self.set_header("Content-Type", "application/octet-stream")
        self.set_header("Accept-Ranges", "bytes")
        self.set_header("Content-Disposition", f"attachment; filename={filename}")

        while True:
            chunk = self.transport_channel.recv(chunk_size)
            if not chunk:
                break
            try:
                # Write the chunk to response
                self.write(chunk)
                # Send the chunk to client
                await self.flush()
            except tornado.iostream.StreamClosedError:
                break
            finally:
                del chunk
                await tornado.web.gen.sleep(0.000000001)  # 1 nanosecond

        self.ssh_transport_client.close()
        try:
            await self.finish()
        except tornado.iostream.StreamClosedError as err:
            LOG.error(err)
            LOG.debug("Maybe user cancelled download")
        LOG.info(f"Download ended: {remote_file_path}")


class PortHandler(tornado.web.RequestHandler):
    async def get(self):
        random_port = await run_async_func(find_free_port)
        self.write({"port": random_port})


class RegisterHandler(tornado.web.RequestHandler):
    async def post(self):
        data = json_decode(self.request.body)
        await run_async_func(set_cache, data["port"], data)
        self.write("")


class DeregisterHandler(tornado.web.RequestHandler):
    async def delete(self, port):
        await run_async_func(delete_cache, str(port))
        self.write("")


class HostsHandler(tornado.web.RequestHandler):
    async def get(self):
        hosts = [get_cache(key) for key in get_redis_keys()]
        self.write(json.dumps(hosts))


class CleanHandler(tornado.web.RequestHandler):
    async def get(self):
        hosts = [get_cache(key) for key in get_redis_keys()]
        actual_hosts = []
        for host in hosts:
            if is_port_open(host["port"]):
                actual_hosts.append(host)
            else:
                delete_cache(host["port"])
        self.write(json.dumps(actual_hosts))


class NotFoundHandler(tornado.web.RequestHandler):
    def prepare(self):
        LOG.info("In NotFoundHandler")
        raise tornado.web.HTTPError(status_code=404, reason="Oops!")


class DebugHandler(tornado.web.RequestHandler):
    def get(self):
        self.write(json.dumps({k: v["args"] for k, v in MINIONS.items()}))
