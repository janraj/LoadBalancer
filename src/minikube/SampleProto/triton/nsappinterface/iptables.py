try:
    import iptc
    from iptc import IPTCError
except:
    raise

from subprocess import call
import logging
import triton.nsappinterface.netscalerapp as netscalerapp

CLUSTERIP_CHAIN = "CPX-CLUSTERIP"
LOOPBACK_CHAIN = "CPX-LOOPBACK"
HOSTMODE_MASQUERADE_CHAIN = "CPX-MASQUERADE"
POSTROUTING_CHAIN = "POSTROUTING"
PREROUTING_CHAIN = "PREROUTING"
OUTPUT_CHAIN = "OUTPUT"
NODEPORT_VSERVER_CHAIN = 'CPX-APP-HOSTPORT'

if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL,
            format='%(asctime)s  - %(levelname)s - [%(filename)s:%(funcName)-10s]  (%(threadName)s) %(message)s')
    logger = logging.getLogger('docker_netscaler')
    logger.addFilter(logging.Filter('docker_netscaler'))
    logger.setLevel(logging.DEBUG)

logger = logging.getLogger('docker_netscaler')

def cleanup():
    nattable = iptc.Table(iptc.Table.NAT)

    if nattable.is_chain(NODEPORT_VSERVER_CHAIN):
        try:
            chain = iptc.Chain(nattable, NODEPORT_VSERVER_CHAIN)
            chain.flush()
            rule = _vserver_global_rule()
            iptc.Chain(nattable, PREROUTING_CHAIN).delete_rule(rule)
        except IPTCError as ie:
            logger.error("IPTC Exception: deleting %s rule from PREROUTING chain: %s" % (NODEPORT_VSERVER_CHAIN, ie.message))
        except Exception as e:
            logger.error("Exception: deleting %s rule from PREROUTING chain :%s" % (NODEPORT_VSERVER_CHAIN, e.message))
        try:
            iptc.Chain(nattable, OUTPUT_CHAIN).delete_rule(rule)
        except IPTCError as ie:
            logger.error("IPTC Exception: deleting %s rule from OUTPUT chain: %s" % (NODEPORT_VSERVER_CHAIN, ie.message))
        except Exception as e:
            logger.error("Exception: deleting %s rule from OUTPUT chain :%s" % (NODEPORT_VSERVER_CHAIN, e.message))
        try:
            chain.delete()
            logger.debug("Deleted %s" % NODEPORT_VSERVER_CHAIN)
        except IPTCError as ie:
            logger.error("IPTC Exception: %s chain: %s" % (NODEPORT_VSERVER_CHAIN, ie.message))
        except Exception as e:
            logger.error("Exception: delete %s chain :%s" % (NODEPORT_VSERVER_CHAIN, e.message))


    if nattable.is_chain(CLUSTERIP_CHAIN):
        try:
            chain = iptc.Chain(nattable, CLUSTERIP_CHAIN)
            chain.flush()
            rule = _clusterip_global_rule()
            iptc.Chain(nattable, PREROUTING_CHAIN).delete_rule(rule)
        except IPTCError as ie:
            logger.error("IPTC Exception: deleting %s rule from PREROUTING chain: %s" % (CLUSTERIP_CHAIN, ie.message))
        except Exception as e:
            logger.error("Exception: deleting %s rule from PREROUTING chain :%s" % (CLUSTERIP_CHAIN, e.message))
        try:
            iptc.Chain(nattable, OUTPUT_CHAIN).delete_rule(rule)
        except IPTCError as ie:
            logger.error("IPTC Exception: deleting %s rule from OUTPUT chain: %s" % (CLUSTERIP_CHAIN, ie.message))
        except Exception as e:
            logger.error("Exception: deleting %s rule from OUTPUT chain :%s" % (CLUSTERIP_CHAIN, e.message))
        try:
            chain.delete()
            logger.debug("Deleted %s" % (CLUSTERIP_CHAIN))
        except IPTCError as ie:
            logger.error("IPTC Exception: %s chain: %s" % (CLUSTERIP_CHAIN, ie.message))
        except Exception as e:
            logger.error("Exception: delete %s chain :%s" % (CLUSTERIP_CHAIN, e.message))

            
