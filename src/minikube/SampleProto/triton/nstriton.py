#!/usr/bin/env python

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import json
import time
import signal

# Configuring standard output log handler
NSTRITON_LOG_FORMAT='%(asctime)s  - %(levelname)s - [%(filename)s:%(funcName)-10s]  (%(threadName)s) %(message)s'
logger = logging.getLogger('docker_netscaler')
stdloghandler=logging.StreamHandler()
logformatter=logging.Formatter(fmt=NSTRITON_LOG_FORMAT)
stdloghandler.setFormatter(logformatter)
logger.addHandler(stdloghandler)
logger.setLevel(logging.DEBUG)


# Logging will be done into a file and console
# logger.addHandler(logging.StreamHandler())

try:
    # Setup Triton Libs.
    path = os.path.realpath(__file__)
    path = os.path.dirname(path)
    path = os.path.dirname(path)
    sys.path.append(path)
except Exception:
    logger.error("Not able to set lib path for triton libs."
            "Assuming path is already setup and continuing.")

from marathon.mesos_marathon import MarathonInterface
from kubernetes.kubernetes import KubernetesInterface
from nsappinterface.umsinterface import StylebookInterface
from nsappinterface.umsinterface import AppmanagerInterface
from nsappinterface.netscalerapp import Framework
from netaddr import IPAddress

def docker_swarm(app_info, netskaler):
    from swarm.docker_swarm import DockerSwarmInterface
    parser = argparse.ArgumentParser(description='Process Docker client args')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--swarm-allow-insecure")
    group.add_argument("--swarm-tls-ca-cert")
    parser.add_argument("--swarm-url", required=True, dest='swarm_url')
    parser.add_argument("--swarm-tls-cert", required=False,
                        dest='swarm_tls_cert')
    parser.add_argument("--swarm-tls-key", required=False,
                        dest='swarm_tls_key')

    result = parser.parse_args()

    netskaler.framework = Framework.docker_swarm

    dokker = DockerSwarmInterface(result.swarm_url, result.swarm_tls_ca_cert,
                                  result.swarm_tls_cert, result.swarm_tls_key,
                                  result.swarm_allow_insecure,
                                  app_info, netskaler)
    dokker.configure_all()


def mesos_marathon(app_info, netskaler, conf):
    if 'marathon' in conf:
        url = conf['marathon'].get('url')
        user = conf['marathon'].get('user')
        password = conf['marathon'].get('password')
    else:
        parser = argparse.ArgumentParser(description='Process Marathon args')
        parser.add_argument("--marathon-url", required=True, dest='marathon_url')
        parser.add_argument("--marathon-user",  dest='marathon_user')
        parser.add_argument("--marathon-password",  dest='marathon_password')
        parser.add_argument("--config-interface",  dest='config_interface')
        result = parser.parse_args()
        url = result.marathon_url
        user = result.marathon_user
        password = result.marathon_password
    marathon = MarathonInterface(server=url,
                                 netskaler=netskaler,
                                 app_info=app_info,
                                 username=user,
                                 password=password)

    netskaler.framework = Framework.mesos_marathon
    while True:
        #First Let's see if NS is not ready.
        if netskaler.reinit_netscaler == True:
            netskaler.reinit_netscaler = False
            logger.error("NS is not ready. Sleeping for 30 seconds.")
            time.sleep(30)
            netskaler.clear_ns_config()
            if (netskaler.nstype == 'CPX'):
                netskaler.clean_ip_tables()
            if netskaler.reinit_netscaler == True:
                continue

        if marathon.reinit_marathon == True:
            marathon.reinit_marathon = False
            time.sleep(30)

        marathon.get_all_apps()
        marathon.watch_all_apps()
        # Ooops! something goes wrong. Let's take a rest.
        logger.error("watching the apps has broken.")

