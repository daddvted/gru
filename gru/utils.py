import os
import ssl
import json
import paramiko
import logging
import socket
import asyncio
import concurrent.futures
import redis
from contextlib import closing, contextmanager
from paramiko.ssh_exception import AuthenticationException, SSHException
from tornado.log import enable_pretty_logging
from gru.conf import conf

enable_pretty_logging()

# MINIONS =  {
#     '140580981443160': {
#     'minion': <gru.minion.Minion object at 0x7fdb8f760a58>,
#     'args': ('172.16.66.10', 22, 'root', 'P@ssw0rd')}
# }
MINIONS = {}


def get_logging_level():
    """ Return logging level by the value of environment variable"""
    log_dict = {
        "info": logging.INFO,
        "debug": logging.DEBUG,
        "warning": logging.WARNING,
        "critical": logging.CRITICAL,
        "error": logging.ERROR,
    }

    lvl = os.getenv("LOG_LEVEL", "info").strip().lower()
    return log_dict[lvl]


def logger(name='gru'):
    log = logging.getLogger(name)
    log.setLevel(get_logging_level())
    return log


LOG = logger()


def get_ssl_context(options):
    if not options.cert_file and not options.key_file:
        return None
    elif not options.cert_file:
        raise ValueError('cert_file is not provided')
    elif not options.key_file:
        raise ValueError('key_file is not provided')
    elif not os.path.isfile(options.cert_file):
        raise ValueError(f'File {options.cert_file} does not exist')
    elif not os.path.isfile(options.key_file):
        raise ValueError(f'File {options.key_file} does not exist')
    else:
        ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(options.cert_file, options.key_file)
        return ssl_ctx


def get_sftp_client(*args):
    host, port, username, password = args
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.WarningPolicy())
        client.connect(hostname=host, port=port, username=username, password=password)
        sftp = client.open_sftp()
        return sftp
    except (AuthenticationException, SSHException, socket.error) as err:
        print(err)


def find_free_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(('', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


async def run_async_func(func, *args):
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, func, *args)
        return result


@contextmanager
def conn2redis(*args, **kwargs):
    try:
        r = redis.StrictRedis(*args, **kwargs, health_check_interval=30, decode_responses=True, socket_timeout=2,
                              socket_connect_timeout=2)
        yield r
    except redis.RedisError as err:
        LOG.error(f"redis error: {err}")
        LOG.error(f"{kwargs}")
        raise err
    except ConnectionRefusedError as err:
        LOG.error(err)


def get_redis_keys(filter=""):
    with conn2redis(host=conf.redis_host, port=conf.redis_port, db=conf.redis_db) as r:
        if filter:
            keys = r.scan_iter(filter)
        else:
            keys = r.scan_iter()
    return keys


def get_cache(cache_key: str):
    with conn2redis(host=conf.redis_host, port=conf.redis_port, db=conf.redis_db) as r:
        # Cached found, return directly
        data = r.get(cache_key)
        if data:
            LOG.info(f'CACHE FOUND: {cache_key}')
            return json.loads(data)
        # else:
        #     LOG.error(f"GET CACHE KEY({cache_key}) error")
        #     return None


def set_cache(cache_key: str, data):
    with conn2redis(host=conf.redis_host, port=conf.redis_port, db=conf.redis_db) as r:
        result = r.set(cache_key, json.dumps(data))
        if result:
            LOG.info(f'SET CACHE: {cache_key}')
        else:
            LOG.error(f"SET CACHE KEY({cache_key}) error")


def delete_cache(cache_key: str):
    with conn2redis(host=conf.redis_host, port=conf.redis_port, db=conf.redis_db) as r:
        r.delete(cache_key)


def flush_all_caches():
    with conn2redis(host=conf.redis_host, port=conf.redis_port, db=conf.redis_db) as r:
        r.flushall()


def is_port_open(port, host="localhost") -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        if sock.connect_ex((host, port)) == 0:
            return True
        else:
            return False


def fix_padding(data: bytes) -> bytes:
    missing_padding = 4 - len(data) % 4
    if missing_padding:
        return data + b'=' * missing_padding