def install_global_iptable_rules(netskaler):
    nattable = iptc.Table(iptc.Table.NAT)
    # Install Prerouting and Output Chain for VSERVER
    try:
        chain = nattable.create_chain(NODEPORT_VSERVER_CHAIN)
        rule = _vserver_global_rule()
        iptc.Chain(nattable, PREROUTING_CHAIN).insert_rule(rule)
        iptc.Chain(nattable, OUTPUT_CHAIN).insert_rule(rule)
        logger.debug("Created %s chain" % NODEPORT_VSERVER_CHAIN)
    except IPTCError as ie:
        logger.error("IPTC Exception : %s Create : %s" % (NODEPORT_VSERVER_CHAIN, ie.message))
    except Exception as e:
        logger.error("Exception : %s Create : %s" % (NODEPORT_VSERVER_CHAIN, e.message))

    # Install Prerouting and Output Chain for VSERVER
    try:
        chain = nattable.create_chain(CLUSTERIP_CHAIN)
        rule = _clusterip_global_rule()
        iptc.Chain(nattable, PREROUTING_CHAIN).insert_rule(rule)
        iptc.Chain(nattable, OUTPUT_CHAIN).insert_rule(rule)
        logger.debug("Created %s chain" % (CLUSTERIP_CHAIN))
    except IPTCError as ie:
        logger.error("IPTC Exception : %s Create : %s" % (CLUSTERIP_CHAIN, ie.message))
    except Exception as e:
        logger.error("Exception : %s Create : %s" % (CLUSTERIP_CHAIN, e.message))
        

