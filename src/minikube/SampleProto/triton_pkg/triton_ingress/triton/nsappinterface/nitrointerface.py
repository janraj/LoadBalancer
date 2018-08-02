#!/usr/bin/env python
import os
from functools import wraps
import logging
import base64
from triton.nsappinterface.netscalerapp import NetscalerAppInterface
from triton.nsappinterface.netscalerapp import NetscalerApp
from triton.nsappinterface.netscalerapp import Framework
from nssrc.com.citrix.netscaler.nitro.resource.config.ns.nsfeature import nsfeature
from nssrc.com.citrix.netscaler.nitro.resource.config.ns.nsip import nsip
from nssrc.com.citrix.netscaler.nitro.resource.config.ssl.sslcertkey import sslcertkey
from nssrc.com.citrix.netscaler.nitro.resource.config.system.systemfile import systemfile 
from nssrc.com.citrix.netscaler.nitro.resource.config.ssl.sslvserver import sslvserver
from nssrc.com.citrix.netscaler.nitro.resource.config.ssl.sslvserver_sslcertkey_binding import sslvserver_sslcertkey_binding
from nssrc.com.citrix.netscaler.nitro.exception.nitro_exception \
    import nitro_exception
from nssrc.com.citrix.netscaler.nitro.resource.config.lb.lbvserver \
    import lbvserver
from nssrc.com.citrix.netscaler.nitro.service.nitro_service\
    import nitro_service
from nssrc.com.citrix.netscaler.nitro.resource.config.basic.servicegroup\
    import servicegroup
from nssrc.com.citrix.netscaler.nitro.resource.config.lb.lbvserver_servicegroup_binding\
    import lbvserver_servicegroup_binding
from nssrc.com.citrix.netscaler.nitro.resource.config.basic.servicegroup_servicegroupmember_binding\
    import servicegroup_servicegroupmember_binding
from nssrc.com.citrix.netscaler.nitro.resource.config.cs.csvserver \
    import csvserver
from nssrc.com.citrix.netscaler.nitro.resource.config.cs.csvserver_lbvserver_binding \
    import csvserver_lbvserver_binding
from nssrc.com.citrix.netscaler.nitro.resource.config.cs.csaction \
    import csaction
from nssrc.com.citrix.netscaler.nitro.resource.config.cs.cspolicy \
    import cspolicy
from nssrc.com.citrix.netscaler.nitro.resource.config.cs.csvserver_cspolicy_binding \
    import csvserver_cspolicy_binding

from nssrc.com.citrix.netscaler.nitro.resource.config.ns.nsconfig \
    import nsconfig

logger = logging.getLogger('docker_netscaler')

try:
    import triton.nsappinterface.iptables
except:
    logger.warning("Ignoring IPtables Module (Only used in CPX context.)")


