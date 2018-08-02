#!/usr/bin/env python
import logging
from netaddr import IPAddress
import socket
import random

logger = logging.getLogger('docker_netscaler')

class Framework:
    mesos_marathon = 'MESOS_MARATHON'
    kubernetes = 'KUBERNETES'
    docker_swarm = 'DOCKER-SWARM'

class NetscalerAppServicetype:
    lbpoolservicetype = {'ssl': {'edge': 'http', 'reencrypt': 'http'}, \
                        'ssl_tcp':{'edge': 'tcp', 'reencrypt': 'tcp'}}
    servicegroupservicetype = {'ssl': {'edge': 'http', 'reencrypt': 'ssl'}, \
                        'ssl_tcp':{'edge': 'tcp', 'reencrypt': 'ssl_tcp'}}
                        
class NetscalerSuperApp(object):
    def __init__(self):
        self.certkey = None
        self.certkey_sni = {}
        self.cert_ca = {}
        self.responder_policies = {}
        self.ssl_policies = {}
        self.ssl_termination = None

    def assign_application_vip_vport(self, netskaler):
        if netskaler.nstype == "CPX":
            self.vip = netskaler.nsvip
            if self.framework == Framework.mesos_marathon:
                self.vport = self.nodePort
            elif self.framework == Framework.kubernetes:
                if len(netskaler.nsvip_ports)>1:
                    self.vport = netskaler.nsvip_ports.pop()
                else:
                    logger.critical("No more free ports remaining for the %s" %self.vip)
                    return False
        if netskaler.nstype == "VPX":
            if not netskaler.frozen_frontend_from_app_info:
                self.vip = netskaler.nsvip
            if self.framework == Framework.mesos_marathon:
                self.vport = self.nodePort
            if self.framework == Framework.kubernetes:
                self.vport = netskaler.nsvip_ports.pop()
                #self.vport = self.servicePort
        return True
    
    def occupy_application_vport(self, netskaler):
        try:
            netskaler.nsvip_ports.remove(self.vport)
        except ValueError as e:
            pass 
    
    def assign_application_vport(self, netskaler):
        if netskaler.nstype == "CPX":
            if self.framework == Framework.mesos_marathon:
                self.vport = self.nodePort
            elif self.framework == Framework.kubernetes:
                """ self.vip should be properly assigned prior calling this function """
                if len(netskaler.nsvip_ports)>1:
                    self.vport = netskaler.nsvip_ports.pop()
                else:
                    logger.critical("No more free ports remaining for the %s" %self.vip)
                    return False
        if netskaler.nstype == "VPX":
            if self.framework == Framework.mesos_marathon:
                self.vport = self.nodePort
        return True

class NetscalerCsApp(NetscalerSuperApp):
    ''' defines the interface for NS frontend vip'''
    def __init__(self, appName, framework, lb_role):
        super(NetscalerCsApp, self).__init__()
        self.appName = appName
        self.serviceIP = 'localhost'
        self.servicePort = None
        self.nodePort = None    # Port for external access to the app used by Kubernetes
        self.mode = 'tcp'
        self.vip = '*'
        self.vport = '*'
        self.labels = {}
        self.sslCert = None
        self.framework = framework
        self.healthmonitor = 'YES'
        self.stylebook = "com.citrix.adc.stylebooks/1.0/cs-lb-mon"
        self.stylebook_additional_params = {}
        self.target_device=False
        self.config_session = None
        self.network_env = None
        self.networking_app_data={}
        self.dns_domain = "n/a"
        self.policies = {}
        self.lb_role = lb_role


class NetscalerApp(NetscalerSuperApp):
    ''' defines interface to the LB part of the application - service group'''
    def __init__(self, appName, framework):
        super(NetscalerApp, self).__init__()
        self.appName = appName
        self.serviceIP = 'localhost'
        self.servicePort = None
        self.nodePort = None    # Port for external access to the app used by Kubernetes
        self.mode = 'tcp'
        self.backends = set()
        self.backends_count=0
        self.persistence = "None"
        self.vip = '0.0.0.0'
        self.vport = '0'
        self.labels = {}
        self.sslCert = None
        self.lbmethod = "ROUNDROBIN"
        self.framework = framework
        self.healthmonitor = 'YES'
        self.stylebook = "com.citrix.adc.stylebooks/1.1/Marathon-HTTP-LB-MON"
        self.stylebook_additional_params = {}
        self.target_device=False
        self.config_session = None
        self.network_env = None
        self.networking_app_data={}
        self.dns_domain = "n/a"
        self.nsapps=[]   # List of high level apps binding this lb app. For Faster access
    
    def add_backend_member(self, host, port):
        self.backends.add(NetscalerSGMemebers(host, port))
        self.backends_count += 1
        
    def delete_backend_member(self, host, port):
        for ep in self.backends:
            if ep.host == host and ep.port == port:
                self.backends.remove(ep)
                self.backends_count -= 1
                break

    def clear_backends(self):
        self.backends_count = 0
        self.backends.clear()

class NetscalerSGMemebers(object):
    ''' defines the NS servicegroup members '''
    def __init__(self, host, port):
        self.host = host
        self.port = port

    def __hash__(self):
        return hash((self.host, self.port))

    def __repr__(self):
        return "NetscalerServiceGroupMember(%r, %r)" % (self.host, self.port)

