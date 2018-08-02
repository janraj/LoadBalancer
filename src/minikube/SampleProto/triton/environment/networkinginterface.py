import logging

logger = logging.getLogger('docker_netscaler')

class NetworkingInterface(object):
    def __init__(self):
        pass

    def networking_modify_vip_route(self, op, vip, nsips, appname, appdata):
        pass

    def networking_place_vip(self, vip, target_device):
        pass

    def networking_release_vip(self, vip, target_device):
        pass
        
    def networking_is_success(self):
        pass

    def networking_get_app_endpoints(self, app):
        """ Returns a list of endpoints [ep1, ep2,...] 
            Application name matches subnet name
        """
        pass

    def networking_get_vips(self):
        """ Returns information on vips in the form of dictionary:
            {'subnet': <subnet name>, 'ipfrom': <first ip in the range>, 'ipto': <last ip in the range>, 'do_not_use_ips':[<ips that cannot be used, for example gateway ip>]}
        """
        pass
