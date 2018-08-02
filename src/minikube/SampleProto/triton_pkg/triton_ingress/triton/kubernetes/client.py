"""
K8s client. Deal with auth messiness
"""
import requests
import yaml
import posixpath
import base64
import logging
from requests.auth import HTTPBasicAuth
# To change SSL version
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.poolmanager import PoolManager
import ssl
import os.path

requests.packages.urllib3.disable_warnings()

logger = logging.getLogger('docker_netscaler')

class TLSAdapter(HTTPAdapter):
    '''An HTTPS Transport Adapter that uses an arbitrary SSL version.'''
    def __init__(self, ssl_version=None, **kwargs):
        self.ssl_version = ssl_version
        super(TLSAdapter, self).__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block = False):
        self.poolmanager = PoolManager(num_pools = connections, 
                                    maxsize = maxsize,
                                    block = block, 
                                    ssl_version=self.ssl_version)


class ApiPrefix:
    api_prefix = {
       "/services"  : "/api/v1",
       "/endpoints" : "/api/v1",
       "/ingresses" : "/apis/extensions/v1beta1",
       "/pods"      : "/api/v1",
    """ Openshift introduced resource """
       "/routes"    : "oapi/v1",
       "/secrets"   : "/api/v1"
    }

# Find out good place for these files
certs_directory = '/var/mps/tenants/root/ns_ssl_certs/'
keys_directory = '/var/mps/tenants/root/ns_ssl_keys/'
ca_cert_file = "triton-ca.cert"
client_cert_file = "triton-client.cert"
client_key_file = "triton-client.key"
service_account_path = "/var/run/secrets/kubernetes.io/serviceaccount/"

class K8sConfig(object):
    """
    Init from kubectl config
    """

    def __init__(self, filename):
        """
        Constructor

        :Parameters:
           - `filename`: The full path to the configuration file
        """
        self.filename = filename
        self.doc = None

    def parse(self):
        """
        Parses the configuration file.
        """
        try:
            with open(os.path.expanduser(self.filename)) as f:
                self.doc = yaml.safe_load(f.read())
            if "current-context" in self.doc and self.doc["current-context"]:
                current_context = filter(lambda x:
                                         x['name'] == self.doc['current-context'],
                                         self.doc['contexts'])[0]['context']
                username = current_context['user']
                clustername = current_context['cluster']
                self.user = filter(lambda x: x['name'] == username,
                                   self.doc['users'])[0]['user']
                self.cluster = filter(lambda x: x['name'] == clustername,
                                      self.doc['clusters'])[0]['cluster']
            return True
        except IOError:
            return False
        except yaml.YAMLError:
            return False


