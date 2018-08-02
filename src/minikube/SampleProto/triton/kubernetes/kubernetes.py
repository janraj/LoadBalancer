import argparse
import signal, os
import json
import logging
import requests.exceptions
from client import K8sClient
from threading import Thread
import threading
import time
import base64
from triton.nsappinterface.netscalerapp import NetscalerCsApp
from triton.nsappinterface.netscalerapp import NetscalerApp
from triton.nsappinterface.netscalerapp import Framework
from triton.kubernetes.client import ApiPrefix

# from pykube.config import KubeConfig
# from pykube.http import HTTPClient


logger = logging.getLogger('docker_netscaler')

class KubernetesInterface(object):
    """Interface for the Kubernetes REST API."""

    def __init__(self, netskaler, app_info,
                 kubernetesconf, main_thread_timeout=None):
        """Constructor

        :param NetscalerInterface netskaler: Netscaler object
        :param app_info : dictionary of app names
        :param kubernetesconfig : dictionary encapsulating kubernetes configuration and authentication parameters
        :param main_thread_timeout : timeout at which main thread is exiting to perform time related activities
        """
        self.netskaler = netskaler
        self.app_info = app_info
        self.client = K8sClient(kubernetesconf)
        self.watch_all_services = self.KubernetesServicesThread(self)
        self.watch_all_endpoints = self.KubernetesEndpointsThread(self)
        self.watch_all_ingresses = self.KubernetesIngressesThread(self)
        self.watch_all_pods = self.KubernetesPodsThread(self)
    # Openshift kubernetes version has routers resource for the ingress purpose.
        self.watch_all_routes = self.KubernetesRoutesThread(self)
        self.watch_all_secrets = self.KubernetesSecretsThread(self)
        self.events_lock = threading.Condition()
        # If a thread fails we need to stop all threads and start over
        # Once one thread gets exception, it should set variable to 
        # tell other threads to stop, main thread should also monitor the condition 
        # and wait for all threads to stop before reinitializing the process
        self.stop_thread = threading.Event()
        self.reinit_kubernetes = False
        self.done = False
        self.pending_services = {} # Services that are not yet created in the cluster but reffered to by ingress objects
        self.pending_secrets = {} # Secrets that are not yet created in the cluster but reffered to by ingress objects

        if str(netskaler)=="iptablesinterface":
            if main_thread_timeout:
                self.main_thread_timeout=main_thread_timeout
            else:
                self.main_thread_timeout=10
        else:
            self.main_thread_timeout=None
        
    class KubernetesServicesThread(Thread):
        """ Thread for watching for services (apps) """
            
        def __init__(self, controller):
            Thread.__init__(self)
            self.setDaemon(True)
            self.controller = controller
                                 
        def run(self):
            logger.info("Starting thread that watches services...")
            self.controller._events_watch('/services')
            
                        
    class KubernetesEndpointsThread(Thread):
        """ Thread for watching for endpoints """
        
        def __init__(self, controller):
            Thread.__init__(self)
            self.setDaemon(True)
            self.controller = controller
            
        def run(self):
            logger.info("Starting thread that watches endpoints...")
            self.controller._events_watch('/endpoints')
                        
    class KubernetesIngressesThread(Thread):
        """ Thread for watching for ingresses """

        def __init__(self, controller):
            Thread.__init__(self)
            self.setDaemon(True)
            self.controller = controller

        def run(self):
            logger.info("Starting thread that watches ingresses...")
            self.controller._events_watch('/ingresses')

    class KubernetesPodsThread(Thread):
        """ Thread for watching for pods to manage CPX pods """

        def __init__(self, controller):
            Thread.__init__(self)
            self.setDaemon(True)
            self.controller = controller

        def run(self):
            logger.info("Starting thread that watches pods...")
            self.controller._events_watch('/pods')

    class KubernetesSecretsThread(Thread):
        """ Thread for watching for secrets to manage ingress objects """

        def __init__(self, controller):
            Thread.__init__(self)
            self.setDaemon(True)
            self.controller = controller

        def run(self):
            logger.info("Starting thread that watches secrets...")
            self.controller._events_watch('/secrets')

    class KubernetesRoutesThread(Thread):
        """ Thread for watching for routes to manage ingress in Openshift """
        """ This is an Openshift introduced resources.
            Oficial kubernetes does not have this resources
            The resource serves the role for ingress information
            Listening on this resource to satisfy Openshift Environment
        """

        def __init__(self, controller):
            Thread.__init__(self)
            self.setDaemon(True)
            self.controller = controller

        def run(self):
            logger.info("Starting thread that watches Openshift reoutes...")
            self.controller._events_watch('/routes')

    def _get(self, api, namespace=None):
        try:
            response = self.client.get(url=api,
                                       namespace=namespace)
        except requests.exceptions.RequestException as e:
            logger.error('Error while calling  %s:%s', api, e.message)
            raise e
        if response.status_code == 200:
            success = True
        elif response.status_code == 401:
            logger.error("Reuqest to api server is not authorized")
            response.raise_for_status()
        elif response.status_code == 404:
            success = False
        else:
            logger.error('Got HTTP {code}: {body}'.
                         format(code=response.status_code, body=response.text))
            success = False
        return success, response
        
    def _events_watch(self, api):
        
        while self.stop_thread.is_set()==False:
            logger.info("Getting resource version for %s" %api[1:])
            try:
                success, response = self._get(api)
            except:
                break
            if not success:
                logger.error('Failed to get resource version. Thread exiting')
                time.sleep(10)
                continue
        
            message = response.json()
            resource_version = message['metadata']['resourceVersion']
            
            logger.info("Starting watching %s" %api[1:])
            for event_json in self._events(api, resource_version):
                if not event_json: 
                    break
                self.events_lock.acquire()
                self.events_queue.append(event_json)
                self.events_lock.notifyAll()
                self.events_lock.release()

        self.stop_thread.set()
        return

    def main_thread(self):
        logger.info("Starting watch threads...")
        self.stop_thread.clear()
        self.watch_all_services.start()
        self.watch_all_endpoints.start()
        self.watch_all_ingresses.start()
        self.watch_all_pods.start()
        self.watch_all_secrets.start()
        """ Openshift introduced resource """
