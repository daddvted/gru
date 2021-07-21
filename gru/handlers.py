import re
import json
import base64
import socket
import struct
import os.path
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
    is_port_open


class InvalidValueError(Exception):
    pass


class BaseMixin:
    def initialize(self, loop):
        self.context = self.request.connection.context
        self.loop = loop
        self.channel = None
        self.stream_idx = 0
        self.ssh_transport_client = None

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

    def exec_remote_cmd(self, cmd, probe_cmd=None):
        """
        Execute command(cmd or probe-command) on remote host

        :param cmd: Command to execute
        :param probe_cmd: Probe command to execute before 'cmd'
        :return: None
        """
        minion_id = self.get_value('minion', arg_type='query')
        LOG.debug(f'[exec_remote_cmd] Minion ID: {minion_id}')
        minion = MINIONS.get(minion_id)
        args = minion['args']
        LOG.debug(f'[exec_remote_cmd] Minion args: {args}')
        self.ssh_transport_client = self.create_ssh_client(args)

        # Use probe_cmd to detect file's existence
        if probe_cmd:
            chan = self.ssh_transport_client.get_transport().open_session()
            chan.exec_command(probe_cmd)
            ext = (chan.recv_exit_status())
            if ext:
                raise tornado.web.HTTPError(404, "Not found")

        transport = self.ssh_transport_client.get_transport()
        self.channel = transport.open_channel(kind='session')
        self.channel.exec_command(cmd)

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


class StreamUploadMixin(BaseMixin):
    content_type = None
    boundary = None

    def _get_boundary(self):
        """
        Return the boundary of multipart/form-data

        :return: FormData boundary or None if not found
        """
        self.content_type = self.request.headers.get('Content-Type', '')
        match = re.match('.*;\sboundary="?(?P<boundary>.*)"?$', self.content_type.strip())
        if match:
            return match.group('boundary')
        else:
            return None

    @staticmethod
    def _partition_chunk(chunk: bytes) -> (bytes, bytes):
        """
        A chunk has the format below:
        b'\r\nContent-Disposition: form-data; name="upload"; \
        filename="hello.txt"\r\nContent-Type: text/plain\r\n\r\nhello\r\n\r\nworld\r\n\r\n'

        after partition, this will return:
        'form_data_info': b'\r\nContent-Disposition: form-data; name="upload"; \
        filename="hello.txt"\r\nContent-Type: text/plain'

        'raw': b'hello\r\n\r\nworld\r\n\r\n'
        """
        form_data_info, _, raw = chunk.partition(b"\r\n\r\n")
        return form_data_info, raw

    def _write_chunk(self, chunk: bytes) -> None:
        trimmed_chunk = self._trim_trailing_carriage_return(chunk)
        self.channel.sendall(trimmed_chunk)

    @staticmethod
    def _extract_filename(data: bytes) -> str:
        LOG.debug(data)
        ptn = re.compile(b'filename="(.*)"')
        m = ptn.search(data)
        if m:
            name = m.group(1).decode()
        else:
            name = "untitled"
        # Replace spaces with underscore
        return re.sub(r'\s+', '_', name)

    @staticmethod
    def _trim_trailing_carriage_return(chunk: bytes) -> bytes:
        """
        Filter out trailing carriage return(\r\n),
        Not to use rstrip(), to make sure b'hello\n\r\n' won't become b'hello'
        Use str.rpartition() instead of str.rstrip to avoid b'hello\n\r\n' being stripped to b'hello'

        :param chunk:  Bytes string
        :return: Bytes string with '\r\n' filtered out
        """
        if chunk.endswith(b"\r\n"):
            # trimmed, _, _ = chunk.rpartition(b'\r\n')
            # return trimmed
            return chunk[:-2]
        return chunk

    async def data_received(self, data):
        # data format:
        # b'data:application/octet-stream;base64,L2JhY2t1cC9tYWlsL3ZtYWlsL3ZtYWlsMS9jZXRjeGwuY29tL2FuaG9uZ3poYW5'

        # A simple multipart/form-data
        # b'------WebKitFormBoundarysiqXYmhALsFpsMuh\r\nContent-Disposition: form-data; name="upload";
        # filename="hello.txt"\r\nContent-Type: text/plain\r\n\r\n
        # hello\r\n\r\nworld\r\n\r\n------WebKitFormBoundarysiqXYmhALsFpsMuh--\r\n'
        """

        :param data:
        :return: None
        """

        # if not self.boundary:
        #     self.boundary = self._get_boundary()
        #     LOG.debug(f"multipart/form-data boundary: {self.boundary}")

        # Split data with multipart/form-data boundary
        # sep = f'--{self.boundary}'
        # chunks = data.split(sep.encode('ISO-8859-1'))
        # chunks_len = len(chunks)
        # data_len = len(data)
        # print(data)
        # self.total += data_len
        # print(f'data length: {data_len}, total: {self.total}')

        buffers = data.split(b";base64,")
        if len(buffers) == 2:
            chunk = buffers[1]
        else:
            chunk = data

        await run_async_func(self.exec_remote_cmd, f'cat > /tmp/shit')
        await run_async_func(self._write_chunk, chunk)
        # with open("/tmp/shit", "wb+") as f:
        #     f.write(chunk)
        # print(chunk)
        # print(base64.b64decode(chunk))

        # DEBUG
        # print("=====================================")
        # print(f"Stream idx: {self.stream_idx}")
        # print(f"CHUNKS length: {len(chunks)}")

        # Data is small enough in one stream
        # if chunks_len == 3:
        #     form_data_info, raw = self._partition_chunk(chunks[1])
        #     self.filename = self._extract_filename(form_data_info)
        #     await run_async_func(self.exec_remote_cmd, f'cat > /tmp/{self.filename}')
        #     await run_async_func(self._write_chunk, raw)
        #     await run_async_func(self.ssh_transport_client.close)
        # else:
        #     if self.stream_idx == 0:
        #         form_data_info, raw = self._partition_chunk(chunks[1])
        #         self.filename = self._extract_filename(form_data_info)
        #         await run_async_func(self.exec_remote_cmd, f'cat > /tmp/{self.filename}')
        #         await run_async_func(self._write_chunk, raw)
        #     else:
        #         # Form data in the middle data stream
        #         if chunks_len == 1:
        #             await run_async_func(self._write_chunk, chunks[0])
        #         else:
        #             # 'chunks_len' == 2, the LAST stream
        #             await run_async_func(self._write_chunk, chunks[0])
        #             await run_async_func(self.ssh_transport_client.close)
        # self.stream_idx += 1

        # ====================================
        # OLD CODE
        # ====================================
        # # If chunk length is 0, which means the data received is the beginning of multipart/form-data
        # if chunk_length == 0:
        #     pass
        # # Chunk length is 4, means the data received is end of multipart/form-data
        # elif chunk_length == 4:
        #     # End, close file handler(or similar object)
        #     self.ssh_transport_client.close()
        # else:
        #     need2partition = re.match('.*Content-Disposition:\sform-data;.*', chunk.decode('ISO-8859-1'),
        #                               re.DOTALL | re.MULTILINE)
        #     if need2partition:
        #         header, _, part = chunk.partition('\r\n\r\n'.encode('ISO-8859-1'))
        #         if part:
        #             header_text = header.decode('ISO-8859-1').strip()
        #             if 'minion' in header_text:
        #                 pass
        #                 # self.minion_id = part.decode('ISO-8859-1').strip()
        #
        #             if 'upload' in header_text:
        #                 m = re.match('.*filename="(?P<filename>.*)".*', header_text, re.MULTILINE | re.DOTALL)
        #                 if m:
        #                     self.filename = m.group('filename')
        #                 else:
        #                     self.filename = 'untitled'
        #
        #                 self.filename = re.sub('\s+', '_', self.filename)
        #                 # A trick to create a remote file handler
        #                 self.exec_remote_cmd(f'cat > /tmp/{self.filename}')
        #                 self._write_chunk(part)
        #     else:
        #         self._write_chunk(chunk)


