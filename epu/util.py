import os
import ConfigParser

KEY_MESSAGING = "ionmessaging"
KEY_BROKERHOST = "broker_host"
KEY_BROKERCREDFILE = "broker_credfile"
KEY_SYSNAME = "sysname"

class StateWaitException(Exception):
    def __init__(self, msg):
        Exception.__init__(self)
        self.msg = msg
    def __str__(self):
        return self.msg

def get_class(kls):
    """Get a class by name
    """
    parts = kls.split('.')
    module = ".".join(parts[:-1])
    m = __import__( module )
    for comp in parts[1:]:
        m = getattr(m, comp)
    return m

def parse_messaging_config(path):
    """Given a messaging config file created by the deployment recipe, return all the necessary information
    in a dict with the following keys:
    
        "broker_host"
        "broker_credfile" (Might be None)
        "sysname"
        "broker_vhost"
    """
    
    if not os.path.exists(path):
        raise Exception("There is no messaging config file: %s" % path)

    config = ConfigParser.ConfigParser()
    config.readfp(open(path))

    if not config.has_section(KEY_MESSAGING):
        raise Exception("There is no '%s' section in config file: %s" % (KEY_MESSAGING, path))

    retconf = {}
    retconf[KEY_BROKERHOST] = _get_option(config, KEY_BROKERHOST)
    retconf[KEY_SYSNAME] = _get_option(config, KEY_SYSNAME)
    retconf[KEY_BROKERCREDFILE] = _get_option(config, KEY_BROKERCREDFILE, required=False)

    return retconf

def _get_option(config, key, required=True):
    if config.has_option(KEY_MESSAGING, key):
        return config.get(KEY_MESSAGING, key)
    if required:
        raise Exception("The '%s' conf is missing in the messaging configuration file" % key)
    return None

def create_container_config(messaging_conf_path):
    """Given the path to a compliant messaging.conf file, construct a container config to boot
    an ION container (intended for one-off scripts).
    """

    confdict = parse_messaging_config(messaging_conf_path)

    from ion.core.cc import service
    config = service.Options()
    config['broker_host'] = confdict[KEY_BROKERHOST]
    config['broker_vhost'] = "/"
    config['no_shell'] = True
    config['args'] = 'sysname=%s' % confdict[KEY_SYSNAME]
    config['script'] = None

    if confdict[KEY_BROKERCREDFILE]:
        config['broker_credfile'] = confdict[KEY_BROKERCREDFILE]

    return config

def disable_ion_busyloop_detect():
    if not "ION_NO_BUSYLOOP_DETECT" in os.environ:
        os.environ['ION_NO_BUSYLOOP_DETECT'] = "1"