#       self.watch_all_routes.start()

        self.events_queue = []
        try:
            while self.stop_thread.is_set()==False:
                self.events_lock.acquire()
                while len(self.events_queue) == 0:
                    self.events_lock.wait(self.main_thread_timeout)
                    if len(self.netskaler.waiting_apps)>0:
                        break
                if len(self.events_queue):
                    event = self.events_queue.pop(0)
                else:
                    event = None
                self.events_lock.release()
                if event:
                    self.event_handler(event)
                if len(self.netskaler.waiting_apps):
                    self.netskaler.configure_iptables_for_app()
        except Exception as e:
            logger.error("Main thread exits on exceotion %s", e)
            self.stop_thread.set()
        
        logger.info("Waiting for all threads to exit")
        while self.watch_all_services.is_alive() or \
              self.watch_all_endpoints.is_alive() or \
              self.watch_all_ingresses.is_alive() or \
              self.watch_all_pods.is_alive() or \
              self.watch_all_routes.is_alive() or \
              self.watch_all_secrets.is_alive():
            time.sleep(1)
        self.reinit_kubernetes = True
        return
        
        
    def event_handler(self, event):
        #logger.debug("Handling event %s for %s.%s of kind %s" %(event['type'],
        """logger.info("Handling event %s for %s.%s of kind %s" %(event['type'],
                     event['object']['metadata']['name'],
                     event['object']['metadata']['namespace'],
                     event['object']['kind']))
        """
        if event['object']['kind'] == 'Service':
            apps=[]
            a = self._fill_service(event['object'])
            if a == None:
                return
            apps.append(self._fill_service(event['object']))
            if event['type'] == 'ADDED':
                self.configure_cpx_for_apps(apps)
                self.update_ingress_for_services(apps)
            elif event['type'] == 'DELETED':
                self.unconfigure_cpx_for_apps(apps)
            elif event['type']  == 'MODIFIED':
                self.update_cpx_for_apps(apps)
            else:
                logger.warning("Received unrecognized event %s for the item %s.%s of type %s" %(event['type'],
                               event['object']['metadata']['name'], event['object']['metadata']['namespace'],
                               event['object']['kind']))
        elif event['object']['kind'] == 'Endpoints':
            if event['type'] == 'ADDED' or event['type'] == 'MODIFIED':
                self.configure_cpx_from_endpoints_event(event['object'])
            elif event['type'] == 'DELETED':
                logger.debug("Ignoring event %s for the item %s.%s of type %s" %(event['type'],
                             event['object']['metadata']['name'], event['object']['metadata']['namespace'],
                             event['object']['kind']))
            else:
                logger.warning("Received unrecognized event %s for the item %s.%s of type %s" %(event['type'],
                               event['object']['metadata']['name'], event['object']['metadata']['namespace'],
                               event['object']['kind']))
        elif event['object']['kind'] == 'Ingress':
            ingresses=[]
            ingresses.append(self.get_ingress(event['object']))
            if event['type'] == 'ADDED' or event['type']  == 'MODIFIED':
                self.configure_ingress_for_apps(ingresses)
            elif event['type'] == 'DELETED':
                self.unconfigure_ingress_for_apps(ingresses)
            else:
                logger.warning("Received unrecognized event %s for the item %s.%s of type %s" %(event['type'],
                               event['object']['metadata']['name'], event['object']['metadata']['namespace'],
                               event['object']['kind']))
        elif event['object']['kind'] == 'Pod':
            if self.is_ns_pod(event['object']):
                if event['type'] == 'MODIFIED':
                    logger.debug("Received MODIFIED event for cpx pod %s" %(event['object']['metadata']['name']))
                    if event['object']['metadata'].get('deletionTimestamp'):
                        if 'status' in event['object'] and 'conditions' in event['object']['status']:
                            for cond in event['object']['status']['conditions']:
                                if cond['type'] == 'Ready' and cond['status'] == "True":
                                    # Modified event indicating the beginning of the pod deletion process
                                    # the deletionTimeout is set and current condition->Ready is True
                                    # Start process of deleting cpx pod from MAS
                                    logger.debug("Received MODIFIED request with condition type Ready is True for cpx pod %s" %(event['object']['metadata']['name']))
                                    self.netskaler.delete_ns_as_task(event['object']['metadata']['name'])
                                    break
                elif event['type'] == 'DELETED':
                    logger.debug("Received DELETED event for cpx pod %s" %(event['object']['metadata']['name']))
                    #self.netskaler.delete_ns_as_task(event['object']['metadata']['name'])
                else:
                    logger.debug("Ignoring event %s for the item %s.%s of type %s" %(event['type'],
                             event['object']['metadata']['name'], event['object']['metadata']['namespace'],
                             event['object']['kind']))
        elif event['object']['kind'] == 'Secret':
            if event['type'] == 'ADDED':
                self.update_ingress_for_secrets_add(event['object'])
            elif event['type'] == 'DELETED':
                self.update_ingress_for_secrets_delete(event['object'])
            else:
                logger.debug("Ignoring event %s for the item %s.%s of type %s" %(event['type'],
                             event['object']['metadata']['name'], event['object']['metadata']['namespace'],
                             event['object']['kind']))
        else:
            logger.warning("Received unrecognized event %s for the item %s.%s of type %s" %(event['type'],
                           event['object']['metadata']['name'], event['object']['metadata']['namespace'],
                           event['object']['kind']))

    def get_backends_for_app(self, appid):
        """Get host endpoints for apps (services)

        :returns: list of endpoint (hostIp, port) tuples
        :rtype: list
        """
        backends = []
        api = '/services/' + appid
        success, response = self._get(api)
        if not success and response and response.status_code >= 300:
            status = response.json()
            if status['reason'] == 'NotFound':
                logger.info("Service %s not found" % appid)
        if not success:
            return backends
        svc = response.json()
        # node port is the backend port we need. Handle only 1 port for now
        nodePort = svc['spec']['ports'][0]['nodePort']  # TODO
        if nodePort == 0:
            logger.warn("Service %s does not have a node port" % appid)
            return backends
        # find the endpoint for the service so that we can find its pods
        api = '/endpoints'
        success, endpoints = self._get(api)
        if not success:
            return backends
        podnames = []
        if endpoints.status_code == 200:
            endpoints = endpoints.json()
            if endpoints['items'] == None:
                return backends
            for ep in endpoints['items']:
                if ep['metadata']['name'] == appid:
                    if ep['subsets']:
                        podnames = [addr['targetRef']['name']
                                    for addr in ep['subsets'][0]['addresses']]
                    break
        for p in podnames:
            api = '/pods/' + p
            success, response = self._get(api)
            if not success:
                continue
            pod = response.json()
            status = pod['status']['phase']
            host = pod['status']['hostIP']
            if status == 'Running':
                backends.append((host, nodePort))
        return list(set(backends))

    def _events(self, api, resource_version):
        """Get event stream for k8s endpoints
        """
      
        url = self.client.url +\
            ApiPrefix.api_prefix[api] +\
            "/watch" + api + \
            "?resourceVersion=%s&watch=true" %resource_version
        
        try:
            evts = self.client.session.request('GET', url,
                                                   stream=True)
            for e in evts.iter_lines():
                event_json = json.loads(e)
                yield event_json
                    
        except requests.exceptions.RequestException as re:
            logger.warning("Watching connection raised an exception: %s" %re.message)
        
        yield False

    def watch_all_apps(self):
        appnames = map(lambda x:  x['name'], self.app_info['apps'])
        api = '/endpoints'
        try:
            success, response = self._get(api)
        except:
            self.reinit_kubernetes = True
            return
        if not success:
            logger.error("Failed to watch for endpoint changes, exiting")
            return
        endpoints = response.json()
        resource_version = endpoints['metadata']['resourceVersion']
        while True:
            e = self._events(api, resource_version)
            if e:
                service_name = e['object']['metadata']['name']
                if service_name in appnames:
                    self.configure_ns_for_app(service_name)
            else:
                break
        self.reinit_kubernetes = True
        return
               
    def configure_ns_for_app(self, appname):
        try:
            backends = self.get_backends_for_app(appname)
        except:
            self.reinit_kubernetes = True
            return
        logger.info("Backends for %s are %s" % (appname, str(backends)))
        self.netskaler.configure_app(appname,  backends)

    def configure_ns_for_all_apps(self):
        appnames = map(lambda x:  x['name'], self.app_info['apps'])
        for app in appnames:
            self.configure_ns_for_app(app)

    def is_ns_pod(self,pod):
        if 'annotations' in pod['metadata'] and 'NETSCALER_AS_APP' in pod['metadata']['annotations']:
            if pod['metadata']['annotations']['NETSCALER_AS_APP'].lower() == 'true':
                return True
        return False

    def get_ns_pods(self):
        """ Used for getting all pods representing NS devices
            returns the set of pod names
        """
        api = '/pods/'
        success, response = self._get(api)
        nspods=set()
        if not success and response and response.status_code >= 300:
            status = response.json()
            if status['reason'] == 'NotFound':
                logger.info("Api %s not found" % api)
        if not success:
            return nspods
        
        status=response.json()
        
        if status['items'] != None:
            for pod in status['items']:
                if self.is_ns_pod(pod):
                    nspods.add(pod['metadata']['name'])
        return nspods
           
    def sync_ns_devices(self):
        mas_ns_devices=self.netskaler.sync_ns_devices()
        kube_ns_devices=self.get_ns_pods()
        deleted_devices=mas_ns_devices-kube_ns_devices
        for dev in deleted_devices:
            self.netskaler.delete_ns_as_task(dev)

    def get_backends_sb_params_from_annotations(self, object, index):
        if 'annotations' not in object['metadata']:
            return {}
        
        sb_param_name = 'NETSCALER_STYLEBOOK_PARAMS'
        if index != None:
            sb_param_name = sb_param_name + '_' + str(index)
        try:
            if sb_param_name in object['metadata']['annotations']:
                sb_param = json.loads(object['metadata']['annotations'][sb_param_name])
                if isinstance(sb_param, dict):
                    return sb_param
                else:
                    logger.error("Additional stylebook parameters are not dictionary for the resource %s %s.%s", object['kind'], object['metadata']['name'], object['metadata']['namespace'])
        except ValueError as e:
            logger.error("Stylebook Additional Params for the resource %s %s.%s are not decoded in proper json: %s", object['kind'], object['metadata']['name'], object['metadata']['namespace'], e)
        return {}

    '''For services every port creates a cs vserver, NETSCALER_STYLEBOOK_SERVICE_PARAMS
    are provided for cusomization of the cs vserver related to a service
    Lb vserver attached to the cs vserver as default pool is customized by NETSCALER_STYLEBOOK_PARAMS
    '''
    def get_service_sb_params_from_annotations(self, object, index):
        if 'annotations' not in object['metadata']:
            return {}
        
        sb_param_name = 'NETSCALER_STYLEBOOK_SERVICE_PARAMS'
        if index != None:
            sb_param_name = sb_param_name + '_' + str(index)
        try:
            if sb_param_name in object['metadata']['annotations']:
                sb_param = json.loads(object['metadata']['annotations'][sb_param_name])
                if isinstance(sb_param, dict):
                    return sb_param
                else:
                    logger.error("Additional stylebook parameters are not dictionary for the resource %s %s.%s", object['kind'], object['metadata']['name'], object['metadata']['namespace'])
        except ValueError as e:
            logger.error("Stylebook Additional Params for the resource %s %s.%s are not decoded in proper json: %s", object['kind'], object['metadata']['name'], object['metadata']['namespace'], e)
        return {}

    def set_service_sb_from_annotations(self, entity, dest, index):
        if 'annotations' not in entity['metadata']:
            dest['stylebook']=None
            dest['stylebook_params']={}
            dest['stylebook_service_params']={}
            return

        #Make global arrays of possible service type, persistence types, lb methods
        sb_name = 'NETSCALER_SERVICETYPE_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            par = entity['metadata']['annotations'][sb_name].lower()
            if par.upper() in ['TCP', 'HTTP', 'SSL', 'UDP', 'ANY', 'SSL_TCP']:
                dest['protocol'] = par

        sb_name = 'NETSCALER_LBMETHOD_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            par = entity['metadata']['annotations'][sb_name].lower()
            if par.upper() in ['ROUNDROBIN', 'LEASTCONNECTION', 'LEASTRESPONSETIME']:
                dest['lbmethod'] = par

        sb_name = 'NETSCALER_PERSISTENCE_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            par = entity['metadata']['annotations'][sb_name].lower()
            if par.upper() in ['NONE', 'COOKIEINSERT', 'SOURCEIP', 'SRCIPDESTIP', DESTIP]:
                dest['persistence'] = par

        sb_name = 'NETSCALER_STYLEBOOK_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            dest['stylebook'] = entity['metadata']['annotations'][sb_name]
        else:
            dest['stylebook'] = None
        
        dest['secret'] = {}
        sb_name = 'NETSCALER_SSL_CERTIFICATE_DATA_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            dest['secret']['cert'] = entity['metadata']['annotations'][sb_name]

        sb_name = 'NETSCALER_SSL_KEY_DATA_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            dest['secret']['key'] = entity['metadata']['annotations'][sb_name]

        sb_name = 'NETSCALER_SSL_CA_CERTIFICATE_DATA_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            dest['secret']['cacert'] = entity['metadata']['annotations'][sb_name]

        # Inspired by Openshift destination CA certificate parameter
        sb_name = 'NETSCALER_SSL_DESTINATION_CA_CERTIFICATE_DATA_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            dest['secret']['destcacert'] = entity['metadata']['annotations'][sb_name]

        sb_name = 'NETSCALER_SSL_TERMINATION_' + str(index)
        if sb_name in entity['metadata']['annotations']:
            par = entity['metadata']['annotations'][sb_name].lower()
            if par.upper() in ['EDGE', 'REENCRYPT']:
                dest['port_allowed'] = par
        
        dest['stylebook_params'] =  self.get_backends_sb_params_from_annotations(entity, index)
        dest['stylebook_service_params'] =  self.get_service_sb_params_from_annotations(entity, index)

    def get_ingress(self, ingress):
        """ Filling data from ingress json object """
        ing={'app_name':ingress['metadata']['name'], 'app_namespace': ingress['metadata']['namespace'],  'stylebook': None, 'stylebook_params': {}, 'app_ports': [{"app_port": 80, "protocol": "http", "port_allowed": True, "secret": None, 'stylebook_params':{}}], 'policies': {}}
        """ Ingress Vip selection process:
        1. Infrastructure allocated ingress IP
        2. The value of the NETSCALER_VIP in annotations
        3. The value of the NS_VIP environment variable passed while starting triton
        4. Resolving IP of the first host in the ingress spec
        If After the selection process vip remains to be $NS-VIP$, do not configure ingress
        on devices and wait till infrastructure provides it
        """
        vip=self.netskaler.nsvip
        ingressip=self._get_ingress_ip(ingress)
        if ingressip:
            vip=ingressip 

        tls_service=False
        if 'tls' in ingress['spec']:
            for tls in ingress['spec']['tls']:
                if 'secretName' in tls:
                    ing['app_ports'].append({'app_port':443, 'protocol': 'ssl', 'port_allowed': True, 'secret':tls['secretName'], 'stylebook_params':{}})
                    tls_service=True
                break

        if 'annotations' in ingress['metadata']:
            if 'NETSCALER_VIP' in ingress['metadata']['annotations']:
                vip=ingress['metadata']['annotations']['NETSCALER_VIP']
            
            """ Ingress object does not specify port explicitly, if not provided in annotations,
                default to 80, and secure to 443 """
            ing['app_ports'][0]['app_port']=int(ingress['metadata']['annotations'].get('NETSCALER_HTTP_PORT', 80))
            ing['app_ports'][0]['protocol']=ingress['metadata']['annotations'].get('NETSCALER_HTTP_SERVICETYPE', "http").lower()
            ing['app_ports'][0]['stylebook_params']=self.get_backends_sb_params_from_annotations(ingress, 'INSECURE')
            pa=ingress['metadata']['annotations'].get('NETSCALER_ALLOW_HTTP_PORT', "true").lower()
            if pa == "false":
                pa = False
            else:
                pa = True
            ing['app_ports'][0]['port_allowed']=pa
            if tls_service:
                ing['app_ports'][1]['app_port']=int(ingress['metadata']['annotations'].get('NETSCALER_SECURE_PORT', 443))
                ing['app_ports'][1]['protocol']=ingress['metadata']['annotations'].get('NETSCALER_SECURE_SERVICETYPE', "ssl").lower()
                ing['app_ports'][1]['stylebook_params']=self.get_backends_sb_params_from_annotations(ingress, 'SECURE')
            ing['stylebook']=ingress['metadata']['annotations'].get('NETSCALER_STYLEBOOK', None)
            ing['stylebook_params']=self.get_backends_sb_params_from_annotations(ingress, None)
        
        if 'backend' in ingress['spec']:
            backend=ingress['spec']['backend']
            policyname=self._get_lbname_details(backend['serviceName'], ing['app_namespace'], str(backend['servicePort'])) + ".svc"
            sb_params=self.get_backends_sb_params_from_annotations(ingress, 'DEFAULT')
            ing['policies']['default']={'rule': 'True', 'targetlbapp': policyname, 'stylebook_params': sb_params}
        if 'rules' in ingress['spec']:
            index=0
            for rule in ingress['spec']['rules']:
                host_expr=""
                if 'host' in rule:
                    if vip=="$NS-VIP$":
                        ingressip=self.netskaler.resolve_ip(rule['host'])
                        if ingressip:
                            vip=ingressip
                    
                    host_expr = 'HTTP.REQ.HOSTNAME.EQ("' + rule['host'] + '")'
                for path in rule['http']['paths']:
                    path_expr=""
                    if 'path' in path:
                        path_expr='HTTP.REQ.URL.PATH.STARTSWITH("' + path['path'] + '")'
                    
                    if host_expr != '' and path_expr != '':
                        expr=host_expr + " && " + path_expr
                    elif host_expr == '':
                        expr=path_expr
                    elif path_expr == '':
                        expr=host_expr
                    else:
                        expr="TRUE"

                    targetlbapp=self._get_lbname_details(path['backend']['serviceName'], ing['app_namespace'], str(path['backend']['servicePort'])) + '.svc'
                    policyname=self._get_lbname_details(ing['app_name'], ing['app_namespace'], str(ing['app_ports'][0]['app_port'])) + '.' + targetlbapp
                    sb_params=self.get_backends_sb_params_from_annotations(ingress, index)
                    ing['policies'][policyname]={'rule': expr, 'targetlbapp': targetlbapp, 'stylebook_params': sb_params}
                    index += 1
        
        ing['app_clusterip']=vip
        
        logger.debug("Processed ingress %s: " %ingress['metadata']['name'])
        logger.debug(json.dumps(ing))
        return ing
    
    def get_all_ingresses(self):
        """ Used for initial configuration of all the ingresses in kubernetes """
        api = '/ingresses/'
        success, response = self._get(api)
        ingresses=[]
        if not success and response and response.status_code >= 300:
            status = response.json()
            if status['reason'] == 'NotFound':
                logger.info("Api %s not found" % api)
        if not success:
            return ingresses

        status=response.json()

        if status['items'] != None:
            for ingress in status['items']:
                ingresses.append(self.get_ingress(ingress))
        return ingresses

            
    def get_service(self, app):
        """ Creating application from a application description given by kubernetes
            app - dcitionary that is result of kubernetes json blob, describing an application
        """
        
        a = self._fill_service(app)
        if a == None:
            return None
        appname = a['app_name']
                    
        api = '/endpoints/' + appname
        success, response = self._get(api, namespace=a['app_namespace'])
        if not success:
            logger.info("Failed to get endpoints list for the app %s" % appname)
            return a
        eps = response.json()
        if eps.get('subsets') != None:
            for ep in eps['subsets']:
                if 'addresses' in ep:
                    for port in ep['ports']:
                        if 'name' not in port: # Ports don't always have names
                            port['name'] = None
                        ep1=[]
                        for addr in ep['addresses']:
                            ep1.append({'ip':addr['ip'],'port_name':port['name'], 'port':port['port']})
                        a['app_endpoints'].append(ep1)
        return a
    
    def get_all_services(self):
        """ Used for initial configuration of all the services in kubernetes or
            creates array of applications in the form of array of dictionaries:
            [{app_name:<>, app_namespace:<>, app_clusterIP:<>, app_ports[(<port>, <protocol>, <targetPort>)], app_endpoints[[{ip:<>, port_name:<>, port:<>}]]
        """
        api = '/services/'
        success, response = self._get(api)
        apps=[]
        if not success and response and response.status_code >= 300:
            status = response.json()
            if status['reason'] == 'NotFound':
                logger.info("Api %s not found" % api)
        if not success:
            return apps
        
        status=response.json()
        
        if status['items'] != None:
            for app in status['items']:
                a = self.get_service(app)
                if a == None:
                    continue
                apps.append(a)
        return apps
    
    def kubernetes_service_to_nsapps(self, apps):
        """ Configuring cpx for apps received from kubernetes by the fucntion get_all_services """
        nsapps={}
        for app in apps:
            logger.info("Configuring cpx for application %s.%s", app['app_name'], app['app_namespace'])
            for p in app['app_ports']:
                lb_name = self._get_cs_name(app, p)

                if lb_name in self.netskaler.nsapps:
                    nsapp = self.netskaler.nsapps[lb_name]
                else:
                    nsapp = NetscalerCsApp(lb_name, Framework.kubernetes, "client")
                    
                nsapp.servicePort = p['port']
                nsapp.serviceIP = app['app_clusterip']
                nsapp.mode=p['protocol']
                nsapp.nodePort = p['nodePort']
            
                nsapp.ingress=app['ingress']
                
                if nsapp.config_session:
                    nsapp.occupy_application_vport(self.netskaler)
                else:
                    if nsapp.assign_application_vip_vport(self.netskaler) == False:
                        return nsapps

                lb_name = self._get_lb_name(app, p)
                nslbapp = NetscalerApp(lb_name, Framework.kubernetes)

                nsapp.ssl_termination = p.get('port_allowed')
                # Actual service type of the LB vip and service group will be determined 
                # during Nitro or managed app preparation based on CS vip service type and ssl_termination 
                # parameter.
                # This is because LB app is shared between service and ingress resource 
                # and may have different service types depending on situation.
                # For example in the default supported case, under ingress application LB vip
                # and servic egroup should be of type http, while under service application
                # service type of both entities should be tcp.
                # When different service type is requested by annotations or stylebook parameters 
                # things may become more complicated, therefore cs application determined service type
                # anf parameter ssl_termination alter it accordingly.
                nslbapp.mode = nsapp.mode 

                if 'lbmethod' in p:
                    nslbapp.lbmethod = p['lbmethod']
                if 'persistence' in p:
                    nslbapp.persistence = p['persistence']
                
                if p['stylebook']:
                    nsapp.stylebook = p['stylebook']
                nsapp.stylebook_additional_params = p['stylebook_service_params']
                
                if p.get('secret'):
                    cert_data = p['secret'].get('cert')
                    key_data = p['secret'].get('key')
                    if cert_data and key_data:
                        certkey = self.create_certkey_data(nsapp.appName, cert_data, key_data)
                        if certkey:
                            nsapp.certkey = certkey
                    cert_data = p['secret'].get('cacert')
                    if cert_data:
                        certkey = self.create_cert_data(nsapp.appName + '-ca', cert_data)
                        if certkey:
                            nsapp.cert_ca['default']  = (certkey,)
                    cert_data = p['secret'].get('destcacert')
                    if cert_data:
                        certkey = self.create_cert_data(nsapp.appName + '-dest-ca', cert_data)
                        if certkey:
                            nslbapp.cert_ca['default']  = (certkey,)
                    nsapp.sslCert = nsapp.appName
                    
                nsapp.policies['default']={'targetlbapp':nslbapp, 'stylebook_params':p['stylebook_params']}
                nslbapp.nsapps.append(nsapp)

                #srvrs=[]
                for ep in app['app_endpoints']:
                    for ep1 in ep:
                        if p['name'] != None and ep1['port_name'] != None:
                            if p['name'] == ep1['port_name']:
                                nslbapp.add_backend_member(ep1['ip'], ep1['port'])
                                #srvrs.append(ep1['ip'], ep1['port'])
                        elif ep1['port'] == p['targetPort']: # Numeric ports
                            nslbapp.add_backend_member(ep1['ip'], ep1['port'])
                            #srvrs.append((ep1['ip'], ep1['port']))
                        else:
                            break
                nsapps[nsapp.appName]=nsapp
                self.attach_service_to_ingress(nsapp)
        return nsapps

    def attach_service_to_ingress(self, nsapp):
        if nsapp.appName not in self.pending_services:
            return
        remove_svc=[]
        for ingapp in self.pending_services[nsapp.appName]:
            for _, pol in ingapp.policies.iteritems():
                if pol['targetservice'] == nsapp.appName:
                    pol['targetlbapp'] = nsapp.policies['default']['targetlbapp']
                    nsapp.policies['default']['targetlbapp'].nsapps.append(ingapp)
                    remove_svc.append(ingapp)
        for ingapp in remove_svc:
            self.pending_services[nsapp.appName].remove(ingapp)
        if len(self.pending_services[nsapp.appName]) != 0:
            logger.error("List of ingresses applications that bind %s must be 0 at this time, but it is %d", nsapp.appName, len(self.pending_services[nsapp.appName]))
        else:
            self.pending_services.pop(nsapp.appName)

    def insert_pending_service(self, nsapp, svc_name):
        if svc_name not in self.pending_services:
            self.pending_services[svc_name]=[]
            self.pending_services[svc_name].append(nsapp)
        else:
            if nsapp not in self.pending_services[svc_name]:
                self.pending_services[svc_name].append(nsapp)

    def configure_cpx_for_apps(self, apps):
        self.configure_ns_for_apps(self.kubernetes_service_to_nsapps(apps))
        
    def kubernetes_ingress_to_nsapps(self, ingress):
        nsapps={}
        """ Configuring ingress Netscaler for accessing the cluster """
        for ing in ingress:
            if ing['app_clusterip']=="$NS-VIP$":
                ing['app_clusterip']="$NS-VIP-NS$"
                logger.info("Ingress %s.%s has no specific VIP assigned, let MAS assign the NS device ip" %(ing['app_name'], ing['app_namespace']))
            
            for app_port in ing['app_ports']:
                logger.info("Configuring ingress netscaler for ingress %s.%s.%d" %(ing['app_name'], ing['app_namespace'], app_port['app_port']))

                if app_port['port_allowed'] == False:
                    logger.info("Skipping ingress %s.%s.%d: not allowed" %(ing['app_name'], ing['app_namespace'], app_port['app_port']))
                    continue
            
                cs_name = self._get_lbname_details(ing['app_name'], ing['app_namespace'], str(app_port['app_port']))
                if cs_name in self.netskaler.nsapps:
                    nsapp = self.netskaler.nsapps[cs_name]
                    nsapp.policies.clear()
                else:
                    nsapp = NetscalerCsApp(cs_name, Framework.kubernetes, "server")
                    nsapp.vport=app_port['app_port']
                    nsapp.mode=app_port['protocol'].lower()

                nsapp.ssl_termination = 'edge'
                
                if ing['stylebook']:
                    nsapp.stylebook=ing['stylebook']
                # If per port SB paramters are present, use them, otherwise use global
                # Per port parameters take precedence
                # We could create superset of both. will do if needed.
                if app_port['stylebook_params']:
                    nsapp.stylebook_additional_params=app_port['stylebook_params']
                else:
                    nsapp.stylebook_additional_params=ing['stylebook_params']

                nsapp.vip=ing['app_clusterip'] # Ingress IP may change

                if app_port['secret']:
                    self.handle_secret(nsapp, app_port['secret'], ing['app_namespace'])
                    nsapp.sslCert=self._get_lbname_details(app_port['secret'], ing['app_namespace'])

                priority=10
                priority_increment=5
                for polname, pol in ing['policies'].iteritems():
                    svc_name = pol['targetlbapp']
                    if svc_name in self.netskaler.nsapps:
                        # Service exits attach its lbapp to the policy
                        nslbapp = self.netskaler.nsapps[svc_name].policies['default']['targetlbapp']
                        if nsapp not in nslbapp.nsapps:
                            nslbapp.nsapps.append(nsapp)
                    else:
                        # Service does not exist yet, put it into pending list
                        nslbapp = None
                        self.insert_pending_service(nsapp, svc_name)

                    nsapp.policies[polname]={'rule': pol['rule'], 'targetservice': svc_name, 'targetlbapp': nslbapp, 'priority': priority, 'stylebook_params':pol['stylebook_params']}
                    priority += priority_increment
                logger.info("Policies of the app %s are", nsapp.appName)
                for polname, pol in nsapp.policies.iteritems():
                # Reusing svc_name for debug message
                    svc_name = polname + " " + pol['rule'] + " "
                    if pol['targetlbapp'] != None:
                        svc_name += pol['targetlbapp'].appName
                    else:
                        svc_name += "None"
                    logger.info(svc_name)
                nsapps[nsapp.appName]=nsapp
        return nsapps

    """ Handling secret for secure part of ingress application """
    def handle_secret(self, nsapp, secret_name, namespace):
        """lbname - fully qualified secret name"""
        lbname = self._get_lbname_details(secret_name, namespace)
        self.insert_pending_secret(nsapp, lbname)
        cert_key = self.get_secret(secret_name, namespace)
        if cert_key:
            self.attach_secret_to_ingress(nsapp, lbname, cert_key)
            
    def insert_pending_secret(self, nsapp, secret_name):
         if secret_name not in self.pending_secrets:
            self.pending_secrets[secret_name]=[]
            self.pending_secrets[secret_name].append(nsapp)
         else:
            if nsapp not in self.pending_secrets[secret_name]:
                self.pending_secrets[secret_name].append(nsapp)
    
    def attach_secret_to_ingress(self, ingapp, secret_name, cert_key):
        if secret_name not in self.pending_secrets:
            return
        if ingapp in self.pending_secrets[secret_name]:
            ingapp.certkey=cert_key
            self.pending_secrets[secret_name].remove(ingapp)
            """ configure ingress here """
        if len(self.pending_secrets[secret_name]) != 0:
            logger.error("List of ingresses applications that bind %s must be 0 at this time, but it is %d", secret_name, len(self.pending_secrets[secret_name]))
        else:
            self.pending_secrets.pop(secret_name)

    def get_secret(self, secretname, namespace):
        api = '/secrets/' + secretname
        success, response = self._get(api, namespace)
        if not success:
            logger.info("Failed to get secret for the app %s.%s", secretname, namespace)
            return None
        try:
            secret = response.json()
        except ValueError as e:
            logger.error("Could not parse secret data %s", e)
            return None
        
        if secret.get('code') == 404:
            logger.info("Secret %s.%s not found" %(secretname, namespace))
            return None
        
        return self.create_certkey_secret(secret)

    def create_certkey_secret(self, secret):
        if secret['type'] != 'kubernetes.io/tls' or 'data' not in secret or 'tls.crt' not in secret['data'] or 'tls.key' not in secret['data']:
            logger.info("Secret %s.%s is not properly formatted or not a tls type", secret['metadata']['name'], secret['metadata']['namespace'])
            return None
        secretname=self._get_lbname_details(secret['metadata']['name'], secret['metadata']['namespace'])
        cert_data = base64.b64decode(secret['data']['tls.crt'])
        key_data = base64.b64decode(secret['data']['tls.key'])
        return self.create_certkey_data(secretname, cert_data, key_data)

    def create_certkey_data(self, secretname, cert_data, key_data):
        cert = self.create_cert_data(secretname, cert_data)
        if cert == None:
            return None

        key = self.create_key_data(secretname, key_data)
        if key == None:
            return None
        return (cert, key)

    def create_cert_data(self, secretname, cert_data):
        try:
            cert_name = secretname + ".crt"
            cert_file = self.netskaler.certs_directory + cert_name
        except ValueError as e:
            return None
        try:
            with open(cert_file, "w") as f:
                f.write(cert_data)
        except IOError as e:
            logger.error("Could not create certificate file %s: %s", cert_file, e)
            return None
        return cert_name

    def create_key_data(self, secretname, key_data):
        try:
            key_name = secretname + ".key"
            key_file = self.netskaler.keys_directory + key_name
        except ValueError as e:
            return None
        try:
            with open(key_file, "w") as f:
                f.write(key_data)
        except IOError as e:
            logger.error("Could not create key file %s: %s", cert_file. e)
            return None
        return key_name

    def delete_certkey(self,secret):
        secretname=self._get_lbname_details(secret['metadata']['name'], secret['metadata']['namespace'])
        try:
            cert_name = secretname + ".crt"
            cert_file = self.netskaler.certs_directory + cert_name
        except ValueError as e:
            return None
        try:
            os.remove(cert_file)
        except OSError as e:
            pass
        try:
            key_name = secretname + ".key"
            key_file = self.netskaler.keys_directory + key_name
        except ValueError as e:
            return None
        try:
            os.remove(key_file)
        except OSError as e:
            pass
        return (cert_name, key_name)

    def configure_ingress_for_apps(self, ingresses):
        self.configure_ns_for_apps(self.kubernetes_ingress_to_nsapps(ingresses))

    """ apps here is representation of an app received from kubernetes event """
    def update_ingress_for_services(self, apps):
        for app in apps:
            logger.info("Updating ingress for application %s.%s", app['app_name'], app['app_namespace'])
            for p in app['app_ports']:
                cs_name = self._get_cs_name(app, p)
                lb_name = self._get_lb_name(app, p)
                csapp=self.netskaler.nsapps.get(cs_name)
                lbapp=self.netskaler.nslbapps.get(lb_name)
                if not lbapp or not csapp:
                    logger.error("Update ingress: couldn't find lb application %s and lb %s", cs_name, lb_name)
                    continue
                for nsapp in lbapp.nsapps:
                    if nsapp != csapp:
                        # csapp for the service has been just created, no need to issue update for it. 
                        logger.info("Updating ingress %s for the app %s", nsapp.appName, lbapp.appName)
                        self.configure_ns_for_single_app(nsapp)

    def update_ingress_for_secrets_add(self, secret):
        secret_name = self._get_lbname_details(secret['metadata']['name'], secret['metadata']['namespace'])
        certkey=self.create_certkey_secret(secret)
        if certkey == None:
            logger.error("Failed to create certkey for the secret %s", secret_name)
            return
        if secret_name not in self.pending_secrets:
            return
        for ingapp in self.pending_secrets[secret_name]:
            self.attach_secret_to_ingress(ingapp, secret_name, certkey)
            self.configure_ns_for_single_app(ingapp)
    
    def update_ingress_for_secrets_delete(self, secret):
        secret_name = self._get_lbname_details(secret['metadata']['name'], secret['metadata']['namespace'])
        # Unifficient way: iterating over all nsapps.
        # Optimization would be to create secret objects and refer to linked nsapps from it.
        for _, ingapp in self.netskaler.nsapps.iteritems():
            if ingapp.sslCert == secret_name:
                ingapp.certkey = None
                self.insert_pending_secret(ingapp, secret_name)
                self.configure_ns_for_single_app(ingapp)