class K8sClient(object):
    """
    Client for interfacing with the Kubernetes API.
    """
    def __init__(self, kubeconf):
        """
        Creates a new instance of the HTTPClient.

        :Parameters:
           - `kubeconf`: dictionary that encapsulates kubernetes configuration and authentication parameters
        """
        self.cfg_file = kubeconf.get('kubeconfig')
        self.url = kubeconf.get('apiserver')
        self.ca = kubeconf.get('certificate-authority')
        self.ca_data = kubeconf.get('certificate-authority-data')
        self.client_cert = kubeconf.get('client-cert')
        self.client_cert_data = kubeconf.get('client-cert-data')
        self.client_key = kubeconf.get('client-key')
        self.client_key_data = kubeconf.get('client-key-data')
        self.username = kubeconf.get('username')
        self.password = kubeconf.get('password')
        self.token = kubeconf.get('token')
        self.insecure_skip_verify = kubeconf.get('insecure_skip_verify')
        
        if self.cfg_file:
            self.config = K8sConfig(self.cfg_file)
            if self.config.parse() == True:
                """ If both file and separate parameters are given, the individual parameters take precedents """
                if not self.url:
                    self.url = self.config.cluster.get('server')

                """ Cluster parameters:
                            - apiserver url
                            - CA verification either data is provided, or path to a file or insecure skip verify is set
                """
                if not self.ca:
                    self.ca = self.config.cluster.get('certificate-authority')
                if not self.ca_data:
                    self.ca_data = self.config.cluster.get('certificate-authority-data')
                """ insecure-skip-tls-verify is mutually exclusive with certificate-authority and
                    certficate-authority-data but still checking explicit setting """
                if self.insecure_skip_verify == None:
                    self.insecure_skip_verify = self.config.cluster.get('insecure-skip-tls-verify')

                """ Authenticaation parameters
                    See https://kubernetes.io/docs/admin/authentication/ for details
                    Any one or more authntication methods may be configured on the api server
                    At the moment the following authntication methods are supported:
                        - Client certs (configured on api server by supplying --client-ca-file)
                        - Basic with user and password (configured on api server by supplying --basic-auth-file)
                        - Tokens (configured on api server by supplying --token-auth-file)
                        - service accounts (mainly for CPX based triton, but service account token can be provided as token)
                """
                """ For client certs based authntication """
                if not self.client_cert:
                    self.client_cert = self.config.user.get('client-certificate')
                self.client_cert_data = self.config.user.get('client-certificate-data')
                if not self.client_key:
                    self.client_key = self.config.user.get('client-key')
                self.client_key_data = self.config.user.get('client-key-data')

                """ For Token based authentication """
                if not self.token:
                    self.token = self.config.user.get('token')

                """ For basic authntication """
                if not self.username:
                    self.username = self.config.user.get('username')
                if not self.password:
                    self.password = self.config.user.get('password')
            else:
                logger.error("Could not open or parse kubeconfig file %s", self.cfg_file)
        else:
            sa_file = service_account_path + "token"
            try:
                with open(sa_file, "r") as f:
                    self.token = f.read()
            except IOError as e:
                logger.error("Could not read serviceaccout token %s: %s", sa_file, e)
            sa_file = service_account_path + "ca.crt"
            if os.path.isfile(sa_file):
                self.ca = sa_file
        
        logger.info("Kubernetes parameters:")
        logger.info('\tCluster:')
        logger.info('\t\tAPI Server: %s', self.url)
        logger.info('\t\tInsecure Skip TLS verify: %s', str(self.insecure_skip_verify))
        logger.info('\t\tCA: %s', self.ca)
        logger.info('\t\tCA data: %s', self.ca_data)
        logger.info('\tUser:')
        logger.info('\t\tClient cert: %s', self.client_cert)
        logger.info('\t\tClient cert data: %s', self.client_cert_data)
        logger.info('\t\tClient key: %s', self.client_key)
        logger.info('\t\tClient key data: %s', self.client_key_data)
        logger.info('')
        logger.info('\t\tUsername: %s', self.username)
        logger.info('\t\tPassword: %s', self.password)
        logger.info('')
        logger.info('\t\tToken: %s',self.token)

        self.session = self.build_session()
    
    def figureoutcertdata(self, certdata):
        """ Certificate/key data may be supplied as base64 encoded or not 
            this function tries to guess the form of certdata and return plain data
        """
        if certdata.find("-BEGIN ") != -1 and certdata.find("-END ") != -1:
            """ Assuming clear text is given """
            return certdata
        """ Assuming 64base encoded is given """
        try:
            return base64.b64decode(certdata)
        except TypeError:
            return None

    def write_certkey_file(self,certkey_files,certkey_data):
        for cert_filename in certkey_files:
            try:
                with open(cert_filename, "w") as f:
                    f.write(certkey_data)
                return cert_filename
            except IOError:
                continue
        return None
        
    def build_session(self):
        """
        Creates a new session for the client.
        """
        client_cert = None
        client_key = None
        s = requests.Session()
        s.mount('https://', TLSAdapter())

        """ Skip verify has a precedence over ca or ca data 
            because. ca or ca data may still exist in the config but skip verify set:
            uncheked box in the GUI """
        if self.insecure_skip_verify == True:
            s.verify = False
        else:
            if self.ca:
                s.verify = self.ca
            elif self.ca_data:
                cert_data = self.figureoutcertdata(self.ca_data)
                if cert_data != None:
                    ca_cert = self.write_certkey_file((certs_directory + ca_cert_file, ca_cert_file), cert_data)
                    if ca_cert == None:
                        logger.info("Could not write ca file. Skipping CA verification")
                        s.verify = False
                    else:
                        s.verify = ca_cert
                else:
                    logger.error("Could not decode CA certificate data. Skipping CA verification")
                    s.verify = False
            else:
                s.verify = False

        if self.client_cert:
            client_cert = self.client_cert
        elif self.client_cert_data:
            cert_data = self.figureoutcertdata(self.client_cert_data)
            if cert_data != None:
                client_cert = self.write_certkey_file((certs_directory + client_cert_file, client_cert_file), cert_data)
                if client_cert == None:
                    logger.error("Could not write client certificate file. Access may be unathorized")
            else:
                logger.error("Could not decode client certificate data. Access may be unathorized")
        if self.client_key:
            client_key=self.client_key
        elif self.client_key_data:
            cert_data = self.figureoutcertdata(self.client_key_data)
            if cert_data != None:
                client_key = self.write_certkey_file((keys_directory + client_key_file, client_key_file), cert_data)
                if client_key == None:
                    logger.error("Could not write client key file. Access may be unathorized")
            else:
                logger.error("Could not decode client key data. Access may be unathorized")
        if client_cert != None and client_key != None:
            s.cert = (client_cert, client_key)

        # Python 2.6 requires index in format
        if self.token:
            s.headers["Authorization"] = "Bearer {0}".format(self.token)
        if self.username and self.password:
            s.auth=HTTPBasicAuth(self.username, self.password)
        return s

    def get_kwargs(self, **kwargs):
        """
        Creates a full URL to request based on arguments.

        :Parametes:
           - `kwargs`: All keyword arguments to build a kubernetes API endpoint
        """

        url  = kwargs.get("url", "")
        # Query with the first input. Eg. Use /endpoints/ from /endpoints/appname
        split = url.split('/')
        query = '/'.join(split[:2])
        bits = [ApiPrefix.api_prefix[query]]
        if "namespace" in kwargs:
            if kwargs["namespace"] != None:
                bits.extend(["namespaces", kwargs["namespace"],])
            kwargs.pop("namespace")

        if url.startswith("/"):
            url = url[1:]
        bits.append(url)
        kwargs["url"] = self.url + posixpath.join(*bits)
        return kwargs

    def request(self, *args, **kwargs):
        """
        Makes an API request based on arguments.

        :Parameters:
           - `args`: Non-keyword arguments
           - `kwargs`: Keyword arguments
        """
        return self.session.request(*args, **self.get_kwargs(**kwargs))

    def get(self, *args, **kwargs):
        """
        Executes an HTTP GET.

        :Parameters:
           - `args`: Non-keyword arguments
           - `kwargs`: Keyword arguments
        """
        return self.session.get(*args, **self.get_kwargs(**kwargs))