def install_vserver_iptable_rules(ns_app):
    # Install Rules for Nodeport (loopback:'appport' or 'hostip:appport') application traffic:
    nattable = iptc.Table(iptc.Table.NAT)
    if ns_app.nodePort:
        rule = _nodeport_vserver_nodeport_rule(ns_app)
        try:
            # oops iptc module doesn't support checking if a rule exist so
            # deleting first and then readding.
            # TODO write method to find if relevant iptable rule exist
            iptc.Chain(nattable, NODEPORT_VSERVER_CHAIN).delete_rule(rule)
        except IPTCError as ie:
            logger.warn("IPTC Exception: %s" % ie.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
        
        try:
            iptc.Chain(nattable, NODEPORT_VSERVER_CHAIN).append_rule(rule)
            logger.debug("Created iptable rule for %s" % ns_app.appName)
        except IPTCError as ie:
            logger.warn("IPTC Exception: %s" % ie.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
        
    if ns_app.framework == netscalerapp.Framework.kubernetes:
        rule = _vserver_cluseterip_rule(ns_app)
        try:
            # oops iptc module doesn't support checking if a rule exist so
            # deleting first and then readding.
            # TODO write method to find if relevant iptable rule exist
            iptc.Chain(nattable, CLUSTERIP_CHAIN).delete_rule(rule)
        except IPTCError as ie:
            logger.warn("IPTC Exception: %s" % ie.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)

        try:
            iptc.Chain(nattable, CLUSTERIP_CHAIN).append_rule(rule)
            logger.debug("Created CLUSTERIP iptable rule for %s" % ns_app.appName)
        except IPTCError as ie:
            logger.warn("IPTC Exception: %s" % ie.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
            
        if ns_app.ingress != None:
            install_vserver_ingress_rules(ns_app) 
    
def install_vserver_ingress_rules(ns_app):
    nattable = iptc.Table(iptc.Table.NAT)
    rule = _vserver_ingress_rule(ns_app)
    
    try:
        iptc.Chain(nattable, NODEPORT_VSERVER_CHAIN).append_rule(rule)
        logger.debug("Created ingress iptable rule for %s" % ns_app.appName)
    except IPTCError as ie:
        logger.warn("IPTC Exception: %s" % ie.message)
    except Exception as e:
        logger.warn("Exception: %s" % e.message)


def delete_vserver_iptable_rules(ns_app):
    nattable = iptc.Table(iptc.Table.NAT)
    if ns_app.nodePort:
        try:
            rule = _nodeport_vserver_nodeport_rule(ns_app)
            iptc.Chain(nattable, NODEPORT_VSERVER_CHAIN).delete_rule(rule)
            logger.debug("Deleted iptable rule for %s" % ns_app.appName)
        except IPTCError as ie:
            logger.warn("IPTC Exception: %s" % ie.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
            
    if ns_app.framework == netscalerapp.Framework.kubernetes:
        try:
            rule = _vserver_cluseterip_rule(ns_app)
            iptc.Chain(nattable, CLUSTERIP_CHAIN).delete_rule(rule)
            logger.debug("Deleted ClusterIP iptable rule for %s" % ns_app.appName)
        except IPTCError as ie:
            logger.warn("IPTC Exception: %s" % ie.message)
        except Exception as e:
            logger.warn("Exception: %s" % e.message)
                
        if ns_app.ingress != None:
            delete_vserver_ingress_rules(ns_app)    

def delete_vserver_ingress_rules(ns_app):
    nattable = iptc.Table(iptc.Table.NAT)
    rule = _vserver_ingress_rule(ns_app)
    
    try:
        iptc.Chain(nattable, NODEPORT_VSERVER_CHAIN).delete_rule(rule)
        logger.debug("Deleted ingress iptable rule for %s" % ns_app.appName)
    except IPTCError as ie:
        logger.warn("IPTC Exception: %s" % ie.message)
    except Exception as e:
        logger.warn("Exception: %s" % e.message)

''' Helper Functions Start below '''
def _vserver_global_rule():
    rule = iptc.Rule()
    match1 = rule.create_match("addrtype")
    match1.dst_type = "LOCAL"
    match2 = rule.create_match("comment")
    match2.comment = "Rules for Steering Application traffic to CPX Vserver"
    rule.target = rule.create_target(NODEPORT_VSERVER_CHAIN)
    return rule

def _clusterip_global_rule():
    rule = iptc.Rule()
    match2 = rule.create_match("comment")
    match2.comment = "Rules for Steering CLUSTERIP traffic to CPX Vserver"
    rule.target = rule.create_target(CLUSTERIP_CHAIN)
    return rule


def _vserver_cluseterip_rule(ns_app):
    rule = iptc.Rule()
    if ns_app.mode == 'udp':
        protocol = "udp"
    else:
        protocol = 'tcp'
    rule.protocol = protocol
    match = rule.create_match(protocol)
    rule.set_dst(ns_app.serviceIP)
    match.dport = str(ns_app.servicePort)
    match_c = rule.create_match("comment")
    match_c.comment = "vserver - clusterip rule " + ns_app.appName
    
    rule.target = rule.create_target("DNAT")
    rule.target.to_destination = ns_app.vip + ":" + str(ns_app.vport)
    return rule

def _nodeport_vserver_nodeport_rule(ns_app):
    rule = iptc.Rule()
    if ns_app.mode == 'udp':
        protocol = "udp"
    else:
        protocol = 'tcp'
    rule.protocol = protocol
    match = rule.create_match(rule.protocol)
    match.dport = str(ns_app.nodePort)
    match_c = rule.create_match("comment")
    match_c.comment = "vserver - host port rule " + ns_app.appName
    
    rule.target = rule.create_target("DNAT")
    rule.target.to_destination = ns_app.vip + ":" + str(ns_app.vport)
    return rule

def _vserver_ingress_rule(ns_app):
    rule = iptc.Rule()
    rule.protocol = ns_app.mode
    rule.set_dst(ns_app.ingress)
    match = rule.create_match(rule.protocol)
    match.dport = str(ns_app.servicePort)
    match_c = rule.create_match("comment")
    match_c.comment = "Ingress vserver - app " + ns_app.appName
    
    rule.target = rule.create_target("DNAT")
    rule.target.to_destination = ns_app.vip + ":" + str(ns_app.vport)
    return rule

if __name__ == "__main__":
    import sys
    if len (sys.argv) >= 2 and sys.argv[1] == "cleanup":
        cleanup()
        logger.debug("cleaned the iptable rules")