class NetscalerAppInterface(object):
    certs_directory = None
    keys_directory = None
    def __init__(self, app_info,
            nsgroup, nstype, 
            nsnetmode, nsvip,
            loopbackdisabled, framework_healthcheck, networking=None, dns=None):
        self.nsvip = nsvip
        self.app_info = app_info
        self.groups = nsgroup
        self.nstype = nstype
        self.nsnetmode = nsnetmode
        self.framework = None
        self.reinit_netscaler = False
        # Purpose of holding apps is to be able to clear iptable Rules
        # installed for CPX.
        self.nsapps = {}
        self.nslbapps = {}
        self.loopbackdisabled = loopbackdisabled
        self.framework_healthcheck = framework_healthcheck
        self.nsvip_ports=range(20000,30000)
        random.shuffle(self.nsvip_ports)
        self.frozen_frontend_from_app_info = False
        self.networking=networking
        self.dns=dns
        self.nsvips={}
        self.devices = {}
        self.ip_cache = {}
        self.waiting_apps = set()
        
        
        """
        app_info expected structure:
        '{"appkey": "com.citrix.lb.appname",
          "apps": [{"name": "foo0", "lb_ip":"10.220.73.122", "lb_port":"443"},
                   {"name": "foo1", "lb_ip":"10.220.73.123", "lb_port":"80"},
                   {"name":"foo2"}, {"name":"foo3"}]}'
        """
    
    def __str__(self):
        return "netscalerappinterface"
    
    def is_group_needed(self, groups, app_groups):
        # All groups / wildcard match
        if '*' in groups:
            return True

        # empty group only
        if len(groups) == 0 and len(app_groups) == 0:
            raise Exception("No groups specified")

        # Contains matching groups
        if (len(frozenset(app_groups) & set(groups))):
            return True

        return False

    def sync_ns_apps(self):
        pass

    def sync_ns_cs_apps(self):
        pass

    def sync_ns_devices(self):
        return set()
    
    def delete_ns_as_task(self, taskid):
        pass

    def delete_ns_as_app(self, app):
        pass

    def configure_iptables_for_app(self):
        pass

    def _nsapp_get_vips(self,nsapp):
        vips_label=self.networking.networking_create_vips_label(nsapp)
        if vips_label in self.nsvips:
            vips=self.nsvips[vips_label]
        else:
            vips=self.networking.networking_get_vips(nsapp)
            logger.debug("Allocated vips for the labe %s" %vips_label)
            
        if not vips:
            logger.error("Failed to allocate vip for the label %s" %vips_label)
        else:
            if vips_label not in self.nsvips:
                vips["used"]=[]
                self.nsvips[vips_label]=vips
            logger.debug(self.nsvips[vips_label])

        return vips
    
    def nsapp_mark_vip_used(self, nsapp):
        if self.networking == None:
            # If no networking provided by orchestration
            return nsapp.vip
        vips=self._nsapp_get_vips(nsapp)
        
        if not vips or vips["numips"]==0:
            logger.info("No Vips avaailable for the app %s", nsapp.appName)
            return
        
        if nsapp.vip in vips["used"]:
            return
        
        vips["used"].append(nsapp.vip)
        vips["numips"]-=1
        logger.debug("Reserved vip %s for app %s" %(nsapp.appName, nsapp.vip))
        logger.debug(self.nsvips)
        
    def nsapp_allocate_vip(self, nsapp):
        if self.networking == None:
            # If no networking provided by orchestration
            return nsapp.vip
        
        vips=self._nsapp_get_vips(nsapp)
        
        if not vips or vips["numips"]==0:
            logger.info("No Vips avaailable for the app %s", nsapp.appName)
            return nsapp.vip
            
        while vips["numips"] > 0:
            vip=vips["ipfrom"]
            vips["ipfrom"]=str(IPAddress(vips["ipfrom"])+1)
            vips["numips"]-=1

            if vip not in vips["exclude"] and vip not in vips["used"]:
                break
            else:
                vip=None

        if vip == None:
            return nsapp.vip
        vips["used"].append(vip)
        logger.debug("Allocated vip %s for app %s" %(nsapp.appName, vip))
        logger.debug(self.nsvips)
        return vip

    def nsapp_release_vip(self, nsapp):
        if self.networking == None:
            return

        #self.networking.networking_release_vip(nsapp)
        vips_label=self.networking.networking_create_vips_label(nsapp)

        if vips_label not in self.nsvips:
            return

        vips = self.nsvips[vips_label]
        if vips["numips"] == 0 or IPAddress(nsapp.vip)<IPAddress(vips["ipfrom"]):
            vips["ipfrom"]=nsapp.vip
        vips["numips"]+=1
        vips["used"].remove(nsapp.vip)
        logger.debug("Released vip %s for app %s" %(nsapp.vip, nsapp.appName))
        logger.debug(self.nsvips)

    def resolve_ip(self, host):
        cached_ip = self.ip_cache.get(host, None)
        if cached_ip:
            return cached_ip
        else:
            try:
                logger.debug("trying to resolve ip address for host %s", host)
                ip = socket.gethostbyname(host)
                self.ip_cache[host] = ip
                logger.debug("resolved ip for host %s", ip)
                return ip
            except socket.gaierror:
                logger.error("couldn't resolve ip for host %s. Task would not be added.", host)
                return None