def ns_session_scope(func):
    @wraps(func)
    def login_logout(self, *args, **kwargs):
        if not self.ns_session:
            ip_port=self.nsip+':'+self.nsport
            self.ns_session = nitro_service(ip_port, self.ns_protocol)
            self.ns_session.certvalidation = False 
            self.ns_session.hostnameverification = False
            self.ns_session.set_credential(self.nslogin, self.nspasswd)
            self.ns_session.timeout = 600
        try :
            self.ns_session.login()
        except nitro_exception as e :
            logger.error("Nitro Exception::login::errorcode="+str(e.errorcode)+",message="+ e.message)
            self.reinit_netscaler = True
            return login_logout
        except Exception as e:
            logger.error("Exception: %s" % e.message)
            self.reinit_netscaler = True
            return login_logout
        result = func(self, *args, **kwargs)
        try :
            self.ns_session.logout()
        except nitro_exception as e :
            logger.warn("Nitro Exception::logout::errorcode="+str(e.errorcode)+",message="+ e.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
        self.ns_session = None
        return result
    return login_logout


class NetscalerInterface(NetscalerAppInterface):
    def __init__(self, nsip, nslogin, nspasswd, app_info, nsgroup, nstype,
                 nsnetmode, nsvip, loopbackdisabled, framework_healthcheck, is_controller, configure_frontends=False):
        ''' Initialize variable in base class. '''
        super(NetscalerInterface, self).__init__(app_info,
                nsgroup,
                nstype,
                nsnetmode,
                nsvip,
                loopbackdisabled,
                framework_healthcheck
                )

        self.nsip=nsip
        self.ns_protocol = os.environ.get("NS_PROTOCOL")
        if not self.ns_protocol:
          self.ns_protocol='http'
        else:
          self.ns_protocol = self.ns_protocol.lower()
        self.nsport=os.environ.get("NS_PORT")
        if not self.nsport:
          if self.ns_protocol=='https':
            self.nsport='443'
          else:
            self.nsport='80'
        
        self.nslogin = nslogin
        self.nspasswd = nspasswd
        self.ns_session = None
        self.force = True
        self.level = 'basic'
        self.certs_directory='/var/mps/tenants/root/ns_ssl_certs/'
        self.keys_directory='/var/mps/tenants/root/ns_ssl_certs/'
        if not os.path.exists(self.certs_directory):
           os.makedirs(self.certs_directory)
        if not os.path.exists(self.keys_directory):
           os.makedirs(self.keys_directory)
        self.configure_frontends = configure_frontends;
        if self.configure_frontends and self.nsvip == '$NS-VIP$':
            self.nsvip = self.get_nsip()
        logger.info("Netscaler ip address %s, NS vip = %s NS Type = %s, Netmode = %s User %s Password %s" \
                %(self.nsip, self.nsvip, self.nstype, self.nsnetmode, self.nslogin, self.nspasswd))

        """ Clearing config with basic method to start fresh """
        # Should clean NS config for CPX as well. So making it unconditional.
        # Iptables cleanup is conditional
        '''
        # For canary deployment support
        self.weighted_pods={}
        '''
        self.pod_uid2ip={} 
        #TODO: sync ns config instead of clearing it
        self.clear_ns_config()
        self.ns_feature_enabled = False
        self.enable_ns_features()
        if self.nstype == 'CPX':
            import triton.nsappinterface.iptables
            self.clean_ip_tables()
        #else:
        #    self.clear_ns_config()
        """
        app_info expected structure:
        '{"appkey": "com.citrix.lb.appname",
          "apps": [{"name": "foo0", "lb_ip":"10.220.73.122", "lb_port":"443"},
                   {"name": "foo1", "lb_ip":"10.220.73.123", "lb_port":"80"},
                   {"name":"foo2"}, {"name":"foo3"}]}'
        """
        if configure_frontends and app_info:
            frontends = [(l['name'], l['lb_ip'], l['lb_port'])
                         for l in self.app_info['apps']
                         if l.get('lb_ip') and l.get('lb_port')]
            for f in frontends:
                self.configure_lb_frontend(f[0], f[1], f[2], 'HTTP')
        self.frozen_frontend_from_app_info = configure_frontends
    
    def configure_ns_cs_app(self, nsapp):
        try:
              self.configure_nsapp(nsapp)
        except:
           logger.error("Nitro exception %s" %e.message)

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

    def _create_service_group(self, grpname, servicetype):
        try:
            svc_grp = servicegroup.get(self.ns_session, grpname)
            if (svc_grp.servicegroupname == grpname):
                logger.info("Service group %s already configured " % grpname)
                return
        except nitro_exception as e:
            pass

        try:
            svc_grp = servicegroup()
            svc_grp.servicegroupname = grpname
            svc_grp.servicetype = servicetype
            servicegroup.add(self.ns_session, svc_grp)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _create_lb(self, lbname, lbmethod, vip, port, servicetype):
        try:
            lb = lbvserver.get(self.ns_session, lbname)
            if (lb.name == lbname) and \
                    (lb.ipv46 == vip) and \
                    (str(lb.port) == port) and \
                    (lb.servicetype != servicetype):
                logger.info("LB %s is already configured with different servicetype" % lbname)
                return
            else:
                logger.info("LB %s is already configured with a different \
                            VIP/port : %s:%s\n" % (lb.name, lb.ipv46, lb.port))
                raise Exception("LB %s already configured with different VIP/\
                                port : %s:%s\n" % (lbname, lb.ipv46, lb.port))
        except nitro_exception as e:
            pass
        try:
            lb = lbvserver()
            lb.name = lbname
            lb.ipv46 = vip
            lb.servicetype = servicetype
            lb.port = port
            lb.lbmethod = lbmethod
            lbvserver.add(self.ns_session, lb)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _create_nsapp_vserver(self, nsapp, override_service_type):
        try:
            lb = lbvserver.get(self.ns_session, nsapp.appName)
            if (lb.name == nsapp.appName) and \
                    (lb.ipv46 == nsapp.vip) and \
                    (str(lb.port) == nsapp.vport):
                # logger.info("LB %s is already configured " % lb.name)
                return
            else:
                logger.info("LB %s is already configured with a different \
                            VIP/port : %s:%s\n" % (lb.name, lb.ipv46, lb.port))
                raise Exception("LB %s already configured with different VIP/\
                                port : %s:%s\n" % (lb.name, lb.ipv46, lb.port))
        except nitro_exception as e:
            pass

        try:
            lb = lbvserver()
            lb.name = nsapp.appName
            lb.ipv46 = nsapp.vip
            if override_service_type == None:
                lb.servicetype = nsapp.mode
            else:
                lb.servicetype = override_service_type
            lb.port = str(nsapp.vport)
            lb.lbmethod = nsapp.lbmethod
            lb.persistencetype = nsapp.persistence
            lbvserver.add(self.ns_session, lb)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _delete_nsapp_vserver(self, nsapp):
        try:
            lbvserver.delete(self.ns_session, nsapp.appName)
        except nitro_exception as e:
            pass

    def _create_nsapp_service_group(self, nsapp, override_service_type):
        try:
            svc_grp = servicegroup.get(self.ns_session, nsapp.appName)
            if (svc_grp.servicegroupname == nsapp.appName):
                #logger.info("Service group %s already configured " % nsapp.appName)
                return
        except nitro_exception as e:
            pass
        try:
            svc_grp = servicegroup()
            svc_grp.servicegroupname = nsapp.appName
            if override_service_type == None:
                svc_grp.servicetype = nsapp.mode
            else:
                svc_grp.servicetype = override_service_type 
            svc_grp.healthmonitor = nsapp.healthmonitor
            servicegroup.add(self.ns_session, svc_grp)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _delete_nsapp_service_group(self, nsapp):
        try:
            servicegroup.delete(self.ns_session, nsapp.appName)
        except nitro_exception as e:
            pass

    def _add_service(self, grpname, srvr_ip, srvr_port):
        try:
            bindings = servicegroup_servicegroupmember_binding.get(
                self.ns_session, grpname)
            for binding in bindings:
                if binding.ip == srvr_ip and str(binding.port) == srvr_port:
                    #logger.info("Service %s:%s is already bound to service \
                    #            group %s " % (srvr_ip, srvr_port, grpname))
                    return

        except nitro_exception as e:
            pass
        
        try:
            binding = servicegroup_servicegroupmember_binding()
            binding.servicegroupname = grpname
            binding.ip = srvr_ip
            binding.port = srvr_port
            servicegroup_servicegroupmember_binding.add(self.ns_session, binding)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _bind_service_group_lb(self, lbname, grpname):
        try:
            bindings = lbvserver_servicegroup_binding.get(self.ns_session,
                                                          lbname)
            for b in bindings:
                if b.name == lbname and b.servicegroupname == grpname:
                    #logger.info("LB %s is already bound to service group %s"
                    #            % (lbname, grpname))
                    return
        except nitro_exception as e:
            pass

        try:
            binding = lbvserver_servicegroup_binding()
            binding.name = lbname
            binding.servicegroupname = grpname
            lbvserver_servicegroup_binding.add(self.ns_session, binding)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _delete_lb(self, lbname):
        try:
            lb = lbvserver()
            lb.name = lbname
            lbvserver.delete(self.ns_session, lb)
        except nitro_exception as ne:
            logger.warn("Nitro Exception: %s" % ne.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)

    def _delete_servicegroup(self, grpname):
        try:
            svc_grp = servicegroup()
            svc_grp.servicegroupname = grpname
            servicegroup.delete(self.ns_session, svc_grp)
        except nitro_exception as ne:
            logger.warn("Nitro Exception: %s" % ne.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
    '''
    def _set_weights_to_services(self, grpname, srvrs):
        try:
            bindings = servicegroup_servicegroupmember_binding.get(
                self.ns_session, grpname)
            existing = [(b.ip, b.port) for b in bindings if b.port != 0]
            weights = [(b.ip, b.port) for b in bindings if b.weight != 1]
        except nitro_exception as e:
            return # no bindings
        sum_weight = 0
        weighted_pods_count = 0
        total_pods_count = 0
        for s in existing:
           total_pods_count = total_pods_count + 1
           if str(s[0]) in self.weighted_pods:
             weight = self.weighted_pods[str(s[0])]
             sum_weight = sum_weight + int(weight)
             weighted_pods_count = weighted_pods_count + 1
        if sum_weight > 100:
              raise Exception("Sum %s is beyond 100. Please check yaml for pods" %(sum_weight)) 
        if sum_weight is 0 and not weights:
              logger.info("#####################No pod is weighted")
              return

        variable_weight = 100-sum_weight
        variable_weight_pods_count = total_pods_count - weighted_pods_count
        if variable_weight_pods_count > 0:
          if weighted_pods_count > 0:
            each_weight = variable_weight/variable_weight_pods_count
            residue_weight = variable_weight%variable_weight_pods_count  
          else:
            each_weight = 1
            residue_weight = 0

        variable_weight_pods_ctr = 0 
        for s in existing:
            try:
                svcgroup = servicegroup()
                svcgroup.servicegroupname = grpname
                svcgroup.servername = s[0]
                if str(svcgroup.servername) in self.weighted_pods:
                   weight = self.weighted_pods[str(svcgroup.servername)]
                   svcgroup.weight = weight
                else:
                  variable_weight_pods_ctr = variable_weight_pods_ctr  + 1
                  svcgroup.weight = each_weight 
                  if residue_weight > 0:
                     svcgroup.weight = svcgroup.weight + 1
                     residue_weight = residue_weight - 1  
                svcgroup.port = s[1]
                logger.info("Binding %s:%s from service group %s " %
                        (s[0], s[1], grpname))
                servicegroup.update(self.ns_session, svcgroup)
            except nitro_exception as e:
                logger.error("Nitro exception %s" %e.message)
       ''' 



    def _configure_services(self, grpname, srvrs):
        to_add = srvrs
        to_remove = []
        try:
            bindings = servicegroup_servicegroupmember_binding.get(
                self.ns_session, grpname)
            existing = [(b.ip, b.port) for b in bindings if b.port != 0]
            to_remove = list(set(existing) - set(srvrs))
            to_add = list(set(srvrs) - set(existing))
            to_leave = list(set(srvrs) & set(existing))
        except nitro_exception as e:
            pass  # no bindings
        for s in to_remove:
            try:
                binding = servicegroup_servicegroupmember_binding()
                binding.servicegroupname = grpname
                binding.ip = s[0]
                binding.port = s[1]
                logger.info("Unbinding %s:%s from service group %s " % (s[0], s[1],
                        grpname))
                servicegroup_servicegroupmember_binding.delete(self.ns_session,
                                                           binding)
            except nitro_exception as e:
                logger.error("Nitro exception %s" %e.message)
        for s in to_add:
            try:
                binding = servicegroup_servicegroupmember_binding()
                binding.servicegroupname = grpname
                binding.ip = s[0]
                binding.port = s[1]
                logger.info("Binding %s:%s from service group %s " %
                        (s[0], s[1], grpname))
                servicegroup_servicegroupmember_binding.add(self.ns_session,
                                                        binding)
            except nitro_exception as e:
                logger.error("Nitro exception %s" %e.message)
        #for s in to_leave:
            #logger.info("%s:%s is already bound to  service group %s"
            #            % (s[0], s[1], grpname))
        '''
        # for canary depolyment support
        self._set_weights_to_services(grpname, srvrs)
        '''
 
        return to_add, to_remove

    def _delete_nsapp_cs_vserver(self, nsapp):
        try:
            cs = csvserver.get(self.ns_session, nsapp.appName)
        except nitro_exception as e:
            return

        try:
            cs = csvserver()
            cs.name = nsapp.appName
            csvserver.delete(self.ns_session, cs)
            logger.debug("cs vserver %s delete successful" %nsapp.appName)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

        return 
    
    def _get_certkey_data(self, nsapp):
      cert_data = None
      key_data = None
      cert_file = self.certs_directory + nsapp.certkey[0]
      key_file = self.keys_directory + nsapp.certkey[1]

      try:
        with open(cert_file, "r") as f:
          cert_data = f.read()
          cert_data_base64 = base64.b64encode(cert_data)
      except IOError as e:
        logger.error("Could not read certificate file %s: %s", cert_file, e)
        return (None,None)

      try:
        with open(key_file, "r") as f:
          key_data = f.read()
          key_data_base64 = base64.b64encode(key_data)
      except IOError as e:
        logger.error("Could not read key file %s: %s", key_file, e)
        return (None,None)

      return key_data_base64, cert_data_base64

    def _delete_certkey_data(self, cert_filename, key_filename):
      try:
        sysfile = systemfile()
        sysfile._filename = cert_filename
        sysfile._filelocation='/nsconfig/ssl/'
        systemfile.delete(self.ns_session, sysfile)
      except nitro_exception as e:
        logger.error("Nitro exception %s" %e.message)

      try:
        sysfile = systemfile()
        sysfile._filename = key_filename
        sysfile._filelocation='/nsconfig/ssl/'
        systemfile.delete(self.ns_session, sysfile)
      except nitro_exception as e:
        logger.error("Nitro exception %s" %e.message)

    def _upload_certkey_data(self, cert_filename, cert_data, key_filename, key_data):
      try:
        sysfile = systemfile()
        sysfile._filecontent = cert_data 
        sysfile._filename = cert_filename
        sysfile._filelocation='/nsconfig/ssl/'
        systemfile.add(self.ns_session, sysfile)
      except nitro_exception as e:
        logger.error("Nitro exception %s" %e.message)
       
      try:
        sysfile = systemfile()
        sysfile._filecontent = key_data 
        sysfile._filename = key_filename
        sysfile._filelocation='/nsconfig/ssl/'
        systemfile.add(self.ns_session, sysfile)
      except nitro_exception as e:
        logger.error("Nitro exception %s" %e.message)

    def _unbind_ssl_vserver(self, nsapp):
      try:
        sslvserver = sslvserver_sslcertkey_binding()
        sslvserver.vservername = nsapp.appName
        sslvserver.certkeyname = nsapp.appName
        sslvserver_sslcertkey_binding.delete(self.ns_session, sslvserver)
        logger.debug("ssl vserver %s unbind successful" %nsapp.appName)
      except nitro_exception as e:
        logger.error("Nitro exception %s" %e.message)

    def _bind_ssl_vserver(self, nsapp):
      try:
        sslvserver = sslvserver_sslcertkey_binding()
        sslvserver.vservername = nsapp.appName
        sslvserver.certkeyname = nsapp.appName
        sslvserver_sslcertkey_binding.add(self.ns_session, sslvserver)
        logger.debug("ssl vserver %s bind successful" %nsapp.appName)
      except nitro_exception as e:
        logger.error("Nitro exception %s" %e.message)
 
    def _delete_nsapp_certkey(self, nsapp):
      try:
        ck = sslcertkey.get(self.ns_session, nsapp.appName)
      except nitro_exception as e:
          logger.info("Certkey %s is not configured" %nsapp.appName)
          return

      (key_data, cert_data) = self._get_certkey_data(nsapp)
      self._delete_certkey_data(nsapp.certkey[0], nsapp.certkey[1])
      try:
        ck = sslcertkey()
        ck.certkey = nsapp.appName
        ck.cert = "/nsconfig/ssl/" + nsapp.certkey[0]
        ck.key = "/nsconfig/ssl/" + nsapp.certkey[1]
        sslcertkey.delete(self.ns_session, ck)
        logger.debug("certkey %s deletion successful" %nsapp.appName)
      except nitro_exception as e:
        logger.error("Nitro exception %s" %e.message)

    def _create_nsapp_certkey(self, nsapp):
      try:
        ck = sslcertkey.get(self.ns_session, nsapp.appName)
        if ck is not None:
          logger.info("Certkey %s is already configured" %nsapp.appName)
          return
      except nitro_exception as e:
          pass

      (key_data, cert_data) = self._get_certkey_data(nsapp)
      self._upload_certkey_data(nsapp.certkey[0], cert_data, nsapp.certkey[1], key_data)
      try:
        ck = sslcertkey()
        ck.certkey = nsapp.appName
        ck.cert = "/nsconfig/ssl/" + nsapp.certkey[0]
        ck.key = "/nsconfig/ssl/" + nsapp.certkey[1]
        sslcertkey.add(self.ns_session, ck)
        logger.debug("certkey %s create successful" %nsapp.appName)
      except nitro_exception as e:
        logger.error("Nitro exception %s" %e.message)

    def _create_nsapp_cs_vserver(self, nsapp):
        try:
            cs = csvserver.get(self.ns_session, nsapp.appName)
            if (cs.name == nsapp.appName) and \
                    (cs.ipv46 == nsapp.vip) and \
                    (cs.port == nsapp.vport):
                # logger.info("CS %s is already configured " % lb.name)
                return
            else:
                logger.info("CS %s is already configured with a different \
                            VIP/port : %s:%s\n" % (cs.name, cs.ipv46, cs.port))
                raise Exception("CS %s already configured with different VIP/\
                                port : %s:%s\n" % (cs.name, cs.ipv46, cs.port))
        except nitro_exception as e:
            pass

        try:
            if self.ns_feature_enabled == False:
              self.enable_ns_features() 
            cs = csvserver()
            cs.name = nsapp.appName
            cs.ipv46 = nsapp.vip
            cs.port = str(nsapp.vport)
            cs.servicetype = nsapp.mode
            csvserver.add(self.ns_session, cs)
            logger.debug("cs vserver %s create successful" %nsapp.appName)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _delete_cs_action(self, actionname, lbname):
        try:
            csa=csaction.get(self.ns_session, actionname)
        except nitro_exception as e:
            return
        
        try:
            csa=csaction()
            csa.name=lbname
            csa.targetlbvserver=lbname
            csaction.delete(self.ns_session, csa)
            logger.debug("cs action %s delete successful" %lbname)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)


    def _create_cs_action(self, actionname, lbname):
        try:
            csa=csaction.get(self.ns_session, actionname)
            if csa.name == actionname: 
                if csa.targetlbvserver == lbname:
                    logger.info("CS action %s already exists with target %s" %(actionname, lbname))
                    return
                logger.info("CS action %s already exists with targetlbvserver %s" %(actionname, csa.targetlbvserver))
                raise Exception("CS action %s already exists with targetlbvserver %s" %(actionname, csa.targetlbvserver))
        except nitro_exception as e:
            pass
        
        try:
            csa=csaction()
            csa.name=lbname
            csa.targetlbvserver=lbname
            csaction.add(self.ns_session, csa)
            logger.debug("cs action %s create successful" %lbname)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _delete_cs_policy(self, policyname):
        try:
            csp=cspolicy.get(self.ns_session, policyname)
        except nitro_exception as e:
            return
        try:
            csp=cspolicy()
            csp.policyname=policyname
            cspolicy.delete(self.ns_session, csp)
            logger.debug("cs policy %s delete successful" %policyname)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)
 
    def _create_cs_policy(self, policyname, rule, actionname):
        try:
            csp=cspolicy.get(self.ns_session, policyname)
            if csp.policyname == policyname:
                if csp.action == actionname:
                    logger.info("CS policy %s already exists with action %s, rule %s " %(policyname, actionname, csp.rule))
                    return
                logger.info("CS policy %s already exists with action %s, rule %s " %(actionname, csa.action, csp.rule))
                raise Exception("CS policy %s already exists with action %s, rule %s " %(actionname, csa.action, csp.rule))
        except nitro_exception as e:
            pass

        try:
            csp=cspolicy()
            csp.policyname=policyname
            csp.action=actionname
            csp.rule=rule
            cspolicy.add(self.ns_session, csp)
            logger.debug("cs policy %s create successful" %policyname)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)
 
    def _unbind_default_cs_policy(self, csname, lbname):
        try:
            csb=csvserver_lbvserver_binding.get(self.ns_session, csname)
        except nitro_exception as e:
             return
        try:
            csb=csvserver_lbvserver_binding()
            csb.name=csname
            csb.lbvserver=lbname
            csvserver_lbvserver_binding.delete(self.ns_session, csb)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

   
    def _bind_default_cs_policy(self, csname, lbname):
        try:
            csb=csvserver_lbvserver_binding.get(self.ns_session, csname)
            for pol in csb:
                if pol.lbvserver == lbname:
                    logger.info("CS vserver %s already binds default lb vserver %s" %(csname, lbname))
                    return
        except nitro_exception as e:
             pass
        try:
            csb=csvserver_lbvserver_binding()
            csb.name=csname
            csb.lbvserver=lbname
            csvserver_lbvserver_binding.add(self.ns_session, csb)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    def _unbind_cs_vserver_cs_policy(self, csname, policyname, priority):
        try:
            csb=csvserver_cspolicy_binding.get(self.ns_session, csname)
        except nitro_exception as e:
           return
 
        try:
            csb=csvserver_cspolicy_binding()
            csb.name=csname
            csb.policyname=policyname
            csb.priority=priority
            csvserver_cspolicy_binding.delete(self.ns_session, csb)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)


    def _bind_cs_vserver_cs_policy(self, csname, policyname, priority):
        try:
            csb=csvserver_cspolicy_binding.get(self.ns_session, csname)
            for pol in csb:
                if pol.policyname == policyname:
                    logger.info("CS vserver %s already binds policy %s with priority %d" %(csname, policyname, priority))
                    return
        except nitro_exception as e:
            pass
        try:
            csb=csvserver_cspolicy_binding()
            csb.name=csname
            csb.policyname=policyname
            csb.priority=priority
            csvserver_cspolicy_binding.add(self.ns_session, csb)
        except nitro_exception as e:
            logger.error("Nitro exception %s" %e.message)

    @ns_session_scope
    def configure_lb_frontend(self, lbname, lb_vip, lb_port, servicetype):
        try:
            self._create_lb(lbname, "ROUNDROBIN", lb_vip, lb_port, servicetype)
        except nitro_exception as ne:
            logger.warn("Nitro Exception: %s" % ne.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)

    @ns_session_scope
    def configure_lb(self, lbname, lb_vip, lb_ports, srvrs, servicetype):
        try:
            self._create_lb(lbname, "ROUNDROBIN", lb_vip, lb_ports, servicetype)
            self._create_service_group(lbname, servicetype)  # Reuse lbname
            self._bind_service_group_lb(lbname, lbname)
            self._configure_services(lbname, srvrs)
        except nitro_exception as ne:
            logger.warn("Nitro Exception: %s" % ne.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)

    @ns_session_scope
    def configure_app(self, lbname,  srvrs):
        try:
            self._create_service_group(lbname, 'HTTP')  # Reuse lbname
            self._bind_service_group_lb(lbname, lbname)
            self._configure_services(lbname, srvrs)
        except nitro_exception as ne:
            logger.warn("Nitro Exception: %s" % ne.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)

    @ns_session_scope
    def configure_nsapp(self, ns_csapp):
        try:
            if (self.configure_frontends and ns_csapp.lb_role is 'server') or (not self.configure_frontends and ns_csapp.lb_role is 'client'):
                self._create_nsapp_cs_vserver(ns_csapp)
                if ns_csapp.certkey is not None:
                   self._create_nsapp_certkey(ns_csapp)
                   self._bind_ssl_vserver(ns_csapp)
            for policyname, policy in ns_csapp.policies.iteritems():
                ns_app=policy['targetlbapp']
                """ Attention Hack!
                    Ingress construction should be of type HTTP,
                    but kubernetes service and all accompany infrastructure is based on protocol field and
                    can be either tcp or udp.
                    So in case of creating ingress entities, override service type to http """
                if ns_csapp.lb_role == 'server' and ns_app == None:
                    #A valid case when ingress is configured earlier than service configuration
                    continue
                if self.frozen_frontend_from_app_info:
                    override_service_type = "http"
                else:
                    override_service_type = None
 
                if not self.frozen_frontend_from_app_info or ns_csapp.lb_role == "client":
                    self._create_nsapp_vserver(ns_app, override_service_type)
                self._create_nsapp_service_group(ns_app, override_service_type)  # Reuse lbname
                self._bind_service_group_lb(ns_app.appName, ns_app.appName)
                logger.info("Configured " + str(ns_app.appName) + " with NS monitoring : " + str(ns_app.healthmonitor))
                srvrs = [(x.host, x.port) for x in ns_app.backends]
                self._configure_services(ns_app.appName, srvrs)

                self.nslbapps[ns_app.appName]=ns_app

                if (self.configure_frontends and ns_csapp.lb_role is 'server') or (not self.configure_frontends and ns_csapp.lb_role is 'client'):
                    if policyname == 'default':
                        self._bind_default_cs_policy(ns_csapp.appName, ns_app.appName)
                    else:
                        self._create_cs_action(ns_app.appName, ns_app.appName)
                        self._create_cs_policy(policyname, policy['rule'], ns_app.appName)
                        self._bind_cs_vserver_cs_policy(ns_csapp.appName, policyname, policy['priority'])

            if self.nstype != 'CPX':
                    self._saveconfig()
        except nitro_exception as ne:
             logger.warn("Nitro Exception: %s" % ne.message)
        except Exception as e:
             logger.warn("Exception: %s" % e.message)

        # Store nsapp to be able to delete the iptable rule when
        # app is deleted. 
        if self.nstype == 'CPX' and ns_csapp.lb_role == 'client':
            triton.nsappinterface.iptables.install_vserver_iptable_rules(ns_csapp)
        self.nsapps[ns_csapp.appName] = ns_csapp

    def _delete_ingress_association(self, lb_app):
           for _, app in self.nsapps.iteritems():
               if app.lb_role == 'server':
                   ingress = app
                   for policyname, pol in ingress.policies.iteritems():
                     if pol['targetlbapp'] == lb_app:
                        self._unbind_cs_vserver_cs_policy(app.appName, policyname, pol['priority'])
                        self._delete_cs_policy(policyname)
                        self._delete_cs_action(lb_app.appName,lb_app.appName)
                        pol['targetlbapp'] = None
 
    @ns_session_scope
    def delete_nsapp(self, ns_app):
        try:
            if not self.frozen_frontend_from_app_info:
                self._delete_nsapp_cs_vserver(ns_app)
                for policyname, policy in ns_app.policies.iteritems():
                   lb_app=policy['targetlbapp']
                   self._delete_nsapp_vserver(lb_app)
                   self._delete_nsapp_service_group(lb_app)  # Reuse lbname
            elif ns_app.lb_role == 'client':
                for policyname, policy in ns_app.policies.iteritems():
                   lb_app=policy['targetlbapp']
                   self._delete_ingress_association(lb_app)
                   self._delete_nsapp_vserver(lb_app)
                   self._delete_nsapp_service_group(lb_app)  # Reuse lbname
                self._delete_nsapp_service_group(ns_app)  # Reuse lbname
            elif ns_app.lb_role == 'server':
                if ns_app.certkey is not None:
                   self._unbind_ssl_vserver(ns_app)
                   self._delete_nsapp_certkey(ns_app)
                for policyname, policy in ns_app.policies.iteritems():
                   lb_app=policy['targetlbapp']
                   if lb_app is None:
                      continue
                   self._unbind_cs_vserver_cs_policy(ns_app.appName, policyname, policy['priority'])
                   self._delete_cs_policy(policyname)
                   self._delete_cs_action(lb_app.appName, lb_app.appName)
                self._delete_nsapp_cs_vserver(ns_app)
               
            if (self.nstype != 'CPX'):
                self._saveconfig()
        except nitro_exception as ne:
            logger.warn("Nitro Exception: %s" % ne.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
        if self.nstype == 'CPX' and ns_app.lb_role == 'client':
            triton.nsappinterface.iptables.delete_vserver_iptable_rules(ns_app)
        if ns_app.framework == Framework.kubernetes:
            self.nsvip_ports.append(ns_app.vport)
        self.nsapps.pop(ns_app.appName)

    @ns_session_scope
    def delete_lb(self, lbname):
        try:
            self._delete_lb(lbname)
            self._delete_servicegroup(lbname)
        except nitro_exception as ne:
            logger.warn("Nitro Exception: %s" % ne.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)

    @ns_session_scope
    def adjust_service_group(self, lbapp):
        to_add = []
        to_remove = []
        members = [(x.host, x.port) for x in ns_app.backends]
        try:
            to_add, to_remove = self._configure_services(lbapp.appName, members)
        except nitro_exception as ne:
            logger.warn("Nitro Exception: %s" % ne.message)
            return None, None
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
            return None, None

        return to_add, to_remove



    def _clearconfig(self):
        var = nsconfig()
        var.level = nsconfig.Level.basic
        nsconfig.clear(self.ns_session, var)
        logger.debug("clearconfig - Done")

    def _saveconfig(self) :
        var = nsconfig()
        nsconfig.save(self.ns_session, var)
        logger.debug("saveconfig - Done")

    def clean_ip_tables(self):
        # In some cases we want to continue iptables manipulation even if some of the commands failed.
        # So Exception handling is done inside cleanup and install_global_iptable_rules
        triton.nsappinterface.iptables.cleanup()
        triton.nsappinterface.iptables.install_global_iptable_rules(self)
        logger.info ("reinitialized iptables")

    @ns_session_scope
    def clear_ns_config(self):
        try:
            self._clearconfig()
        except nitro_exception as e :
            logger.warn("Nitro Exception::clearconfig::errorcode="+str(e.errorcode)+",message="+ e.message)
        except Exception as e:
            logger.warn("Exception::clearconfig::message="+str(e.args))

    @ns_session_scope
    def enable_ns_features(self):
        try:
            feature = [nsfeature.Feature.CS]
            self.ns_session.enable_features(feature)
            feature = [nsfeature.Feature.LB]
            self.ns_session.enable_features(feature)
            self.ns_feature_enabled = True
            logger.debug("cs and lb features enabled successfully")
        except nitro_exception as e :
            logger.error("Nitro Exception::enable_ns_features::errorcode="+str(e.errorcode)+",message="+ e.message)
        except Exception as e:
            logger.error("Exception::enable_ns_features::message="+str(e.args))


    @ns_session_scope
    def get_nsip(self):
        obj = nsip()
        try:
            result = nsip.get(self.ns_session, obj)
            return str(result.ipaddress)
        except nitro_exception as e :
            logger.warn("Nitro Exception::clearconfig::errorcode="+str(e.errorcode)+",message="+ e.message)
        except Exception as e:
            logger.warn("Exception::clearconfig::message="+str(e.args))


    @ns_session_scope
    def save_ns_config(self):
        try:
            self._saveconfig()
        except nitro_exception as e :
            logger.warn("Nitro Exception::saveconfig::errorcode="+str(e.errorcode)+",message="+ e.message)
        except Exception as e:
            logger.warn("Exception::saveconfig::message="+str(e.args))

    def delete_ns(self):
        pass

    def sync_ns_devices(self):
        logger.debug("Nitro interface: empty sync_ns_devices")
        return set()

    def delete_ns_as_task(self, taskid):
        logger.info("Nitro interface: empty deleting %s from device list", taskid)
        pass