class IndexHandler(BaseMixin, tornado.web.RequestHandler):
    executor = ThreadPoolExecutor()

    # executor = ThreadPoolExecutor(max_workers=cpu_count() * 6)

    def initialize(self, loop):
        super(IndexHandler, self).initialize(loop=loop)
        # self.ssh_client = self.get_ssh_client()
        self.ssh_term_client = None
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
        return 'utf-8'

    def create_minion(self, args):
        ssh_endpoint = args[:2]
        LOG.info('Connecting to {}:{}'.format(*ssh_endpoint))

        term = self.get_argument('term', '') or 'xterm'
        shell_channel = self.ssh_term_client.invoke_shell(term=term)
        shell_channel.setblocking(0)
        minion = Minion(self.loop, self.ssh_term_client, shell_channel, ssh_endpoint)
        minion.encoding = conf.encoding if conf.encoding else self.get_server_encoding(self.ssh_term_client)
        return minion

    def get(self):
        LOG.debug(f"MINIONS: {MINIONS}")
        self.render('index.html', mode=conf.mode)

    async def post(self):
        args = self.get_args()
        try:
            self.ssh_term_client = self.create_ssh_client(args)
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
                "args": args
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
class UploadHandler(StreamUploadMixin, BaseMixin, tornado.web.RequestHandler):
    filename = ""

    def initialize(self, loop):
        super(UploadHandler, self).initialize(loop=loop)
        self.filename = self.get_value("file", arg_type="query")

    async def post(self):
        pass


class DownloadHandler(BaseMixin, tornado.web.RequestHandler):
    def initialize(self, loop):
        super(DownloadHandler, self).initialize(loop=loop)

    async def get(self):
        chunk_size = 1024 * 1024 * 1  # 1 MiB

        remote_file_path = self.get_value("filepath", arg_type="query")
        filename = os.path.basename(remote_file_path)
        LOG.debug(remote_file_path)

        try:
            self.exec_remote_cmd(cmd=f'cat {remote_file_path}', probe_cmd=f'ls {remote_file_path}')
        except tornado.web.HTTPError:
            self.write(f'Not found: {remote_file_path}')
            await self.finish()
            return

        self.set_header("Content-Type", "application/octet-stream")
        self.set_header("Accept-Ranges", "bytes")
        self.set_header("Content-Disposition", f"attachment; filename={filename}")

        while True:
            chunk = self.channel.recv(chunk_size)
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
