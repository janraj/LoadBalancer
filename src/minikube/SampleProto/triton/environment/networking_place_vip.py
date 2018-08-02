import triton.nstriton
import sys
import json
import logging
errlogs = open('/var/mps/log/nstriton.log', 'a')

ch = logging.StreamHandler(errlogs)
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s  - %(levelname)s - [%(filename)s:%(funcName)-10s]  (%(threadName)s) %(message)s')
ch.setFormatter(formatter)
logger = logging.getLogger('docker_netscaler')
logger.addFilter(logging.Filter('docker_netscaler'))
logger.setLevel(logging.INFO)
logger.addHandler(ch)

conf = {}
triton.nstriton.re_read_conf_zmq(conf)
op = sys.argv[1]
parameters = sys.argv[2]
app_networking_data = sys.argv[3]
devices = sys.argv[4].split(",")
try:
	parameters = json.loads(parameters)
	network = triton.nstriton.create_networking_interface(conf)
	for app_service in parameters["app-services"]:
		vip = app_service["virtual-ip"]
		if (network.networking_modify_vip_route(op, vip, devices, parameters["appname"], app_networking_data)):
			exit (0)
		else: 
			exit (100)
except Exception as e:
	print "error in network interface: " + str(e)
exit (200)