def kubernetes(appinfo, netskaler, conf):
    if 'kube' not in conf:
        conf['kube'] = {}
        parser = argparse.ArgumentParser(description='Process Kubernetes args')
        parser.add_argument("--kube-config", required=False,
                            dest='cfg', default=None)
        parser.add_argument("--kube-apiserver", required=False,
                            dest='server', default=None)
        parser.add_argument("--kube-certificate-authority", required=False,
                            dest='ca', default=None)
        parser.add_argument("--kube-client-certificate", required=False,
                            dest='clientcert', default=None)
        parser.add_argument("--kube-client-key", required=False,
                            dest='clientkey', default=None)
        parser.add_argument("--kube-username", required=False,
                            dest='username', default=None)
        parser.add_argument("--kube-password", required=False,
                            dest='password', default=None)
        parser.add_argument("--kube-token", required=False,
                            dest='token', default=None)
        
        result                              = parser.parse_known_args()
        conf['kube']['kubeconfig']          = result[0].cfg
        conf['kube']['apiserver']           = result[0].server
        conf['kube']['certificate-authority'] = result[0].ca
        conf['kube']['client-cert-data']         = result[0].clientcert
        conf['kube']['client-key-data']          = result[0].clientkey
        conf['kube']['username']            = result[0].username
        conf['kube']['password']            = result[0].password
        conf['kube']['token']               = result[0].token

    m_thr_time  = conf['kube'].get('main_thread_timeout')

    # '{"appkey": "com.citrix.lb.appname", "apps": [{"name": "foo"},
    #  {"name": "bar"}]}'
    # Already done in main app_info = json.loads(os.environ['APP_INFO'])

    kube = KubernetesInterface(netskaler=netskaler,
                               app_info=appinfo,
                               kubernetesconf=conf['kube'],
                               main_thread_timeout=m_thr_time)

    netskaler.framework = Framework.kubernetes

    while True:
        # First Let's see if NS is not ready.
        # Block only used in case config interface is netscaler since that is
        # the only place where reinit_netscaler is set to true
        if netskaler.reinit_netscaler == True:
            netskaler.reinit_netscaler = False
            logger.error("NS is not ready. Sleeping for 15 seconds.")
            time.sleep(15)
            netskaler.clear_ns_config()
            if (netskaler.nstype == 'CPX'):
                netskaler.clean_ip_tables()
            if netskaler.reinit_netscaler == True:
                continue

        if kube.reinit_kubernetes == True:
            kube.reinit_kubernetes = False
            logger.error("Problem talking to kubernetes. Sleepping for 15 seconds")
            time.sleep(15)

        # Don't need checks on type of NS device being configured
        # Only support apps list with nitro interface
        if len(kube.app_info['apps']) > 0 and str(netskaler)== "netscalerappinterface":
            kube.configure_ns_for_all_apps()
            if kube.reinit_kubernetes == False and \
               netskaler.reinit_netscaler == False: # Incase initial configuration resulted in failure
                kube.watch_all_apps()
        elif len(kube.app_info['apps']) == 0:
            kube.configure_cpx_for_all_apps()
            if kube.reinit_kubernetes == False and \
               netskaler.reinit_netscaler == False: # Incase initial configuration resulted in failure
                kube.main_thread()
        else:
            logger.error("Combination of config interface %s and selected apps not supported" % type(netskaler))

def readconf_file(conffile):
    try:
        with open(conffile, "r") as conf_handle:
            conf = json.loads(conf_handle.read())
        logger.info("reading triton config from %s.", conffile)
    except IOError:
        return None
    return conf

