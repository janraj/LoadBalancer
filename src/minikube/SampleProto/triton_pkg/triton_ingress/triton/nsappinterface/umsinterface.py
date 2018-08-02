import logging
import os
import json
import requests
from netscalerapp import NetscalerAppInterface
from netscalerapp import NetscalerApp
from netscalerapp import NetscalerCsApp
from netscalerapp import Framework
from netscalerapp import NetscalerAppServicetype

logger = logging.getLogger('docker_netscaler')
''' Class to interact with UMS '''
class UMSInterface(NetscalerAppInterface):
    ns_device_url = "/nitro/v1/config/ns/"
    managed_device_url = "/nitro/v1/config/managed_device/"
    appmanager_url = "/nitro/v1/config/managed_app/"
    appmanager_resourcetype = "managed_app"
    NETSCALER_LOGIN = {
        "login": {
        "username": "{username}",
        "password": "{password}"
        }
    }
    login_parameters = NETSCALER_LOGIN["login"]
    username = {"username": login_parameters}
    password = {"password": login_parameters}
    certs_directory = '/var/mps/tenants/root/ns_ssl_certs/'
    keys_directory  = '/var/mps/tenants/root/ns_ssl_keys/'
    def __init__(self, host, nslogin, nspasswd, app_info, nsgroup, nstype,
                 nsnetmode, nsvip, loopbackdisabled, framework_healthcheck, is_controller, configure_frontends=False, networking=None, dns=None, zmq_sock=None):
        ''' Initialize variable in base class. '''
        super(UMSInterface, self).__init__(app_info,
                nsgroup,
                nstype,
                nsnetmode,
                nsvip,
                loopbackdisabled,
                framework_healthcheck,
                networking,
                dns
                )
        self.__host = "http://" + host
        self.nslogin = nslogin
        self.nspasswd = nspasswd
        self.ns_session = {}
        self.headers= {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        self.__template_directory = 'templates'
        self.__load_templates()
        self.zmq_sock = zmq_sock
        if self.zmq_sock == None:
            self.login()
        else:
            import zmq

    def __str__(self):
        return "umsinterface"

    def __load_templates(self):
        '''Loads template files if they exist, othwerwise it sets defaults'''
        variables = [
            'NETSCALER_APP',
            'NETSCALER_SERVICES',
            'NETSCALER_LOGIN'
        ]

        for variable in variables:
            try:
                filename = os.path.join(self.__template_directory, variable)
                with open(filename) as f:
                    logger.info('overriding %s from %s', variable, filename)
                    setattr(self, variable, f.read())
            except IOError:
                logger.debug("setting default value for %s", variable)
                try:
                    setattr(self, variable, getattr(self.__class__, variable))
                except AttributeError:
                    logger.exception('default not found, aborting.')
                    raise

    @property
    def netscaler_app(self):
        return self.NETSCALER_APP

    @property
    def netscaler_services(self):
        return self.NETSCALER_SERVICES

    @property
    def netscaler_login(self):
        return self.NETSCALER_LOGIN

    def __blank_prefix_or_empty(self, s):
        if s:
            return ' ' + s
        else:
            return s

    def login(self):
        for key, parameter in self.username.iteritems():
            parameter[key] = self.nslogin
        for key, parameter in self.password.iteritems():
            parameter[key] = self.nspasswd

        loginpayload = json.dumps(self.netscaler_login)
        login_url = self.__host + "/nitro/v2/config/login"
        response = requests.request(
            "POST",
            login_url,
            data = loginpayload,
            headers=self.headers,
            verify=False)
        response.raise_for_status()
        self.ns_session = {'NITRO_AUTH_TOKEN': response.cookies["NITRO_AUTH_TOKEN"]}

    def api_req_raw_http(self, method, path, body=None, **kwargs):
        #loop is to try login if request fails with 401
        for i in range(2):
            path_str = self.__host
            path_str = path_str + path
            response = requests.request(
                method,
                path_str,
                data = body,
                headers = self.headers,
                cookies = self.ns_session,
                **kwargs
            )

            logger.debug("UMSInterface-HTTP: %s %s", method, response.url)
            if response.status_code == 401:
                self.login()
                continue
            else:
                break
        if 'message' in response.json():
            response.reason = "UMSInterface-HTTP: %s (%s)" % (
                response.reason,
                response.json()['message'])
        response.raise_for_status()
        logger.debug("\n" + json.dumps(response.json(), sort_keys=True, indent=4))
        return response

    def api_req_raw_zmq(self, method, resourcetype, body=None, **kwargs):
        req_json_str = "{"
        req_json_str +=     "\"resourceType\": \"" + resourcetype + "\""
        req_json_str +=     ",\"operation\": \"" + method + "\""
        req_json_str +=     "," +  body
        req_json_str += "}"
        logger.debug("UMSInterface-ZMQ: %s %s", method, resourcetype)
        logger.debug("\n" + json.dumps(json.loads(req_json_str), sort_keys=True, indent=4))
        self.zmq_sock.send_string(req_json_str);
        response = self.zmq_sock.recv()

        resp_json = json.loads(response)
        logger.debug("\n" + json.dumps(resp_json, sort_keys=True, indent=4))
        return resp_json

    def fill_common_parameters(self, nsapp):
        for key, parameter in self.appname.iteritems():
            parameter[key] = nsapp.appName

        for key, parameter in self.lb_role.iteritems():
            parameter[key] = nsapp.lb_role

        if self.app_dns_domain:
            for key, parameter in self.app_dns_domain.iteritems():
                parameter[key] = nsapp.dns_domain

        if self.orchestrator_env:
            for key, parameter in self.orchestrator_env.iteritems():
                parameter[key] = nsapp.framework

        if self.network_env:
            for key, parameter in self.network_env.iteritems():
                parameter[key] = nsapp.network_env

        if self.app_networking_data:
            for key, parameter in self.app_networking_data.iteritems():
                try:
                    parameter[key] = json.dumps(nsapp.networking_app_data)
                except Exception as e:
                    logger.error('app_networking_data not in dictionary format: %s', e)

        if self.stylebook_id:
            for key, parameter in self.stylebook_id.iteritems():
                parameter[key] = nsapp.stylebook

    def nsapp_to_apppayload(self, nsapp):
        # Let's cleanup the list elements state.
        self.app_sb_service_params.clear()
        for key, parameter in self.app_services.iteritems():
            app_services = parameter[key] = []

        for key, parameter in self.backends.iteritems():
            app_instances = parameter[key] = []

        self.fill_common_parameters(nsapp)

        # Fill app stylebook payload parameters
        for key, parameter in self.app_service_name.iteritems():
            parameter[key] = nsapp.appName

        # Fill app service parameters
        for key, parameter in self.app_cs_lb_sb_params.iteritems():
            parameter[key] = nsapp.mode

        for key, parameter in self.vip.iteritems():
            parameter[key] = nsapp.vip

        for key, parameter in self.vport.iteritems():
            parameter[key] = nsapp.vport

       # Should I verify the stylebook_additional_params?
        if self.stylebook_additional_params:
            #for key, parameter in self.stylebook_additional_params.iteritems():
            for sb_svc_param in nsapp.stylebook_additional_params:
                self.app_sb_service_params[sb_svc_param] = nsapp.stylebook_additional_params[sb_svc_param]

        for backend in nsapp.backends:
            for key, parameter in self.host.iteritems():
                parameter[key] = backend.host
            for key, parameter in self.port.iteritems():
                parameter[key] = backend.port
            app_instances.append(self.netscaler_services.copy())

        #for app_svc in num_services:
        app_services.append(self.app_sb_service_params)

        for key, parameter in self.stylebook_payload.iteritems():
            parameter[key] = json.dumps(self.app_sb_params)

        #logger.debug("\n" + json.dumps(self.netscaler_app, sort_keys=True, indent=4))
        return self.netscaler_app

    def ns_cs_app_to_apppayload(self, nsapp):
        """ Filling app_manager request for cs based applications """
        """ Ceaning arrays """
        self.app_cs_lb_sb_params.clear()
        for key, parameter in self.lb_pools.iteritems():
            parameter[key] = []
        for key, parameter in self.certificate.iteritems():
            parameter[key] = []

        self.fill_common_parameters(nsapp)

        # Fill app stylebook payload parameters
        for key, parameter in self.cs_vservername.iteritems():
            parameter[key] = nsapp.appName

        # Fill app service parameters
        for key, parameter in self.cs_service_type.iteritems():
            parameter[key] = nsapp.mode

        for key, parameter in self.cs_vip.iteritems():
            parameter[key] = nsapp.vip

        for key, parameter in self.cs_vport.iteritems():
            if nsapp.vip == "$NS-VIP-NS$":
                if nsapp.mode == 'ssl':
                    parameter[key] = "$NS-HTTPS-PORT-NS$"
                else:
                    parameter[key] = "$NS-HTTP-PORT-NS$"
            else:
                parameter[key] = nsapp.vport
        
        if nsapp.certkey:
            self.app_certificate.clear()
            self.app_certificate['ssl-inform'] = "PEM"
            self.app_certificate_advanced.clear()
            for key, parameter in self.cert_name.iteritems():
                parameter[key] = nsapp.sslCert
            for key, parameter in self.cert_file.iteritems():
                parameter[key] = nsapp.certkey[0]
            for key, parameter in self.key_file.iteritems():
                parameter[key] = nsapp.certkey[1]
            for key, parameter in self.certificate.iteritems():
                parameter[key].append(self.app_certificate.copy())
        
        for certname, cert in nsapp.cert_ca.iteritems():
            self.app_certificate.clear()
            self.app_certificate['ssl-inform'] = "PEM"
            self.app_certificate_advanced.clear()
            for key, parameter in self.cert_name.iteritems():
                parameter[key] = nsapp.sslCert + '-ca'
            for key, parameter in self.cert_file.iteritems():
                parameter[key] = cert[0]
            for key, parameter in self.ca_cert.iteritems():
                parameter[key] = True
            for key, parameter in self.certificate_advanced.iteritems():
                parameter[key] = self.app_certificate_advanced.copy()
            for key, parameter in self.certificate.iteritems():
                parameter[key].append(self.app_certificate.copy())
            
        for polname, pol in nsapp.policies.iteritems():
            lbapp=pol['targetlbapp']
            if lbapp == None:
                # There is no lb vserver yet, do not confiure its policy
                continue
                
            lbpool=self.app_lb_params.copy()
            for key,_ in self.lb_backends.iteritems():
                app_instances=lbpool[key]=[]

            for key,_ in self.lb_vservername.iteritems():
                lbpool[key]=lbapp.appName
            for key,_ in self.lb_virtual_ip.iteritems():
                lbpool[key]=lbapp.vip
            for key,_ in self.lb_virtual_port.iteritems():
                lbpool[key]=lbapp.vport
            for key,_ in self.lb_service_type.iteritems():
                lbpool[key] = NetscalerAppServicetype.lbpoolservicetype.get(nsapp.mode, {}).get(nsapp.ssl_termination, nsapp.mode)
                
            for key,_ in self.lb_svc_service_type.iteritems():
                lbpool[key] = NetscalerAppServicetype.servicegroupservicetype.get(nsapp.mode, {}).get(nsapp.ssl_termination, nsapp.mode)

            for backend in lbapp.backends:
                for key, parameter in self.host.iteritems():
                    parameter[key] = backend.host
                for key, parameter in self.port.iteritems():
                    parameter[key] = backend.port
                app_instances.append(self.netscaler_services.copy())
            
            if polname == 'default':
                for key, parameter in self.default_lb_pool.iteritems():
                    parameter[key]=lbpool
            else:
                for key, parameter in self.lb_pool.iteritems():
                    parameter[key]=lbpool
                for key, parameter in self.lb_rule.iteritems():
                    parameter[key]=pol['rule']
                for key, parameter in self.lb_priority.iteritems():
                    parameter[key]=pol['priority']
                for key, parameter in self.lb_pools.iteritems():
                    parameter[key].append(self.app_lb_pool_pi_params.copy())
            if self.stylebook_additional_params:
                for sb_svc_param in pol['stylebook_params']:
                    lbpool[sb_svc_param] = pol['stylebook_params'][sb_svc_param]

       # Should I verify the stylebook_additional_params?
        if self.stylebook_additional_params:
            #for key, parameter in self.stylebook_additional_params.iteritems():
            for sb_svc_param in nsapp.stylebook_additional_params:
                self.app_cs_lb_sb_params[sb_svc_param] = nsapp.stylebook_additional_params[sb_svc_param]

        for key, parameter in self.stylebook_payload.iteritems():
            parameter[key] = json.dumps(self.app_cs_lb_sb_params)

        logger.debug("\n" + json.dumps(self.netscaler_app, sort_keys=True, indent=4))
        return self.netscaler_app

    def configure_nsapp(self, ns_app):
        if not ns_app.config_session:
            ns_app.vip=self.nsapp_allocate_vip(ns_app)
        if self.networking != None:
            endps=self.networking.networking_get_app_endpoints(ns_app)
            ns_app.clear_backends()
            for be in endps:
                logger.debug("Additing end point ip %s port %s" %(be, ns_app.vport))
                ns_app.add_backend_member(be, ns_app.vport)
        app_payload = self.nsapp_to_apppayload(ns_app)
        self.post(ns_app, app_payload)
        self.nsapps[ns_app.appName] = ns_app

    def configure_ns_cs_app(self, ns_app):
        if not ns_app.config_session:
            ns_app.vip=self.nsapp_allocate_vip(ns_app)
        if self.networking != None:
            endps=self.networking.networking_get_app_endpoints(ns_app)
            ns_app.clear_backends()
            for be in endps:
                logger.debug("Additing end point ip %s port %s" %(be, ns_app.vport))
                ns_app.add_backend_member(be, ns_app.vport)
        app_payload = self.ns_cs_app_to_apppayload(ns_app)
        self.post(ns_app, app_payload)
        self.nsapps[ns_app.appName] = ns_app
        for _, pol in ns_app.policies.iteritems():
            lbapp=pol['targetlbapp']
            if lbapp != None  and not lbapp.appName in self.nslbapps:
                self.nslbapps[lbapp.appName]=lbapp

    def delete_nsapp(self, ns_app):
        self.delete(ns_app)
        if ns_app.lb_role == "client" and ns_app.framework == Framework.kubernetes:
            self.nsvip_ports.append(ns_app.vport)
        self.nsapps.pop(ns_app.appName)

    def delete_ns_as_task(self, taskid):
        logger.info("deleting %s from device list", taskid)
        self.delete_ns_device(taskid)

    def delete_ns_as_app(self, app):
        logger.info("deleting app %s which corresponds to ns devices", app)
        for taskid in self.devices[app]:
            self.delete_ns_as_task(taskid)
        self.devices.pop(app)

    def sync_ns_devices(self):
        try:
            old_task_ids = set()
            url = self.ns_device_url
            resourcetype = self.appmanager_resourcetype
            payload = "\"ns\": [{}]"
            resp_json = self.api_req("GET", url, resourcetype, body=payload)
            ns_devices = resp_json["ns"]
            for ns_d in ns_devices:
                if 'task_id' in ns_d and len(ns_d['task_id']):
                    old_task_ids.add(ns_d['task_id'])
            self.devices = {}
            return old_task_ids
        except Exception as e:
            logger.error('Exception occured during syncing ns devices : %s', e)
            return set()

    def delete_ns_device(self, taskid):
        try:
            url = self.ns_device_url
            resourcetype = self.appmanager_resourcetype
            payload = "\"ns\": [{}]"
            resp_json = self.api_req("GET", url, resourcetype, body=payload)
            ns_devices = resp_json["ns"]
            for ns_d in ns_devices:
                if ns_d['task_id'] == taskid:
                    nsid = ns_d['id']
                    try:
                        url = self.managed_device_url +  nsid
                        resourcetype = "managed_device"
                        payload = "\"managed_device\": {" + "\"id\": \"" + nsid + "\"}"
                        logger.info('AppManagerInterface: Deleted netscaler %s', taskid)
                        self.api_req("DELETE", url, resourcetype, body=payload)
                        logger.info('AppManagerInterface: Deleted netscaler %s', taskid)
                    except requests.exceptions.RequestException as e:
                        logger.error('DELETE Netscaler: Error while calling %s: %s', url, e)
                    return
        except requests.exceptions.RequestException as e:
            logger.error('Get All NS Devices: Error while calling %s: %s', url, e)
            return

class StylebookInterface(UMSInterface):
    stylebook_url = "/stylebook/nitro/v1/config/stylebooks/"

    NETSCALER_APP = {
        "configpack": {
            "parameters": None,
            "target_devices": { "10.217.212.175" : {'id': '56f3875d1bd42059e5cf69a6'}}
        }
    }

    NETSCALER_STYLEBOOK_PARAMETERS = {
        "appname": "{appname}",
        "app-services" : [],
    }

    NETSCALER_STYLEBOOK_PARAM_APP_SERVICES = {
        "virtual-ip": "{vip}",
        "virtual-port": "{vport}",
        "protocol": "{servicetype}",
        "servers": []
    }

    NETSCALER_SERVICES = {
        "ip": "{host}",
        "port": "{port}"
    }

    parameters = NETSCALER_APP
    app_sb_params = NETSCALER_STYLEBOOK_PARAMETERS
    app_sb_service_params = NETSCALER_STYLEBOOK_PARAM_APP_SERVICES

    app_service_name = {'appname': app_sb_params}

    servicetype = { 'protocol': app_sb_service_params}
    vip = {'virtual-ip' : app_sb_service_params}
    vport = {'virtual-port': app_sb_service_params}
    backends = {'servers': app_sb_service_params}
    app_services = {'app-services' : app_sb_params}
    stylebook_additional_params = {'stylebook_additional_params': app_sb_service_params}

    app_netfunc_params = parameters['configpack']
    stylebook_payload = {'parameters': app_netfunc_params}

    host = {'ip' : NETSCALER_SERVICES}
    port = {'port' : NETSCALER_SERVICES}

    ''' Not used '''
    orchestrator_env = None
    network_env = None
    app_networking_data = None
    stylebook_id = None
    app_dns_domain = None
    appname = {}

    NETSCALER_SERVICES = {
            "ip": "{host}",
            "port": "{port}",
            "add-server": False
    }
    host = {'ip' : NETSCALER_SERVICES}
    port = {'port' : NETSCALER_SERVICES}

    
    def __init__(self, *args, **kwargs):
        super(StylebookInterface, self).__init__(*args, **kwargs)

    def __str__(self):
        return "stylebookinterface"

    def api_req(self, method, path, resourcetype, body=None, **kwargs):
        if self.zmq_sock:
            if method == "POST":
                method = "add"
            elif method == "PUT":
                method = "modify"
            elif method == "GET":
                method = "get"
            elif method == "DELETE":
                method = "delete"
            return self.api_req_raw_zmq(method, resourcetype, body=body, **kwargs)
        else:
            return self.api_req_raw_http(method, path, body=body, **kwargs).json()

    def post(self, nsapp, app_payload):
        if nsapp.config_session:
            self.update(nsapp, app_payload)
        else:
            self.create(nsapp, app_payload)

    def create(self, nsapp, app_payload):
        try:
            url = self.stylebook_url + nsapp.stylebook + "/configpacks"
            resp_json = self.api_req("POST", url, None, body=json.dumps(app_payload))
        except requests.exceptions.RequestException as e:
            logger.error('Error while calling %s: %s', url, e)
            return
        except Exception as e:
            logger.error('Error while creating app: %s', e)
            return
        try:
            nsapp.config_session = resp_json["configpack"]["config_id"]
        except Exception as e:
            logger.error('Error accessing configid in Stylebook payload')

    def update(self, nsapp, app_payload):
        try:
            url = self.stylebook_url + nsapp.stylebook + "/configpacks/" + nsapp.config_session
            resp_json = self.api_req("PUT", url, None, body=json.dumps(app_payload))
        except requests.exceptions.RequestException as e:
            logger.error('Error while calling %s: %s', url, e)
            return
        try:
            nsapp.config_session = resp_json["configpack"][nsapp.stylebook]["configID"]
        except Exception as e:
            logger.error('Error accessing configid in Stylebook payload')

    def delete(self, nsapp):
        try:
            url = self.stylebook_url + nsapp.stylebook + "/configpacks/" + nsapp.config_session
            resp_json = self.api_req("DELETE", url, None)
        except requests.exceptions.RequestException as e:
            logger.error('Error while calling %s: %s', url, e)
            return

class AppmanagerInterface(UMSInterface):
    appmanager_url = "/nitro/v1/config/managed_app/"
    appmanager_resourcetype = "managed_app"
    NETSCALER_APP = {
        "app_id": "{appname}",
        "appname": "{appname}",
        "app_dns": "",
        "container_manager": "",
        "networking_manager": "",
        "app_networking_data": "",
        "dns_manager": "n/a",
        "lb_role": "client",
        "app_netfuncs_config": [
            {
                "type" : "stylebook",
                "netfunc_params" : {
                    "netfunc_id" : "{stylebook_id}",
                    "parameters" : None
                }
            }
        ]
    }

    NETSCALER_STYLEBOOK_PARAMETERS = {
        "appname": "{appname}",
        "app-services" : [],
    }

    NETSCALER_STYLEBOOK_PARAM_APP_SERVICES = {
        "virtual-ip": "{vip}",
        "virtual-port": "{vport}",
        "protocol": "{servicetype}",
        "servers": []
    }

    NETSCALER_SERVICES = {
        "ip": "{host}",
        "port": "{port}",
        "add-server": False
    }

    parameters = NETSCALER_APP
    app_sb_params = NETSCALER_STYLEBOOK_PARAMETERS
    app_sb_service_params = NETSCALER_STYLEBOOK_PARAM_APP_SERVICES

    appname = { 'app_id' : parameters, 'appname' : parameters}
    app_dns_domain = { 'app_dns' : parameters}
    app_service_name = {'appname': app_sb_params}

    servicetype = { 'protocol': app_sb_service_params}
    vip = {'virtual-ip' : app_sb_service_params}
    vport = {'virtual-port': app_sb_service_params}
    backends = {'servers': app_sb_service_params}
    app_services = {'app-services' : app_sb_params}
    stylebook_additional_params = {'stylebook_additional_params': app_sb_service_params}

    app_netfunc_params = parameters["app_netfuncs_config"][0]['netfunc_params']
    stylebook_id = {'netfunc_id': app_netfunc_params}
    stylebook_payload = {'parameters': app_netfunc_params}

    host = {'ip' : NETSCALER_SERVICES}
    port = {'port' : NETSCALER_SERVICES}

    orchestrator_env = {'container_manager': parameters}
    network_env = {'networking_manager' : parameters}
    app_networking_data = {'app_networking_data': parameters}
    lb_role = {'lb_role': parameters}


    NETSCALER_CS_LB_STYLEBOOK_PARAM_APP_SERVICES = {
        "appname": "{cs_vserver_name}",
        "cs-virtual-ip": "{cs_vip}",
        "cs-virtual-port": "{cs_vip_port}",
        "cs-service-type": "{cs_service_type}",
        "certificates": [],
        "default-lb-pool": None,
        "pools": []
    }

    NETSCALER_CS_LB_STYLEBOOK_POOL_PI = {
        "rule": "{pool_rule}",
        "priority": "{priority}",
        "lb-pool": "{NETSCALER_CS_LB_POOL}"
    }

    NETSCALER_CS_LB_POOL = {
        "lb-appname": "{lb_app_name}",
        "lb-virtual-ip": "{lb_virtual_ip}",
        "lb-virtual-port": "{lb_virtual_port}",
        "lb-service-type": "{lb_service_type}",
        "svc-servers": [],
    }

    NETSCALER_CERTIFICATE = {
        "cert-name": "{certkey_name}",
        "cert-file": "{cert_file}",
        "key-file": "{key_file}",
        "ssl-inform": "PEM"
    }

    NETSCALER_CERT_ADVANCED = {}

    app_cs_lb_sb_params=NETSCALER_CS_LB_STYLEBOOK_PARAM_APP_SERVICES
    app_lb_params=NETSCALER_CS_LB_POOL
    app_lb_pool_pi_params=NETSCALER_CS_LB_STYLEBOOK_POOL_PI
    app_certificate=NETSCALER_CERTIFICATE
    app_certificate_advanced=NETSCALER_CERT_ADVANCED

    cs_vservername = {'appname': app_cs_lb_sb_params}
    cs_vip = {'cs-virtual-ip' : app_cs_lb_sb_params}
    cs_vport = {'cs-virtual-port': app_cs_lb_sb_params}
    cs_service_type = {'cs-service-type': app_cs_lb_sb_params}
    default_lb_pool = {'default-lb-pool': app_cs_lb_sb_params}
    certificate = {"certificates": app_cs_lb_sb_params}
    lb_pools = {'pools': app_cs_lb_sb_params}

    cert_name = {'cert-name': app_certificate}
    cert_file = {'cert-file': app_certificate}
    key_file = {'key-file': app_certificate}
    certificate_advanced = {'cert-advanced': app_certificate}

    ca_cert = {'is-ca-cert': app_certificate_advanced}
    sni_cert = {'sni-cert': app_certificate_advanced}

    lb_rule = {'rule': app_lb_pool_pi_params}
    lb_priority = {'priority': app_lb_pool_pi_params}
    lb_pool = {'lb-pool': app_lb_pool_pi_params}

    lb_vservername = {'lb-appname': app_lb_params}
    lb_virtual_ip = {'lb-virtual-ip': app_lb_params}
    lb_virtual_port = {'lb-virtual-port': app_lb_params}
    lb_service_type = {'lb-service-type': app_lb_params}
    lb_svc_service_type = {'svc-service-type': app_lb_params}
    lb_backends = {'svc-servers': app_lb_params}

    def __init__(self, *args, **kwargs):
        super(AppmanagerInterface, self).__init__(*args, **kwargs)

    def __str__(self):
        return "appmanagerinterface"

    def api_req(self, method, path, resourcetype, body=None, **kwargs):
        if self.zmq_sock:
            if method == "POST":
                method = "add"
            elif method == "PUT":
                method = "modify"
            elif method == "GET":
                method = "get"
            elif method == "DELETE":
                method = "delete"
            return self.api_req_raw_zmq(method, resourcetype, body=body, **kwargs)
        else:
            if method == "POST":
                body = "object={" + body + "}"
            elif method == "PUT":
                body = "{" + body + "}"
            elif method == "DELETE":
                data = None
            return self.api_req_raw_http(method, path, body=body, **kwargs).json()

    def post(self, nsapp, app_payload):
        if nsapp.config_session:
            self.update(nsapp, app_payload)
        else:
            self.create(nsapp, app_payload)

    def create(self, nsapp, app_payload):
        try:
            url = self.appmanager_url
            resourcetype = self.appmanager_resourcetype
            payload = "\"managed_app\": " + json.dumps(app_payload)
            resp_json = self.api_req("POST", url, resourcetype, body=payload)
        except requests.exceptions.RequestException as e:
            logger.error('Create Application: Error while calling %s: %s', url, e)
            return
        try:
            nsapp.config_session = True
            logger.info('AppManagerInterface: Created app %s', nsapp.appName)
            #nsapp.target_device=resp_json["managed_app"][0]["app_netfuncs_config"][0]["netfunc_params"]["app_devices"][0]["device_mgmt_ip"]
            #if self.networking != None:
            #    self.networking.networking_place_vip(nsapp)
            if self.dns != None:
                nsapp.dns_domain = self.dns.dns_create_a_record(nsapp)
        except Exception as e:
            logger.error('Create Application: Error accessing device id in Appmanager payload %s', e)

    def update(self, nsapp, app_payload):
        try:
            url = self.appmanager_url
            resourcetype = self.appmanager_resourcetype
            payload = "\"managed_app\": " + json.dumps(app_payload)
            resp_json = self.api_req("PUT", url, resourcetype, body=payload)
        except requests.exceptions.RequestException as e:
            logger.error('Update Application: Error while calling %s: %s', url, str(e))
            return
        logger.info('AppManagerInterface: Created app %s', nsapp.appName)
        nsapp.config_session = True

    def delete(self, nsapp):
        try:
            url = self.appmanager_url + "?args=app_id:" + nsapp.appName
            resourcetype = self.appmanager_resourcetype
            payload = "\"managed_app\": {" + "\"app_id\": \"" + nsapp.appName + "\"}"
            self.api_req("DELETE", url, resourcetype, body=payload)
            self.nsapp_release_vip(nsapp)
            #self.networking.networking_release_vip(nsapp)
            if self.dns != None:
                self.dns.dns_delete_a_record(nsapp)
            logger.info('AppManagerInterface: Deleted app %s', nsapp.appName)
        except requests.exceptions.RequestException as e:
            logger.error('DELETE Application: Error while calling %s: %s', url, e)
            return

    def sync_ns_apps(self):
        try:
            url = self.appmanager_url
            resourcetype = self.appmanager_resourcetype
            payload = "\"managed_app\": [{}]"
            resp_json = self.api_req("GET", url, resourcetype, body=payload)
        except requests.exceptions.RequestException as e:
            logger.error('Get All Apps: Application: Error while calling %s: %s', url, e)
            return
        try:
            managed_apps = resp_json["managed_app"]
            for m_app in managed_apps:
                parameter_str = m_app["app_netfuncs_config"][0]["netfunc_params"]["parameters"]
                parameter = json.loads(parameter_str)
                ns_app = NetscalerApp(m_app["app_id"], m_app["container_manager"])
                #ns_app.target_device = m_app["app_netfuncs_config"][0]["netfunc_params"]["app_devices"][0]["device_mgmt_ip"]
                ns_app.vip = parameter["app-services"][0]["virtual-ip"]
                ns_app.vport = parameter["app-services"][0]["virtual-port"] 
                ns_app.config_session = True
                self.nsapps[ns_app.appName] = ns_app
        except ValueError as e:
            logger.error("Get All Apps: mananged_app is not decoded in proper json: %s", e)
        except Exception as e:
            logger.error("Get All Apps: managed_app unkwown decoding error")

    def sync_ns_cs_apps(self):
        try:
            url = self.appmanager_url
            resourcetype = self.appmanager_resourcetype
            payload = "\"managed_app\": [{}]"
            resp_json = self.api_req("GET", url, resourcetype, body=payload)
        except requests.exceptions.RequestException as e:
            logger.error('Get All Apps: Application: Error while calling %s: %s', url, e)
            return
        try:
            managed_apps = resp_json["managed_app"]
            for m_app in managed_apps:
                parameter_str = m_app["app_netfuncs_config"][0]["netfunc_params"]["parameters"]
                parameter = json.loads(parameter_str)
                ns_app = NetscalerCsApp(m_app["app_id"], m_app["container_manager"], m_app["lb_role"])
                ns_app.vip = parameter["cs-virtual-ip"]
                ns_app.vport = parameter["cs-virtual-port"]
                ns_app.mode = parameter['cs-service-type']
                ns_app.config_session = True
                self.nsapps[ns_app.appName] = ns_app
        except ValueError as e:
            logger.error("Get All Apps: mananged_app is not decoded in proper json: %s", e)
        except Exception as e:
            logger.error("Get All Apps: managed_app unkwown decoding error")
