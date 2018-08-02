import requests
import json
import logging
from dnsinterface import DNSInterface

logger = logging.getLogger('docker_netscaler')

class InfobloxInterface(DNSInterface):
    def __init__(self, username, password, api_url, suffix):
        self.username=username
        self.password=password
        self.api_url=api_url + "/wapi/v2.2.2/"
        self.headers= {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        self.zone=suffix
        logger.debug("Configured Infoblox with username %s, password %s, api_url %s, zone %s" %(self.username, self.password, self.api_url, self.zone))
    
    def _infoblox_login(self):
        pass

    def _infoblox_logout(self):
        requests.request("POST", self.api_url + "logout", verify=False)

    def _infoblox_raw_api(self, method, url, **kwargs):
            response=requests.request(method, url, auth=(self.username, self.password), headers=self.headers, verify=False, **kwargs)
            self._infoblox_logout()
            response.raise_for_status()
            return response
    
    def dns_create_a_record(self, nsapp):
        appname=nsapp.appName.rsplit('-',1)[0]
        domain=appname + "." + self.zone
        create_payload={"name" : domain, "ipv4addr" : nsapp.vip}
        create_url=self.api_url + "record:a"
        try:
            self._infoblox_raw_api("POST", create_url, data=json.dumps(create_payload))
        except Exception as e:
            logger.debug("Failed to create A %s, message %s" %(domain, e.message))
        return domain
        
    def dns_delete_a_record(self, nsapp):
        appname=nsapp.appName.rsplit('-',1)[0]
        domain=appname + "." + self.zone
        get_payload={"name" : domain}
        get_url=self.api_url + "record:a"
        try:
            response=self._infoblox_raw_api("GET", get_url, data=json.dumps(get_payload))
        except Exception as e:
            logger.info("Failed to retrieve A %s, message %s" %(domain, e.message))
            return
        
        try:
            response_json=response.json()[0]
            ref=response_json["_ref"]
        except Exception as e:
            return

        delete_url=self.api_url + ref
        try:
            response=self._infoblox_raw_api("DELETE", delete_url)
        except Exception as e:
            logger.info("Failed to delete A %s, message %s" %(domain, e.message))
        return
            
        
if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL,
        format='%(asctime)s  - %(levelname)s - [%(filename)s:%(funcName)-10s]  (%(threadName)s) %(message)s')
    logger = logging.getLogger('docker_netscaler')
    logger.addFilter(logging.Filter('docker_netscaler'))
    logger.setLevel(logging.DEBUG)
    
    inb=InfobloxInterface("admin", "infoblox", "https://10.217.212.227", "wonderful.com.org")
    #inb.dns_create_a_record("local.fun.citrus", "3.4.5.6")
    inb.dns_delete_a_record("abc")
    
