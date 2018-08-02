from kubernetes import client, config, watch
from pprint import pprint

# Configs can be set in Configuration class directly or using helper utility
config.load_kube_config()

v1 = client.CoreV1Api()
print("Listing pods with their IPs:")
ret = v1.list_pod_for_all_namespaces(watch=False)
for i in ret.items:
    print("%s\t%s\t%s" % (i.status.pod_ip, i.metadata.namespace, i.metadata.name))


print("All Namespaces", v1.list_namespace)

w = watch.Watch()
for event in w.stream(v1.list_namespace, timeout_seconds=1000):
    print("Event: %s %s" % (event['type'], event['object'].metadata.name))

print("Ended.")

