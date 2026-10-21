#QoS Traffic Management using UDP and SDN
## Project Overview
This project sends UDP traffic between Mininet hosts and uses an SDN controller to classify and prioritize traffic
## Components
UDP client and server
Ryu SDN controller
Mininet topology with h1, h2, and s1
## How to Run
strart the Ryu controller:
`ryu-manager ~/qos-traffic-management/controller/qos_controller.py`
In another terminal, start Mininet:
`sudo mn --switch ovs,protocols=OpenFlow13 --controller=remote,ip=127.0.0.1,port=6633`
At the Mininet prompt, check connectivity:
`pingall`

## Topology and Test
Topology: Mininet h1-s1-h2, OpenFlow 1.3, Ryu controller.
Connectivity test: pingall passed.
UDP test : client sent 100 packets to h2 port 5004; server received 100 packets (1900 bytes).

## Reproduce UDP Classification Tests
Controller: souce ~/project5/venv/bin/activate; ryu-manager ~/qos-traffic-management/controller/qos_controller.py
Mininet: sudo mn --switch ovs,protocols=OpenFlow13 --controller=remote,ip=127.0.0.1,port=6633
In Mininet, start the server on h2: h2 python3 /home/student/qos-traffic-management/app/server.py --port PORT &
Then run the client on h1: h1 python3 /home/student/qos-traffic-management/app/clent.py --host 10.0.0.2 --port PORT --rate 20 --seconds5
Repeat with PORT 5004 (HIGH, priority 100), 5001 (MEDIUM priority 50), and 5007 (LOW/default, priority 10).
Inspent installed rules: sh ovs-ofctl -O OpenFlow13 dump-flows s1
Controller mapping: UDP 5004-5005 HIGH (100); 5001-5003 MEDIUM (50); other UDP LOW (10).