def re_read_conf_zmq(conf):
    try:
        import zmq
        context = zmq.Context.instance()
        sock = context.socket(zmq.REQ)
        sock.connect("ipc:///tmp/mps/ipc_sockets/mps_config_sock")
        req = {"resourceType": "triton_config", "operation": "get", "triton_config": [{}]}
        req_str = json.dumps(req)
        sock.send(req_str)
        resp = sock.recv()
    except zmq.ZMQError as e:
        logger.error("ZMQ IPC socket failure: I/O error({0}): {1}".format(e.errno, e.strerror))
        sock.close()
        exit()

    if 'config-interface' in conf and conf["config-interface"] == "appmanager-zmq-skip-conf":
        logger.debug("Triton Configuration from UMS:\n" + json.dumps(conf, sort_keys=True, indent=4))
        conf['zmq_sock'] = sock
        conf["config-interface"] = "appmanager-zmq"
        return

    try:
        resp_json = json.loads(resp)
        logger.debug("triton config ==> \n" + json.dumps(resp_json, sort_keys=True, indent=4))
        triton_config = resp_json["triton_config"][0]
        conf['NS_IP'] = "127.0.0.1" # doesn't mean anything.
        conf['FRAMEWORK_HEALTHCHECK'] = triton_config["framework_healthcheck"]
        conf['LOOPBACK_DISABLED'] = triton_config["loopback_disabled"]

        conf['active_framework'] = triton_config.get('active_framework')
        if 'marathon_params' in triton_config:
            if 'marathon' not in conf:
                conf['marathon'] = {}
            if 'marathon_url' in triton_config['marathon_params']:
                conf['marathon']["url"] = triton_config['marathon_params']['marathon_url']
            if 'marathon_username' in triton_config['marathon_params']:
                conf['marathon']["user"] = triton_config['marathon_params'].get('marathon_username')
            if 'marathon_password' in triton_config['marathon_params']:
                conf['marathon']["password"] = triton_config['marathon_params'].get('marathon_password')

        if 'kube_params' in triton_config:
            if 'kube' not in conf:
                conf['kube'] = {}
            if 'kubeconfig' in triton_config['kube_params']:
                conf['kube']['kubeconfig'] = triton_config['kube_params']['kubeconfig'] 
            if 'apiserver' in triton_config['kube_params']:
                conf['kube']['apiserver'] = triton_config['kube_params']['apiserver']
            if 'insecure_skip_verify' in triton_config['kube_params']:
                conf['kube']['insecure_skip_verify'] = (str(triton_config['kube_params']['insecure_skip_verify'])).lower() == 'true'
            if 'ca' in triton_config['kube_params']:
                conf['kube']['certificate-authority'] = triton_config['kube_params']['ca']
            if 'ca_data' in triton_config['kube_params']:
                conf['kube']['certificate-authority-data'] = triton_config['kube_params']['ca_data']
            if 'client_cert' in triton_config['kube_params']:
                conf['kube']['client-cert'] = triton_config['kube_params']['client_cert']
            if 'client_cert_data' in triton_config['kube_params']:
                conf['kube']['client-cert-data'] = triton_config['kube_params']['client_cert_data']
            if 'client_key' in triton_config['kube_params']:
                conf['kube']['client-key'] = triton_config['kube_params']['client_key']
            if 'client_key_data' in triton_config['kube_params']:
                conf['kube']['client-key-data'] = triton_config['kube_params']['client_key_data']
            if 'username' in triton_config['kube_params']:
                conf['kube']['username'] = triton_config['kube_params']['username']
            if 'password' in triton_config['kube_params']:
                conf['kube']['password'] = triton_config['kube_params']['password']
            if 'token' in triton_config['kube_params']:
                conf['kube']['token'] = triton_config['kube_params']['token']

        if 'networking_params' in triton_config:
            if 'networking' not in conf:
                conf["networking"] = {}
            if 'networking_type' in triton_config['networking_params']:
                if triton_config['networking_params']['networking_type'].upper() == 'NUAGE':
                    conf['networking']['type'] = 'Nuage'
                    conf['networking']['nuage'] = {}
                    networking_params = triton_config['networking_params']
                    if "networking_username" in networking_params:
                        conf['networking']['nuage']["user"] = networking_params['networking_username']
                    if "networking_password" in networking_params:
                        conf['networking']['nuage']["password"] = networking_params['networking_password']
                    if 'networking_enterprise' in networking_params:
                        conf['networking']['nuage']['enterprise'] = networking_params['networking_enterprise']
                    if "networking_api_url" in networking_params:
                        conf['networking']['nuage']["api-url"] = networking_params['networking_api_url']
                    if "networking_vip_subnet" in networking_params:
                        conf['networking']['nuage']["vip-subnet"] = networking_params['networking_vip_subnet']

        if 'dns_params' in triton_config:
            if 'dns' not in conf:
                conf['dns'] = {}
            if 'dns_type' in triton_config['dns_params']:
                if triton_config['dns_params']['dns_type'].upper() == 'INFOBLOX':
                    conf["dns"]["type"] = 'Infoblox'
                    conf["dns"]["infoblox"] = {}
                    dns_params = triton_config['dns_params']
                    if 'dns_username' in dns_params:
                        conf["dns"]["infoblox"]["user"] = dns_params['dns_username']
                    if 'dns_password' in dns_params:
                        conf["dns"]["infoblox"]["password"] = dns_params['dns_password']
                    if 'dns_api_url' in dns_params:
                        conf["dns"]["infoblox"]["api-url"] = dns_params['dns_api_url']
                    if 'dns_suffix' in dns_params:
                        conf["dns"]["dns-suffix"] = dns_params['dns_suffix']

        logger.debug("Triton Configuration from UMS:\n" + json.dumps(conf, sort_keys=True, indent=4))
        conf['zmq_sock'] = sock

    except Exception as e:
        logger.error("ZMQ IPC socket parsing failure: %s" % e)
        exit(201)

