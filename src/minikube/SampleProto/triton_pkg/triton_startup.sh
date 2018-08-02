#!/bin/bash
FAILURE_EXIT_CODE=1
# Checking for EULA acceptance
function PRINT_EULA_MSG {                                                                                                         
        echo "This triton is running without valid license. To enable Triton, you can do following thing:";
        echo -e "\n \t  Pass -e EULA=yes as an environment variable. This acknowledges that you have read the EULA which is available here: https://www.citrix.com/downloads/netscaler-adc/container-based-adc/netscaler-cpx-thank-you.html";
}
if ! [ -z ${EULA} ]; then
	if ! [[ "${EULA,,}" =~ ^(yes|true|ok)$ ]]; then
		PRINT_EULA_MSG
	else
		echo -e "\n User has accepted EULA. Starting Triton \n"
	fi
else
	PRINT_EULA_MSG
fi

if  ! [ -v kubernetes_url ]; then
	echo "Setting default kubernetes url"
	kubernetes_url = "https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_PORT_443_TCP_PORT"
fi
echo "kubernetes url is $kubernetes_url"
python /var/triton_ingress/triton/nstriton.py --config-interface=netscaler --kube-apiserver $kubernetes_url --loglevel=$LOGLEVEL
echo "triton exited"
#while true; do sleep 10; echo hi; done