#        self.delete_certkey(secret)

    """ ns_apps is the dictionary produced by :
    kubernetes_services_to_nsapps and
    kubernetes_ingress_to_nsapps
    """ 
    def configure_ns_for_apps(self, ns_apps):
        for _, nsapp in ns_apps.iteritems():
            self.configure_ns_for_single_app(nsapp)
     
    """ 
    nsapp object if class NetscalerApp
    """
    def configure_ns_for_single_app(self, nsapp):
        self.netskaler.configure_ns_cs_app(nsapp)

    def configure_cpx_for_all_apps(self):
        try:
            self.sync_ns_devices()
            apps = self.get_all_services()
            ingresses = self.get_all_ingresses()
        
            self.netskaler.sync_ns_cs_apps()
            oldapps = set(x for k,x in self.netskaler.nsapps.iteritems())
            newapps = set()

            nsapps=self.kubernetes_service_to_nsapps(apps)
            for _, app in nsapps.iteritems():
                self.configure_ns_for_single_app(app)
                newapps.add(app)
            nsapps=self.kubernetes_ingress_to_nsapps(ingresses)
            for _, app in nsapps.iteritems():
                self.configure_ns_for_single_app(app)
                newapps.add(app)
            deletedapps=oldapps-newapps
            logger.info("Need to delete %d apps" %(len(deletedapps)))
            for i in deletedapps:
                self.unconfigure_ns_for_single_app(i)
        except:
            self.reinit_kubernetes = True
            return


    def configure_cpx_from_endpoints_event(self, event):
        appname = event['metadata']['name']
        namespace = event['metadata']['namespace']
        #logger.info('Configuring new set of endpoints for app %s.%s', appname, namespace)
        if event.get('subsets') == None:
            return
        for ep in event['subsets']:
            if 'addresses' in ep:
                for port in ep['ports']:
                    if 'name' in port:
                        portbase=port['name']
                    else:
                        portbase=str(port['port'])
                    lbname = self._get_lbname_details(appname, namespace, portbase)
                    lbapp = self.netskaler.nslbapps[lbname]
                    if not lbapp:
                        logger.error("Cound't find lb app %s in configured apps", lbname)
                        continue

                    endpoints=[]
                
                    endpoints=[(addr['ip'], port['port']) for addr in ep['addresses']]
                    members = [(x.host, x.port) for x in lbapp.backends]
                    to_remove = list(set(members)-set(endpoints))
                    to_add = list(set(endpoints) - set(members))
                   
                    logger.debug("Updating endpoints to-add : to-remove")
                    logger.debug(to_add)
                    logger.debug(to_remove)
                    if to_add != None and to_remove != None:
                        for s in to_add:
                            lbapp.add_backend_member(s[0], s[1])
                        for s in to_remove:
                            lbapp.delete_backend_member(s[0], s[1])
                        self.adjust_service_group_for_single_app(lbapp)
                        
    def adjust_service_group_for_single_app(self,lbapp):
        for nsapp in lbapp.nsapps:
            logger.info("Adjusting application %s because of lb service %s", nsapp.appName, lbapp.appName)
            self.configure_ns_for_single_app(nsapp)
    
    def unconfigure_cpx_for_apps(self, apps):
        """ Removing apps from configuration """
        for app in apps:
            logger.info("Unconfiguring cpx for application %s.%s", app['app_name'], app['app_namespace'])
            for p in app['app_ports']:
                appname = self._get_cs_name(app, p) 
                lbname = self._get_lb_name(app, p)
                             
                if appname in self.netskaler.nsapps:
                    cs_app=self.netskaler.nsapps[appname]
                    lb_app=self.netskaler.nslbapps[lbname]
                    self.unconfigure_ns_for_single_app(cs_app)

                    lb_app.nsapps.remove(cs_app)
                    lb_app.clear_backends()
                    # Update all ingresses that might have included this service(lbapp)
                    for ingress in lb_app.nsapps:
                        for _, pol in ingress.policies.iteritems():
                            if pol['targetlbapp'] == lb_app:
                                pol['targetlbapp'] = None
                        self.configure_ns_for_single_app(ingress)
                        if appname in self.pending_services:
                            if ingress not in self.pending_services[appname]:
                                self.pending_services[appname].append(ingress)
                        else:
                            self.pending_services[appname]=[ingress]
                    del lb_app.nsapps[:]
                    self.netskaler.nslbapps.pop(lbname)
                else:
                    logger.warn('Application %s is not in the list of configured applications' %lbname)
                #self.delete_lb(lbname)
    
    def unconfigure_ingress_for_apps(self, ingresses):
        """ Removing ingress Netscaler """
        remove_svc=[]
        for ing in ingresses:
            for app_port in ing['app_ports']:
                logger.info("Unconfiguring ingress netscaler for ingress %s.%s.%d" %(ing['app_name'], ing['app_namespace'], app_port['app_port']))
                del remove_svc[:]
                cs_name = self._get_lbname_details(ing['app_name'], ing['app_namespace'], str(app_port['app_port']))
                if cs_name in self.netskaler.nsapps:
                    cs_app=self.netskaler.nsapps[cs_name]
                    self.unconfigure_ns_for_single_app(cs_app)
                    for _, pol in cs_app.policies.iteritems():
                        lb_app=pol['targetlbapp']
                        if lb_app != None:
                            lb_app.nsapps.remove(cs_app)
                    for key, svc in self.pending_services.iteritems():
                        if cs_app in svc:
                            svc.remove(cs_app)
                        if len(svc) == 0:
                            # The last waiting ingress is removed from the service's list
                            # Remove service key
                            remove_svc.append(key) 
                    for key in remove_svc:
                        del self.pending_services[key]

    def unconfigure_ns_for_single_app(self, nsapp):
        self.netskaler.delete_nsapp(nsapp)

    def update_cpx_for_apps(self, apps):
        if self.netskaler.nstype != "CPX":
            return
        
        import triton.nsappinterface.iptables
        """ Updating apps """
        for app in apps:
            logger.info("Modifying cpx for application %s.%s", app['app_name'], app['app_namespace'])
            for p in app['app_ports']:
                lbname = self._get_lb_name(app, p)
                             
                if lbname not in self.netskaler.nsapps:
                    logger.warn('Application %s is not in the list of configured applications' %lbname)
                    continue
                
                ingress_ip=app['ingress']
                app_ingress = self.netskaler.nsapps[lbname].ingress
                if  app_ingress == ingress_ip:
                    logger.info('No change in ingress ip for the app %s' %lbname)
                    continue
                
                logger.info('Ingress ip for the app %s changes to %s'  %(lbname, ingress_ip))
                if app_ingress == None:
                    app_ingress = self.netskaler.nsapps[lbname].ingress=ingress_ip
                    triton.nsappinterface.iptables.install_vserver_ingress_rules(self.netskaler.nsapps[lbname])
                elif ingress_ip == None:
                    triton.nsappinterface.iptables.delete_vserver_ingress_rules(self.netskaler.nsapps[lbname])
                else:
                    triton.nsappinterface.iptables.delete_vserver_ingress_rules(self.netskaler.nsapps[lbname])
                    app_ingress = self.netskaler.nsapps[lbname].ingress=ingress_ip
                    triton.nsappinterface.iptables.install_vserver_ingress_rules(self.netskaler.nsapps[lbname])

    """ ingress here is filled up representation of the object """
    def update_ingress_app(self, ingress):
        for ing in ingress:
            logger.info("Modifying ingress application %s.%s", ing['app_name'], ing['app_namespace'])
            cs_name = self._get_lbname_details(ing['app_name'], ing['app_namespace'], str(ing['app_port']))
            if cs_name not in self.netskaler.nsapps:
                self.configure_ingress_for_apps(ing)
            else:
                csapp=self.netskaler.nsapps[cs_name]
                if ing['cluster_ip'] != "$NS-VIP$":
                    if csapp.vip != ing['cluster_ip']:
                        csapp.vip = ing['cluster_ip']
                        logger.info("Update ingress ip %s app vip %s for %s.%s", ing['cluster_ip'], csapp.vip, ing['app_name'], ing['app_namespace'])
                    else:
                        logger.info("Ingress did not change: ip %s app vip %s for %s.%s", ing['cluster_ip'], csapp.vip, ing['app_name'], ing['app_namespace'])

    """ private methods """
    def  _fill_service(self, app):

        if 'clusterIP' not in app['spec'] or app['spec']['clusterIP'] == 'None':
             return None

        a={'app_name':app['metadata']['name'], 'app_namespace':app['metadata']['namespace'],
           'app_clusterip':app['spec']['clusterIP'], 'app_ports':[], 'app_endpoints':[]}
        
        a['ingress']=self._get_ingress_ip(app)
        index=0
        for port in app['spec']['ports']:
            if  'name' not in port:
                port['name'] = None
            if 'nodePort' not in port:
                port['nodePort'] = None
            if 'protocol' not in port:
                port['protocol']='tcp'
            else:
                port['protocol'] = port['protocol'].lower()
            self.set_service_sb_from_annotations(app, port, index)
            a['app_ports'].append(port)
            index+=1
        
        return a

    def _get_lb_name(self, app_info, port_info):
        
        # Services may be multiport, but the same port number may only
        # exist for different protocols, for example DNS service has 
        # to ports both 53, but one port of protocol udp and another of protocol tcp.
        # Since ingress object do not specify which protocol's' port number of the service
        # should be connected, but we know it is http, which is tcp, the connection should be created
        # based on port numbers so that we cannot name tcp lb vserver by port's name'
        # to solve the "same name" problem in case of multiport services, we name 
        # DNS services using port name or protocol as we do not need to connected such lb vservers to
        # ingress objects. 
        
        # For UDP objects use port name (must be present in multiport situations) or port number and protocol
        if port_info['name'] == None:
            port = str(port_info['targetPort'])
        else:
            port = str(port_info['name'])
        return self._get_lbname_details(app_info['app_name'], app_info['app_namespace'], port)

    def _get_cs_name(self, app_info, port_info):
        # Services may be multiport, but the same port number may only
        # exist for different protocols, for example DNS service has 
        # two ports both 53, but one port of protocol udp and another of protocol tcp.
        # Since ingress objects do not specify which protocol's port number of the service
        # should be connected, but we know it is http, which is tcp, the connection should be created
        # based on port numbers so that we cannot name tcp lb vserver by port's name'
        # to solve the "same name" problem in case of multiport services, we name
        # DNS services using port name or protocol as we do not need to connected such lb vservers to
        # ingress objects. 
        # In order to have cluster internal service on http, protocol provided by kubernetes (tcp)
        # may be altered via annotations, but ingress would not know about it.
        # Make special case for udp, for any other use port number 
        if port_info['protocol']=='udp':
            # For UDP objects use port name (must be present in multiport situations) or port number and protocol
            if port_info['name'] == None:
                port = str(port_info['port']) + '.' + port_info['protocol'] 
            else:
                port = str(port_info['name'])
        else:
            port = str(port_info['port'])
        
        return self._get_lbname_details(app_info['app_name'], app_info['app_namespace'], port) + '.svc'
        
    def _get_lbname_details(self, name_base, name_space, port_str=None):
        lbname = name_base + '.' + name_space
        if port_str:
            lbname = lbname + '.' + port_str
        return lbname

    def _get_ingress_ip(self, app):
        if 'status' in app and 'loadBalancer' in app['status'] and 'ingress' in app['status']['loadBalancer'] and\
           'ip' in app['status']['loadBalancer']['ingress'][0]:
            return app['status']['loadBalancer']['ingress'][0]['ip']
        return None
    
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description='Process Kubernetes args')
    parser.add_argument("--kubeconfig", required=False, dest='cfg')
    parser.add_argument("--token", required=False, dest='token')
    parser.add_argument("--server", required=False, dest='server')
    parser.add_argument("--insecure-tls-verify", required=False,
                        dest='insecure')

    result = parser.parse_args()

    # '{"appkey": "com.citrix.lb.appname", "apps": [{"name": "foo"},
    #  {"name": "bar"}]}'
    app_info = json.loads(os.environ['APP_INFO'])
    appnames = map(lambda x: x['name'], app_info['apps'])

    kube = KubernetesInterface(netskaler=None, app_info=app_info,
                               cfg_file=result.cfg, insecure=True)
    for app in appnames:
        endpoints = kube.get_backends_for_app(app)
        logger.info("Endpoints for app " + app + ": " + str(endpoints))
    kube.watch_all_apps()