def create_networking_interface(conf):
    networking = None
    if "networking" in conf:
        if "type" in conf["networking"]:
            networking=conf["networking"]["type"].lower()

    if networking == "nuage":
        if "nuage" in conf["networking"]:
            nuage_conf=conf["networking"][networking]
        else:
            nuage_conf={}

        from environment.nuageinterface import NuageInterface
        username=nuage_conf.get("username", "csproot")
        password=nuage_conf.get("password", "csproot")
        enterprise=nuage_conf.get("enterprise", "csp")
        api_url=nuage_conf.get("api-url", "https://10.217.212.235:8443")
        vip_subnet=nuage_conf.get("vip-subnet", "Citrix:myDomainInstance:MyZone:VIPSubnet")
        network=NuageInterface(username, password, enterprise, api_url, vip_subnet)
        return network
    return None

def main():

    # '{"appkey": "com.citrix.lb.appname", "apps": [{"name": "foo"},
    #  {"name": "bar"}]}'
    conffile_list = ["triton.conf", "/mpsconfig/triton.conf"]
    for conffile in conffile_list:
        conf = readconf_file(conffile)
        if conf:
            break

    if not conf:
        logger.info("no triton config used.")
        conf = {}

    parser = argparse.ArgumentParser()
    parser.add_argument("--config-interface",
                        help="Netscaler Interface to configure applications."
                        "Possible values \'netscaler/stylebook/appmanager/appmanager-zmq/iptablesmanager\'.",
                        default='netscaler')
    parser.add_argument("--loglevel",
                        help="Logging level (critical/error/warning/info/debug).",
                        default='debug')
    parser.add_argument("--logfilename",
                        help="Log file name; stdout, if not specified",
                        default=None)
    parser.add_argument("--logbytes",
                        help="Number of bytes before log file rotates, default 2M",
                        default='2000000')
    parser.add_argument("--logrotatenumber",
                        help="Number of log files in rotaton, deafult 20",
                        default='20')

    result = parser.parse_known_args()

    # Logs made before this, such as in reading config files may be missed from log file
    # Logging functionality
    # If logfilename is given in the coomand line, logs are written
    # into the rotating files and no the to the stdout/stderr with the base name given by the logfilename option
    # if logfilename option is not given, log goes to stdout/stderr

    if 'loglevel' in conf:
        loglevel = conf['loglevel'].lower()
    else:
        loglevel = result[0].loglevel.lower()

    if loglevel == 'critical':
        logger.setLevel(logging.CRITICAL)
    elif loglevel == 'error':
        logger.setLevel(logging.ERROR)
    elif loglevel == 'warning':
        logger.setLevel(logging.WARNING)
    elif loglevel == 'info':
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.DEBUG)

    if 'logfilename' in conf:
        logfilename=conf['logfilename']
    else:
        logfilename=result[0].logfilename

    if logfilename:
        if 'logbytes' in conf:
            logbytes=int(conf['logbytes'])
        else:
            logbytes=int(result[0].logbytes)

        if 'logrotatenumber' in conf:
            logrotatenumber=int(conf['logrotatenumber'])
        else:
            logrotatenumber=int(result[0].logrotatenumber)

        # Configuring Rotating files handler
        handler = RotatingFileHandler(logfilename, maxBytes=logbytes, backupCount=logrotatenumber)
        handler.setFormatter(logformatter)
        logger.addHandler(handler)
        # Removing standard output log handlers as rotating files are configured
        # This avoids eating file system space by CPX container by log messages
        logger.removeHandler(stdloghandler)
    
    logger.info("==============> Starting Triton <================")

    if 'config-interface' in conf:
        configinterface = conf['config-interface']
    else:
        configinterface = result[0].config_interface

    if configinterface == "appmanager-zmq" or configinterface == "appmanager-zmq-skip-conf":
        re_read_conf_zmq(conf)
        configinterface = "appmanager-zmq"
    
    if 'marathon' not in conf and 'kube' not in conf and 'swarm' not in conf:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--swarm-url", dest='swarm_url')
        group.add_argument("--marathon-url", dest='marathon_url')
        group.add_argument("--kube-apiserver", dest='kube_server')
        group.add_argument("--kube-config", dest='kube_config')

        result = parser.parse_known_args()
        swarm = result[0].swarm_url
        marathon = result[0].marathon_url
        kube_server = result[0].kube_server
        kube_config = result[0].kube_config
        kube = None
    else:
        swarm = conf.get('swarm')
        marathon = conf.get('marathon')
        kube = conf.get('kube')
        kube_server = None
        kube_config = None

    active_framework=conf.get('active_framework')
    if active_framework==None:
        if swarm:
            active_framework="swarm"
        if marathon:
            active_framework="marathon"
        if kube or kube_server or kube_config:
            active_framework="kube"

    if 'APP_INFO' in conf:
        app_info = conf['APP_INFO']
    elif 'APP_INFO' in os.environ:
        app_info = json.loads(os.environ['APP_INFO'])
    else:
        app_info={'apps':[]}

    if 'NS_IP' in conf:
        ns_ip = conf['NS_IP']
    else:
        ns_ip = os.environ.get('NS_IP')
    if ns_ip == None:
        logger.error("NS_IP environment variable is not given")
        exit(2)

    if 'NS_TYPE' in conf:
        ns_type = conf['NS_TYPE']
    else:
        ns_type = os.environ.get("NS_TYPE", "VPX")
    ns_type = ns_type.upper()
    if ns_type != 'CPX' and ns_type != 'VPX' and ns_type != 'MPX':
        logger.error('Unrecognized NS Type %s' %ns_type)
        exit(3)

    if 'NS_NETMODE' in conf:
        ns_netmode = conf['NS_NETMODE']
    else:
        ns_netmode = os.environ.get("NS_NETMODE", 'BRIDGE')
    ns_netmode = ns_netmode.upper()
    if ns_netmode != 'HOST' and ns_netmode != 'BRIDGE' and ns_netmode != 'NONE':
        logger.error('Unrecognized NS Type %s' %ns_netmode)
        exit(4)

    if 'FRAMEWORK_HEALTHCHECK' in conf:
        framework_healthcheck = conf['FRAMEWORK_HEALTHCHECK']
    else:
        framework_healthcheck = os.environ.get('FRAMEWORK_HEALTHCHECK')
    if type(framework_healthcheck) != bool:
        if framework_healthcheck == None:
            framework_healthcheck = True
        elif framework_healthcheck.lower() == 'true':
            framework_healthcheck = True
        elif framework_healthcheck.lower() == 'false':
            framework_healthcheck = False
        else:
            logger.error('Unrecognized framework_healthcheck option ' % framework_healthcheck)
            exit(6)

    if 'LOOPBACKDISABLED' in conf:
        loopbackdisabled = conf['LOOPBACKDISABLED']
    else:
        loopbackdisabled = os.environ.get("LOOPBACKDISABLED", False)
    loopbackdisabled = bool(loopbackdisabled)

    ns_vip = None
    if 'NS_VIP' in conf:
        ns_vip = conf['NS_VIP']
    else:
        ns_vip = os.environ.get("NS_VIP")
    if not ns_vip and ns_type == 'CPX':
        if ns_netmode == 'HOST':
            ns_vip = str(IPAddress(ns_ip) + 1)
            if ns_vip == os.environ.get("NS_GATEWAY"):
                ns_vip = str(IPAddress(ns_vip) + 1)
        else:
            ns_vip = ns_ip
    if not ns_vip:
        ns_vip="$NS-VIP$"

    if (os.environ.get("NS_GATEWAY") == ns_vip):
        logger.error("NETSCALER VIP and NETSCALER Gateway can't be the equal")
        exit(7)
    
    if 'NS_MGMT_SERVER' in conf:
        ns_mgmt_server=conf['NS_MGMT_SERVER']
    else:
        ns_mgmt_server = os.environ.get('NS_MGMT_SERVER', None)

    if configinterface=='iptablesmanager':
        if not ns_mgmt_server or ns_mgmt_server=='':
            logger.error('NS Management server IP is required for iptablesmanager interface')
            exit(8)

    '''
    parser.add_argument("--group",
                        help="Only generate config for apps which list the "
                        "specified names. Defaults to apps without groups. "
                        "Use '*' to match all groups",
                        action="append",
                        default=list())
    groups = result[0].group
    '''

    if 'NETSCALER_GROUP' in conf:
        groupenv = conf['NETSCALER_GROUP']
    else:
        groupenv = os.environ.get('NETSCALER_GROUP')
    if groupenv == None:
        groups = []
    else:
        groups = groupenv.split(',')
    logger.info('Groups to be Configured are --> %s', str(groups))

    if 'NS_USER' in conf:
        ns_user = conf['NS_USER']
    else:
        ns_user = os.environ.get("NS_USER", "nsroot")
    if 'NS_PASSWORD' in conf:
        ns_password = conf['NS_PASSWORD']
    else:
        ns_password = os.environ.get("NS_PASSWORD", "nsroot")

    dns=None
    dns_instance=None

    network = create_networking_interface(conf)

    if "dns" in conf:
        dns_suffix=conf["dns"].get("dns-suffix", "")
        if "type" in conf["dns"]:
            dns=conf["dns"]["type"].lower()

        if dns == "infoblox":
            if "infoblox" in conf["dns"]:
                dns_conf=conf["dns"][dns]
            else:
                dns_conf={}

            username=dns_conf.get("username", "admin")
            password=dns_conf.get("password", "infoblox")
            api_url=dns_conf.get("api-url", "https://10.217.212.227")
            from environment.infobloxinterface import InfobloxInterface
            dns_instance=InfobloxInterface(username, password, api_url, dns_suffix)

    if configinterface == 'netscaler':
        from nsappinterface.nitrointerface import NetscalerInterface
        logger.info('Let\'s give some time to NSPPE to be ready.')
        time.sleep(5)
        netskaler = NetscalerInterface(ns_ip,
                ns_user,
                ns_password,
                app_info,
                groups,
                ns_type,
                ns_netmode,
                ns_vip,
                loopbackdisabled,
                framework_healthcheck,
                False, os.environ.get("NS_CONFIG_FRONT_END"))

    elif configinterface == 'stylebook':
        time.sleep(30)
        netskaler = StylebookInterface(ns_ip,
                ns_user,
                ns_password,
                app_info,
                groups,
                ns_type,
                ns_netmode,
                ns_vip,
                loopbackdisabled,
                framework_healthcheck,
                False, networking=network, dns=dns_instance)

    elif configinterface == 'appmanager' or configinterface == 'appmanager-zmq':
        logger.info('Let\'s give some time for NMAS to be ready.')
        time.sleep(30)
        netskaler = AppmanagerInterface(ns_ip,
                ns_user,
                ns_password,
                app_info,
                groups,
                ns_type,
                ns_netmode,
                ns_vip,
                loopbackdisabled,
                framework_healthcheck,
                False, networking=network, dns=dns_instance, zmq_sock=conf.get("zmq_sock"))
    elif configinterface == 'iptablesmanager':
        from nsappinterface.iptablesinterface import IPTablesInterface
        netskaler = IPTablesInterface(ns_mgmt_server,
                ns_user,
                ns_password,
                app_info,
                groups,
                ns_type,
                ns_netmode,
                ns_vip,
                loopbackdisabled,
                framework_healthcheck,
                False)
    else:
        logger.error("Invalid configinterface %s. Possible values are 'netscaler/stylebook/appmanager/iptablesmanager" % configinterface)
        exit(10)

    def signal_sigint_handler(signum, frame):
        if conf.get("zmq_sock"):
            conf.get("zmq_sock").close()
        logger.critical("\nReceived SIGINT. Exiting...Good Bye!\n")
        exit(5)

    signal.signal(signal.SIGINT, signal_sigint_handler)

    if active_framework=="swarm" and swarm:
        docker_swarm(app_info, netskaler)
    elif active_framework=="marathon" and marathon:
        mesos_marathon(app_info, netskaler, conf)
    elif active_framework=="kube" and (kube or kube_server or kube_config):
        kubernetes(app_info, netskaler, conf)
    else: print 'No orchestrator is requested'

if __name__ == "__main__":
    main()
