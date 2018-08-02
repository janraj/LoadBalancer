import asyncio
import logging
from threading import Thread

from kubernetes import client, config, watch

logger = logging.getLogger('k8s_events')
logger.setLevel(logging.DEBUG)

config.load_kube_config()

v1 = client.CoreV1Api()
v1ext = client.ExtensionsV1beta1Api()

def pods():
    w = watch.Watch() 
    print("In Pods")
    for event in w.stream(v1.list_pod_for_all_namespaces):
        logger.info("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        print("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
	#await asyncio.sleep(0) 

def deployments():
    print("In Deployments")
    w = watch.Watch()
    for event in w.stream(v1ext.list_deployment_for_all_namespaces):
        logger.info("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        print("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        #await asyncio.sleep(0)

def ingress():
    w = watch.Watch() 
    print("Ingress")
    for event in w.stream(v1ext.list_ingress_for_all_namespaces):
        logger.info("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        print("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        #await asyncio.sleep(0) 

def service():
    w = watch.Watch() 
    print("Service")
    for event in w.stream(v1.list_service_for_all_namespaces):
        logger.info("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        print("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        #await asyncio.sleep(0) 

def serviceAccount():
    w = watch.Watch() 
    print("ServiceAccount")
    for event in w.stream(v1.list_service_account_for_all_namespaces):
        logger.info("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        print("Event: %s %s %s" % (event['type'], event['object'].kind, event['object'].metadata.name))
        #await asyncio.sleep(0) 

print("Starting")
ioloop = asyncio.get_event_loop()
ioloop.create_task(pods())
ioloop.create_task(deployments())
ioloop.create_task(ingress())
ioloop.create_task(service())
ioloop.create_task(serviceAccount())
ioloop.run_forever()
