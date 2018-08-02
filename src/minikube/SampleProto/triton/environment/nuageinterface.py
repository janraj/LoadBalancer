import argparse
import logging
from netaddr import IPAddress
from networkinginterface import NetworkingInterface
try:
    from vspk import v3_2 as vsdk
except ImportError: 
    from vspk.vsdk import v3_2 as vsdk

from bambou.exceptions import BambouHTTPError
import json
#from vspk.vsdk import v3_1 as vsdk31
#from vspk.vsdk import v3_2 as vsdk32

logger = logging.getLogger('docker_netscaler')

class NuageInterface(NetworkingInterface):
    def __init__(self, nuage_user, nuage_password, nuage_enterprise, nuage_url, vip_subnet):
        self.nuage_url=nuage_url
        self.nuage_enterprise=nuage_enterprise
        self.nuage_user=nuage_user
        self.nuage_password=nuage_password
        self.nuage_vip_subnet=vip_subnet
        self.nuage_session=None
        logger.debug("Configured Nuage with username %s, password %s, enterprise %s, api_url %s, vip_subnet %s"
        %(self.nuage_user, self.nuage_password, self.nuage_enterprise, self.nuage_url, self.nuage_vip_subnet))
        return

    def _ns_nuage_login(self):
        session=vsdk.NUVSDSession(username=self.nuage_user, password=self.nuage_password, enterprise=self.nuage_enterprise, api_url=self.nuage_url)
        try:
            session.start()
            self.nuage_success=True
            self.session_user=session.user
            self.session_user.enterprises.fetch()
        except BambouHTTPError as e:
            logger.error("Failed to start Nuage session for Enterprise: %s; User: %s; Password: %s; Nuage url: %s. HTTP ErrorCode %d"
                                     %(self.nuage_enterprise, self.nuage_user, self.nuage_password, self.nuage_url, e.connection.response.status_code))
            self.nuage_success=False
            return None
        return session
       
    def _ns_nuage_is_success(self):
        return self.nuage_success
        
    def _ns_nuage_create_static_route(self, domain, subnet, netmask, gateway, enterprise=None):
        if enterprise == None:
            enterprise=self.nuage_enterprise

        try:
            ent_filter="name == '" + enterprise + "'"
            e=self.session_user.enterprises.get(filter=ent_filter)
            if len(e) < 1:
                logger.info("No enterprise with the name %s found" %enterprise)
                return True
            e=e[0]
            ent_filter="name == '" + domain + "'"
            d=e.domains.get(filter=ent_filter)
            if len(d) < 1:
                logger.info("No domains with the name %s found under %s" %(domain, enterprise))
                return True

            d=d[0]
            sr=vsdk.NUStaticRoute()
            sr.address=subnet
            sr.netmask=netmask
            for nsip in gateway:
                sr.next_hop_ip=nsip
                d.create_child(sr)
            return True
                
        except BambouHTTPError as e:
                logger.error("Failed to create static route %s %s %s on %s:%s HTTP ErrorCode: %d"
                             %(subnet, netmask, gateway, enterprise, domain, e.connection.response.status_code))
        return False
                

    def _ns_nuage_delete_static_route(self, domain, subnet, netmask, gateway, enterprise=None):
        if enterprise == None:
            enterprise=self.nuage_enterprise
        
        try:
            ent_filter="name == '" + enterprise + "'"
            e=self.session_user.enterprises.get(filter=ent_filter)
            if len(e) < 1:
                logger.info("No enterprise with the name %s found" %enterprise)
                return True
            e=e[0]
            ent_filter="name == '" + domain + "'"
            d=e.domains.get(filter=ent_filter)
            if len(d) < 1:
                logger.info("No domains with the name %s found under %s" %(domain, enterprise))
                return True
            d=d[0]

            srs=d.static_routes.get()

            for sr in srs:
                if sr.address == subnet and sr.netmask == netmask and (sr.next_hop_ip in gateway):
                    sr.delete()
                    continue
            return True
                                
        except BambouHTTPError as e:
                logger.error("Failed to delete static route %s %s %s on %s:%s HTTP ErrorCode: %d"
                             %(subnet, netmask, gateway, enterprise, domain, e.connection.response.status_code))
        return False

    def _ns_nuage_get_subnet_ips(self, domain, zone, subnet, enterprise=None):
        if enterprise == None:
            enterprise=self.nuage_enterprise
        
        ips=[]
        try:
            s=self._ns_nuage_get_subnet(enterprise, domain, zone, subnet)
            if s == None:
                return ips

            vmints=s.vm_interfaces.get()
            if len(vmints) == 0:
                logger.info("No VM interfaces found in subnet %s under %s:%s:%s" %(subnet, enterprise, domain, zone))
                return []

        except BambouHTTPError as e:
            logger.error("Failed to retrieve vminterfaces. HTTP ErrorCode: %d" %(e.connection.response.status_code))
            return []
        
        for vmint in vmints:
            ips.append(vmint.ip_address)

        return ips

    def _ns_nuage_get_enterprise(self, enterprise):
        ent=None
        for i in (0, 1):
            for ent in self.session_user.enterprises:
                if ent.name == enterprise:
                    return ent
            """ Didn't find enterprise in the cache
                Lets try to get new set, may be, something has changed
            """
            if self.nuage_enterprise == "csp" and i == 0:
                try:
                    self.session_user.enterprises.fetch()
                except BambouHTTPError as e:
                    logger.error("Failed to fetch enterprises. HTTP ErrorCode %d", e.connection.response.status_code)
                    return None
            else:
                logger.info("No enterprise %s found" %enterprise)
                return None
            
    def _ns_nuage_get_domain(self, enterprise, domain):
        ent=self._ns_nuage_get_enterprise(enterprise)
        if ent == None:
            return None

        for i in (0, 1):
            for dom in ent.domains: 
                if dom.name == domain:
                    return dom
            
            """ Didn't find domain in the cache
                Lets try to get new set, may be, something has changed
            """
            if i == 0:
                try:
                    ent.domains.fetch()
                except BambouHTTPError as e:
                    logger.error("Failed to fetch domains in neterprise %s. HTTP ErrorCode %d" %(enterprise, e.connection.response.status_code))
                    return None
            else:
                logger.info("No domain %s found in the enterprise %s" %(domain, enterprise))
                return None

    def _ns_nuage_get_zone(self, enterprise, domain, zone):
        ent=self._ns_nuage_get_enterprise(enterprise)
        if ent == None:
            return None
        dom=self._ns_nuage_get_domain(enterprise, domain)
        if dom == None:
            return None

        for i in (0, 1):
            for zon in dom.zones: 
                if zon.name == zone:
                    return zon
            
            """ Didn't find zone in the cache
                Lets try to get new set, may be, something has changed
            """
            if i == 0:
                try:
                    dom.zones.fetch()
                except BambouHTTPError as e:
                    logger.error("Failed to fetch zones in the domain %s:%s. HTTP ErrorCode %d" %(enterprise, domain, e.connection.response.status_code))
                    return None
            else:
                logger.info("No zone %s found in the domains %s:%s" %(zone, enterprise, domain))
                return None

    def _ns_nuage_get_subnet(self, enterprise, domain, zone, subnet):
        ent=self._ns_nuage_get_enterprise(enterprise)
        if ent == None:
            return None
        dom=self._ns_nuage_get_domain(enterprise, domain)
        if dom == None:
            return None
        zon=self._ns_nuage_get_zone(enterprise, domain, zone)
        if zon == None:
            return None

        for i in (0, 1):
            for sub in zon.subnets: 
                if sub.name == subnet:
                    return sub
            
            """ Didn't find subnet in the cache
                Lets try to get new set, may be, something has changed
            """
            if i == 0:
                try:
                    zon.subnets.fetch()
                except BambouHTTPError as e:
                    logger.error("Failed to fetch subnets in the domain %s:%s:%s. HTTP ErrorCode %d" %(enterprise, domain, zone, e.connection.response.status_code))
                    return None
            else:
                logger.info("No subnet %s found in the domains %s:%s:%s" %(subnet, enterprise, domain, zone))
                return None

    def _ns_nuage_get_subnet_ip_mask(self, domain, zone, subnet, enterprise=None):
        if enterprise == None:
            enterprise=self.nuage_enterprise

        sub=self._ns_nuage_get_subnet(enterprise, domain, zone, subnet)
        if sub == None:
            return None, None, None

        return sub.address, sub.netmask, sub.gateway

    def _ns_nuage_get_subnet_ip_range(self, domain, zone, subnet, enterprise=None):
        if enterprise == None:
            enterprise=self.nuage_enterprise

        ipranges={}
        
        ip, mask, gateway=self._ns_nuage_get_subnet_ip_mask(domain, zone, subnet, enterprise)
        if ip == None or mask == None or gateway == None:
            return ipranges

        try:
            mask=IPAddress(mask)
            if mask.is_netmask() != True:
                return ipranges
            bits=0
            ipi=int(mask)
            for i in range(0,31):
                if ipi>>i & 1 == 0:
                    bits+=1
                else:
                    break
            size=2**bits-3 # Now many ips can be used
            
            #ipranges["subnet"]=enterprise + ":" + domain + ":" + zone + ":" + subnet
            ipranges["ipfrom"]=str(IPAddress(ip)+1)
            #ipranges["ipto"]=str(IPAddress(ip)+size)
            ipranges["numips"]=size
            ipranges["exclude"]=[gateway]
            return ipranges
        except:
            logger.info("Data is not valid %s %s %s" %(ip, mask, gateway))
            return {}

    # Public methods
    def networking_modify_vip_route(self, op, vip, nsips, appname, appdata):
        logger.info("%s: vip route %s on system %s for %s with app_net_data %s..." %(op, vip, str(nsips), appname, appdata))
        app_net_data = json.loads(appdata)
        if self.nuage_session == None:
            self._ns_nuage_login()
        if self._ns_nuage_is_success() == True:
            if op == "ADD":
                return self._ns_nuage_create_static_route(app_net_data["domain"], vip, "255.255.255.255", nsips, app_net_data["enterprise"])
            elif op == "DELETE":
                return self._ns_nuage_delete_static_route(app_net_data["domain"], vip, "255.255.255.255", nsips, app_net_data["enterprise"])
        return False

    def networking_place_vip(self, ns_app):
        logger.debug("Placing vip %s on system %s for %s..." %(ns_app.vip, ns_app.target_device, ns_app.appName))
        if ns_app.network_env != "NUAGE":
            logger.error("Application %s does not nuage environmet" %ns_app.appName)
        if self.nuage_session == None:
            self._ns_nuage_login()
        if self._ns_nuage_is_success() == True:
            nsips = [ns_app.target_device]
            return self._ns_nuage_create_static_route(ns_app.networking_app_data['domain'], ns_app.vip, "255.255.255.255", nsips, ns_app.networking_app_data["enterprise"])
        return

    def networking_release_vip(self, ns_app):
        logger.debug("Releasing vip %s for %s..." %(ns_app.vip, ns_app.appName))
        if ns_app.network_env != "NUAGE":
            logger.error("Application %s does not nuage environmet" %ns_app.appName)
            return
        if self.nuage_session == None:
            self._ns_nuage_login()
        if self._ns_nuage_is_success() != True:
            logger.error("Failed to login into nuage")
            return
        if ns_app.target_device == False:
            logger.debug("Target device is not specified for app %s" %ns_app.appName)
        else:
            nsips = [ns_app.target_device]
            self._ns_nuage_delete_static_route(ns_app.networking_app_data['domain'], ns_app.vip, "255.255.255.255", nsips, ns_app.networking_app_data["enterprise"])
            logger.debug("Removed static route to the vip %s for the app %s" %(ns_app.vip, ns_app.target_device))
        return

    def networking_is_success(self):
        return self._ns_nuage_is_success()

    def networking_get_app_endpoints(self, ns_app):
        """ Returns a list of endpoints [ep1, ep2,...] 
            Application name matches subnet name
        """
        logger.debug("Getting endpoints for %s..." %ns_app.appName)
        bes=[]
        if ns_app.network_env != "NUAGE":
            logger.error("Application %s does not nuage environmet" %ns_app.appName)
            return bes

        if self.nuage_session == None:
            self._ns_nuage_login()

        if self._ns_nuage_is_success() == True:
            attempts=0
            while ns_app.backends_count != len(bes) and attempts < 5:
                bes=self._ns_nuage_get_subnet_ips(ns_app.networking_app_data['domain'], ns_app.networking_app_data['zone'],
                    ns_app.networking_app_data['subnet'], ns_app.networking_app_data['enterprise'])
                attempts+=1
        logger.debug("Backends for %s are:" %ns_app.networking_app_data['subnet'])
        return bes 

    def networking_get_vips(self, ns_app):
        """ Returns information on vips in the form of dictionary:
            {'subnet': <subnet name>, 'ipfrom': <first ip in the range>, 'ipto': <last ip in the range>, 'do_not_use_ips':[<ips that cannot be used, for example gateway ip>]}
        """
        logger.debug("Getting vips for %s..." %ns_app.appName)
        if ns_app.network_env != "NUAGE":
            logger.error("Application %s does not nuage environmet" %ns_app.appName)
            return {}

        if self.nuage_session == None:
            self._ns_nuage_login()
        if self._ns_nuage_is_success() == True:
            return self._ns_nuage_get_subnet_ip_range(ns_app.networking_app_data['domain'], ns_app.networking_app_data['zone'],
       	                    self.nuage_vip_subnet, ns_app.networking_app_data['enterprise'])
        logger.error("Nuage is not successful")
        return {}

    def networking_create_vips_label(self, ns_app):
        if ns_app.network_env != "NUAGE":
            return ""
        return ns_app.networking_app_data['enterprise'] + ":" + ns_app.networking_app_data['domain'] + ":" + ns_app.networking_app_data['zone'] + ":" + self.nuage_vip_subnet

