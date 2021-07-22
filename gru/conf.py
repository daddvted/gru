import os
import os.path


def get_bool_env(name, default=False) -> bool:
    """
    Return boolean value from environment varialbes, e.g.:
    ENV_NAME=true or ENV_NAME=1 will return True, or return False
    """
    result = default
    env_value = os.getenv(name)
    if env_value is not None:
        result = os.getenv(name).upper() in ("TRUE", "1")
    return result


class Conf(dict):
    def __init__(self, *args, **kwargs):
        super(Conf, self).__init__(*args, **kwargs)
        for arg in args:
            if isinstance(arg, dict):
                for k, v in arg.items():
                    self[k] = v
            if kwargs:
                for k, v in kwargs.items():
                    self[k] = v

    def __getattr__(self, item):
        return self.get(item)

    def __setattr__(self, key, value):
        self.__setitem__(key, value)

    def __setitem__(self, key, value):
        super(Conf, self).__setitem__(key, value)
        self.__dict__.update({key: value})

    def __delattr__(self, item):
        self.__delitem__(item)

    def __delitem__(self, key):
        super(Conf, self).__delitem__(key)
        del self.__dict__[key]


conf = Conf()
conf.mode = os.getenv("GRU_MODE", "term")   # Mode:<all|term|gru>
conf.host = os.getenv("GRU_HOST", "0.0.0.0")
conf.port = int(os.getenv("GRU_PORT", 8000))
conf.ssl_port = int(os.getenv("GRU_SSL_PORT", 8443))
conf.cert_file = os.getenv("GRU_CERT_FILE", "./ssl.crt")
conf.key_file = os.getenv("GRU_KEY_FILE", "./ssl.key")
conf.debug = get_bool_env("GRU_DEBUG", True)
conf.xsrf = get_bool_env("GRU_XSRF", False)
conf.origin = os.getenv("GRU_ORIGIN", "*")
conf.ws_ping = int(os.getenv("GRU_WS_PING", 0))
conf.timeout = int(os.getenv("GRU_TIMEOUT", 3))
conf.encoding = os.getenv("GRU_ENCODING", "")
conf.redis_host = os.getenv('REDIS_HOST', 'localhost')
conf.redis_port = os.getenv('REDIS_PORT', 6379)
conf.redis_db = os.getenv('REDIS_DB', 0)

