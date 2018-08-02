import logging
import os
import json
import requests
from netscalerapp import NetscalerAppInterface
from netscalerapp import NetscalerApp
from netscalerapp import Framework
from umsinterface import UMSInterface

logger = logging.getLogger('docker_netscaler')

try:
    import triton.nsappinterface.iptables
except:
    logger.warning("Ignoring IPtables Module (Only used in CPX context.)")

# Class to conduct iptables related actions
class IPTablesInterface(UMSInterface):
    """ Don't really need theese variables, but to make 
        upper class contructor happy
    """
    NETSCALER_APP={}
    NETSCALER_SERVICES={}

    def __init__(self, *args, **kwargs):
        super(IPTablesInterface, self).__init__(*args, **kwargs)
        self.clean_ip_tables()

    def __str__(self):
        return "iptablesinterface"


    def clean_ip_tables(self):
        # In some cases we want to continue iptables manipulation even if some of the commands failed.
        # So Exception handling is done inside cleanup and install_global_iptable_rules
        triton.nsappinterface.iptables.cleanup()
        triton.nsappinterface.iptables.install_global_iptable_rules(self)
        logger.info ("reinitialized iptables")

    def api_req(self, method, path, resourcetype, body=None, **kwargs):
        return self.api_req_raw_http(method, path, body=body, **kwargs).json()

    def sync_ns_cs_apps(self):
        pass
    
    def configure_ns_cs_app(self, nsapp):
        if nsapp.config_session:
            return 
        if nsapp.serviceIP != None:
            if nsapp.lb_role == "client":
                self.waiting_apps.add(nsapp)
        else:
            logger.info('IPTables: Headless service %s: not configuring iptables',nsapp.appName)
        self.nsapps[nsapp.appName]=nsapp
        for _, pol in nsapp.policies.iteritems():
            lbapp=pol['targetlbapp']
            if lbapp != None  and not lbapp.appName in self.nslbapps:
                self.nslbapps[lbapp.appName]=lbapp
        nsapp.config_session=True
        
    def configure_iptables_for_app(self):
        try:
            url = self.appmanager_url
            resourcetype = self.appmanager_resourcetype
            payload = "\"managed_app\": [{}]"
            resp_json = self.api_req("GET", url, resourcetype, body=payload)
        except requests.exceptions.RequestException as e:
            logger.error('IPtables: Get All Apps: Application: Error while calling %s: %s', url, e.message)
            return
        try:
            managed_apps = resp_json["managed_app"]
            for m_app in managed_apps:
                if m_app["app_id"] not in self.nsapps:
                    logger.error("IPTables: Could not find app %s in configured apps ", m_app["app_id"])
                    continue
                ns_app = self.nsapps[m_app["app_id"]]
                if ns_app not in self.waiting_apps:
                    logger.info("IPTables: Not waiting for app %s ", ns_app.appName)
                    continue 
                parameter_str = m_app["app_netfuncs_config"][0]["netfunc_params"]["parameters"]
                parameter = json.loads(parameter_str)
                ns_app.vport = parameter["cs-virtual-port"]

                triton.nsappinterface.iptables.install_vserver_iptable_rules(ns_app)
                self.waiting_apps.remove(ns_app)
        except ValueError as e:
            logger.error("Get All Apps: mananged_app is not decoded in proper json: %s", e)
            return
        except Exception as e:
            logger.error("Get All Apps: managed_app unkwown decoding error: %s",e)
            return

    def delete_nsapp(self, nsapp):
        logger.info("IPtables: Deleting application %s iptables", nsapp.appName)

        if nsapp.serviceIP != None:
            if nsapp.lb_role == "client":
                triton.nsappinterface.iptables.delete_vserver_iptable_rules(nsapp)
        else:
            logger.info('IPTables: Headless service %s: not configuring iptables',nsapp.appName)
        self.nsapps.pop(nsapp.appName)

    def sync_ns_devices(self):
        logger.debug("IPtables: empty sync_ns_devices")
        return set()

    def delete_ns_as_task(self, taskid):
        logger.info("IPTables: empty deleting %s from device list", taskid)
        pass

