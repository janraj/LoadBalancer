import argparse
import os
import json
import logging
import requests
import requests.exceptions
from triton.nsappinterface.netscalerapp import NetscalerApp
from triton.nsappinterface.netscalerapp import Framework

logger = logging.getLogger('docker_netscaler')

class MarathonInterface(object):
    """Interface for the Marathon REST API."""

    def __init__(self, server, netskaler, app_info,
                username=None, password=None, timeout=20):
        """Constructor

        :param server: Marathon URL (e.g., 'http://host:8080' )
        :param str username: Basic auth username
        :param str password: Basic auth password
        :param int timeout: Timeout (in seconds) for requests to Marathon
        """
        self.server = server
        self.netskaler = netskaler
        self.app_info = app_info
        self.auth = (username, password) if username and password else None
        self.timeout = timeout
        self.reinit_marathon = False

    def rest_api(self, method, path, **kwargs):
        path_str = os.path.join(self.server, 'v2')
        for path_elem in path:
            path_str = path_str + "/" + path_elem

        try:
            response = requests.request(
                method,
                path_str,
                headers={
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                auth=self.auth,
                timeout=self.timeout,
                **kwargs
            )
            logger.debug("%s %s", method, response.url)

            if response.status_code == 404:
                # Let's treat 404 as special as app might be about to
                # be deleted. So url will not be found.
                logger.warning("URL not found: {code}: {body}.".format(code=response.status_code, body=response.text))
                return response

            if response.status_code >= 300:
                logger.error('Got HTTP {code}: {body}'.
                             format(code=response.status_code, body=response.text))

            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error('Error while calling %s: %s', path_str, e.message)
            self.reinit_marathon = True
            return None
        return response

    def is_app_required(self, app):
        appId = app['id']
        if appId[1:] == os.environ.get("FRAMEWORK_NAME"):
            return False

        if app['labels'].get('NETSCALER_AS_APP', 'false').lower() == "true":
            return False

        if len(self.netskaler.groups) and 'NETSCALER_GROUP' in app['labels']:
            groups = app['labels']['NETSCALER_GROUP'].split(',')
            if not self.netskaler.is_group_needed(groups, self.netskaler.groups):
                return False
        elif self.app_info['apps']:
            if appId[1:] not in self.app_info['apps']:
                return False
        return True

    def is_task_required(self, app, ns_app, task):
        if len(task['host']) == 0:
            logger.warning("Ignoring Marathon task without host " +
                           task['id'])
            return False
        # If self.netskaler.framework_healthcheck is enabled then,
        # NS will respect marathon monitoring and will not do
        # monitoring itself. As part of respecting marathon monitoring
        # NS will not even add the services if Marathon Healthcheck is
        # not UP.
        if (self.netskaler.framework_healthcheck == True) and \
           'healthChecks' in app and \
            len(app['healthChecks']) > 0:
            if 'healthCheckResults' not in task:
                return False
            alive = True
            for result in task['healthCheckResults']:
                if not result['alive']:
                    alive = False
            if not alive:
                return False
        return True

    def is_netscaler_app(self, app):
        if app['labels'].get('NETSCALER_AS_APP', 'false').lower() == "true":
            return True
        if app['labels'].get('NETSCALER_AS_CPX', 'false').lower() == "true":
            return True
        return False

    def manage_ns_device(self, marathon_app):
        appname = marathon_app["id"][1:]
        if appname not in self.netskaler.devices:
            self.netskaler.devices[appname] = []

        oldtasks = set(self.netskaler.devices[appname])
        newtasks = set(task['id'] for task in marathon_app['tasks'])
        deletedtasks = oldtasks - newtasks
        for task in deletedtasks:
            self.netskaler.delete_ns_as_task(task)
        self.netskaler.devices[appname] = list(newtasks)

    def sync_ns_devices(self, marathon_apps):
        oldtasks = self.netskaler.sync_ns_devices()
        for marathon_app in marathon_apps:
            if self.is_netscaler_app(marathon_app):
                self.manage_ns_device(marathon_app)
        newtasks = set()
        for appname in self.netskaler.devices:
            newtasks = newtasks.union(set(self.netskaler.devices[appname]))
        deletedtasks = oldtasks - newtasks
        for task in deletedtasks:
            self.netskaler.delete_ns_as_task(task)

    def marathonapp_to_nsapps(self, marathon_app):
        ''' Each marathon app may consist of multiple NS Apps (for each servicePort)'''
        ns_apps = {}
        if self.is_app_required(marathon_app) != True:
            return ns_apps

        service_ports = marathon_app.get('ports')
        if (not service_ports) and ('ipAddress' in marathon_app) and\
                'discovery' in marathon_app['ipAddress'] and\
                'ports' in marathon_app['ipAddress']['discovery']:
            # Stragenly Marathon doesn't assign servicePort in IP per container
            # mode. Few choices here -->
            # 1. Pick port same as backend app. (bad!!)
            # 2. Assign Random free vserver port. (more bad!!)
            # 3. Assign port from 'NETSCALER_VIP' (not so bad!!!)
            # so if port is not there in app definition instead of
            # discarding app let's pick from choice 1.
            ports = marathon_app['ipAddress']['discovery']['ports']
            service_ports = [port['number'] for port in ports if 'number' in port]

        for i in range(len(service_ports)):
            servicePort = service_ports[i]
            appname = marathon_app["id"][1:].replace('/', '-') + '-' + str(servicePort)
            if appname in self.netskaler.nsapps:
                ns_app = self.netskaler.nsapps[appname]
                ns_app.clear_backends()
            else:
                ns_app = NetscalerApp(
                    appname, Framework.mesos_marathon)
                ns_app.servicePort = servicePort
                ns_app.nodePort = servicePort

            set_service_attributes_from_labels(marathon_app, ns_app, i)

            healthCheck = get_healthmonitor(marathon_app, i)
            if healthCheck:
                if healthCheck['protocol'].upper() == 'HTTP':
                    ns_app.mode = 'http'
                if self.netskaler.framework_healthcheck == True:
                    ns_app.healthmonitor = 'NO'
                    if 'skip_healthmonitor' not in ns_app.stylebook_additional_params:
                        ns_app.stylebook_additional_params['skip_healthmonitor'] = True

            ns_apps[servicePort] = ns_app

        for task in marathon_app['tasks']:
            if self.is_task_required(marathon_app, ns_app, task) != True:
                continue
            if marathon_app.get('ipAddress'):
                try:
                    task_ips = [x['ipAddress'] for x in task['ipAddresses']]
                    task_ports = [port['number'] for port in
                            marathon_app['ipAddress']['discovery']['ports']]
                    number_of_defined_ports = min(len(task_ports), len(service_ports))
                    for i in range(number_of_defined_ports):
                        task_port = task_ports[i]
                        service_port = service_ports[i]
                        ip = task_ips[i]
                        ns_app = ns_apps.get(service_port, None)
                        if ns_app:
                            ns_app.add_backend_member(ip, task_port)
                    logger.debug("IP per container mode task with IP %s %d"
                            %(ip, task_port))
                except Exception as e:
                    logger.error("Task doesn't have valid ipAddress section.")
            else:
                task_ports = task['ports']
                number_of_defined_ports = min(len(task_ports), len(service_ports))
                for i in range(number_of_defined_ports):
                    task_port = task_ports[i]
                    service_port = service_ports[i]
                    ns_app = ns_apps.get(service_port, None)
                    if ns_app:
                        ip = self.netskaler.resolve_ip(task['host'])
                        if ip is not None:
                            ns_app.add_backend_member(ip, task_port)

        if 'container' in marathon_app and marathon_app['container'] and\
                'docker' in marathon_app['container']:
            if 'portMappings' in marathon_app['container']['docker']:
                portmappings = marathon_app['container']['docker']['portMappings']
                if portmappings:
                    for p in portmappings:
                        service_port = p['servicePort']
                        ns_app = ns_apps.get(service_port, None)
                        if ns_app and ns_app.mode.lower() != 'http':
                            ns_app.mode = p['protocol']
            if 'network' in marathon_app['container']['docker']:
                ns_app.network_env = marathon_app['container']['docker']['network']

        if len(self.netskaler.groups) and 'NETSCALER_GROUP' in marathon_app['labels']:
            groups = marathon_app['labels']['NETSCALER_GROUP'].split(',')
            if not self.netskaler.is_group_needed(groups, self.netskaler.groups):
                return False

        for _,ns_app in ns_apps.iteritems():
            try:
                env=marathon_app['env']
                try:
                    ns_app.networking_app_data["enterprise"]=env["NUAGE-ENTERPRISE"]
                    ns_app.networking_app_data["domain"]=env["NUAGE-DOMAIN"]
                    ns_app.networking_app_data["zone"]=env["NUAGE-ZONE"]
                    ns_app.networking_app_data["subnet"]=env["NUAGE-NETWORK"]
                    ns_app.network_env = "NUAGE"
                except KeyError as e:
                    logger.info("Marathon application %s does not have complete nuage environment varaibles or nuage. %s" %(appname, e.message))
                    ns_app.networking_app_data={}
            except KeyError as e:
                logger.info("Marathon application %s does not have environment varaibles" %appname)
                ns_app.networking_app_data={}

            if not ns_app.config_session:
                ns_app.assign_application_vip_vport(self.netskaler)
            else:
                self.netskaler.nsapp_mark_vip_used(ns_app)
                ns_app.assign_application_vport(self.netskaler)
            logger.debug("VIP %s port %s for ns_app %s" %(ns_app.vip, ns_app.vport, ns_app.appName))
        return ns_apps

    def get_all_apps(self):
        response = self.rest_api('GET', ['apps'],
                                 params={'embed': 'apps.tasks'})
        if response == None:
            return
        marathon_apps = response.json()["apps"]
        logger.debug("got apps %s", [marathon_app["id"] for marathon_app in marathon_apps])

        self.sync_ns_devices(marathon_apps)
        self.netskaler.sync_ns_apps()
        oldapps = set(x for k,x in self.netskaler.nsapps.iteritems())
        newapps = set()
        for marathon_app in marathon_apps:
            ns_apps = self.marathonapp_to_nsapps(marathon_app)
            for k, ns_app in ns_apps.iteritems():
                self.configure_ns_for_app(ns_app)
                logger.debug("Collected vserver %s for %s" % (ns_app.appName, marathon_app["id"][1:]))
                newapps.add(ns_app)
        deletedapps = oldapps - newapps
        logger.debug("Need to delete %d apps" %len(deletedapps))
        for ns_app in deletedapps:
            self.netskaler.delete_nsapp(ns_app)

    def get_app(self, appid):
        response = self.rest_api('GET', ['apps', appid],
                                 params={'embed': 'apps.tasks'})
        if response == None:
            return
        if response.status_code == 404:
            self.delete_app(appid)
            return
        marathon_app = response.json()["app"]

        if self.is_netscaler_app(marathon_app):
            self.manage_ns_device(marathon_app)
            return

        ns_apps = self.marathonapp_to_nsapps(marathon_app)
        for k, ns_app in ns_apps.iteritems():
            self.configure_ns_for_app(ns_app)
            logger.debug("Collected vserver %s for %s" % (ns_app.appName, appid))

    def delete_app(self, appid):
        appid = appid.replace('/', '-')
        delete_nsapps = []
        for nsappname in self.netskaler.nsapps:
            if nsappname.rsplit('-', 1)[0] == appid:
                delete_nsapps.append(nsappname)
        for nsappname in delete_nsapps:
            ns_app = self.netskaler.nsapps[nsappname]
            self.netskaler.delete_nsapp(ns_app)
            logger.debug("Deleteing application %s" % ns_app.appName)

        if appid in self.netskaler.devices:
            self.netskaler.delete_ns_as_app(appid)

    def events(self):
        """Get event stream
           Requires Marathon v0.9. See:
           https://mesosphere.github.io/marathon/docs/rest-api.html#event-stream
        """
        path = 'v2/events'
        url = os.path.join(self.server, 'v2/events')
        headers = {'Content-Type': 'application/json',
                   'Accept': 'text/event-stream'}
        try:
            r = requests.get(url,
                             auth=self.auth,
                             headers=headers,
                             timeout=self.timeout,
                             stream=True)

            for line in r.iter_lines():
                if line and line.find("data:") > -1:
                    event = json.loads(line[line.find("data:") +
                                            len('data: '):].rstrip())
                    if event['eventType'] == 'status_update_event':
                        yield dict((k, event[k])
                                for k in ['appId', 'host', 'taskStatus', 'taskId'])
                        #yield {k: event[k]
                        #       for k in ['appId', 'host', 'taskStatus', 'taskId']}
                    elif event['eventType'] == 'app_terminated_event':
                        yield {'appId': event['appId'], 'appStatus': 'Terminated'}

        except requests.exceptions.RequestException as e:
            logger.error('Error while calling %s: %s', url, e.message)
            self.reinit_marathon = True
        except ValueError as e:
            logger.error("Value Error while decoding %s: %s", url, e.message)
            self.reinit_marathon = True
        except Exception as e:
            logger.error("Value Error while decoding %s: %s", url, e.message)
            self.reinit_marathon = True

        yield None

    def watch_all_apps(self):
        for ev in self.events():
            if self.netskaler.reinit_netscaler == True:
                return
            if self.reinit_marathon == True:
                return
            if ev is None:
                continue

            app = ev['appId']
            if ev.get('appStatus') == 'Terminated':
                self.delete_app(app[1:])
                continue

            host = ev['host']
            status = ev['taskStatus']
            relevant = status in ['TASK_RUNNING',
                                  'TASK_FINISHED',
                                  'TASK_FAILED',
                                  'TASK_KILLED',
                                  'TASK_LOST']
            if app is not None \
               and relevant:
                logger.info("Configuring NS for app %s, "
                            "host=%.12s status=%s" % (app, host, status))
                self.get_app(app[1:])

    def configure_ns_for_app(self, ns_app):
        self.netskaler.configure_nsapp(ns_app)

def get_healthmonitor(app, portIndex):
    for hc in app['healthChecks']:
        if 'portIndex' in hc and hc['portIndex'] == portIndex:
            return hc
    return None

def set_service_attributes_from_labels(marathon_app, ns_app, index):
    l = "NETSCALER_STYLEBOOK_" + str(index)
    if l in marathon_app['labels']:
        ns_app.stylebook = marathon_app['labels'][l]

    l = "NETSCALER_STYLEBOOK_PARAMS_" + str(index)
    try:
        if l in marathon_app['labels']:
            sb_param = json.loads(marathon_app['labels'][l])
            if isinstance(sb_param, dict):
                ns_app.stylebook_additional_params = sb_param
            else:
                raise ValueError("Stylebook Params not in Dictionatly Format")
    except ValueError as e:
        logger.error("Stylebook Additional Params for %s are not decoded in proper json: %s", ns_app.appName, e)

    l = "NETSCALER_LBMETHOD_" + str(index)
    if l in marathon_app['labels']:
        lbmethod = marathon_app['labels'][l].upper()
        if lbmethod in ['CALLIDHASH', 'DESTINATIONIPHASH', 'LEASTBANDWIDTH',
                'LEASTPACKETS', 'LEASTRESPONSETIME', 'ROUNDROBIN', 'SRCIPDESTIPHASH',
                'DOMAINHASH', 'LEASTCONNECTION', 'LEASTREQUEST', 'SOURCEIPHASH',
                'SRCIPSRCPORTHASH',  'URLHASH']:
            ns_app.lbmethod = lbmethod
            ns_app.stylebook_additional_params['algorithm'] = lbmethod

    l = "NETSCALER_PERSISTENCE_" + str(index)
    if l in marathon_app['labels']:
        persistence = marathon_app['labels'][l].upper()
        if persistence in ['DESTIP', 'SOURCEIP', 'SRCIPDESTIP']:
            ns_app.persistence = persistence
            ns_app.stylebook_additional_params['persistence'] = persistence

    l = "NETSCALER_SERVICETYPE_" + str(index)
    if l in marathon_app['labels']:
        st = marathon_app['labels'][l].upper()
        if st in ['HTTP', 'TCP', 'SSL', 'ANY', 'SSL_TCP', 'UDP', 'DNS']:
            ns_app.mode = st


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process Marathon args')
    parser.add_argument("--marathon-url", required=True, dest='marathon_url')

    result = parser.parse_args()

    # '{"appkey": "com.citrix.lb.appname", "apps": [{"name": "foo"},
    #  {"name": "bar"}]}'
    app_info = json.loads(os.environ['APP_INFO'])
    appnames = map(lambda x: x['name'], app_info['apps'])

    marathon = MarathonInterface(result.marathon_url)
    for app in appnames:
        endpoints = marathon.get_app_endpoints("/" + app)
        logger.info("Endpoints for app " + app + ": " + str(endpoints))

    for e in marathon.events():
        if e is not None and e in appnames:
            endpoints = marathon.get_app_endpoints(e)
            logger.info("Endpoints for app " + e + ": " + str(endpoints))