if __name__ == "__main__":
        
    logging.basicConfig(level=logging.CRITICAL,
            format='%(asctime)s  - %(levelname)s - [%(filename)s:%(funcName)-10s]  (%(threadName)s) %(message)s')
    logger = logging.getLogger('docker_netscaler')
    logger.addFilter(logging.Filter('docker_netscaler'))
    logger.setLevel(logging.DEBUG)

    parser = argparse.ArgumentParser(description='Process Kubernetes args')
    parser.add_argument("--nuage-enterprise", required=False, dest='nuage_enterprise', default="csp")
    parser.add_argument("--nuage-user", required=False, dest='nuage_user', default="csproot")
    parser.add_argument("--nuage-password", required=False, dest='nuage_password', default="csproot")
    parser.add_argument("--nuage-url", required=False, dest='nuage_url', default="https://10.217.212.235:8443")

    result = parser.parse_args()

    domain="myDomainInstance"
    zone="MyZone"
    vip_subnet="VIPSubnet"

    nu=NuageInterface(result.nuage_user, result.nuage_password, result.nuage_enterprise, result.nuage_url, domain, zone, vip_subnet)
    if nu.networking_is_success() == False:
        quit()
        
    ips=nu.networking_get_app_endpoints("application_cool")
    print("Application application_cool Endpoints are:")
    for i in ips:
        print i

    #nu.ns_nuage_create_static_route(domain, "1.1.1.34", "255.255.255.255", "10.11.59.43", enterprise="Citrix")
    #nu.ns_nuage_remove_static_route(domain, "1.1.1.34", "255.255.255.255", "10.11.59.43", enterprise="Citrix")
    nu.networking_get_vips()
                
    print nu.networking_get_vips()
