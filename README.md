# QoS Traffic Management using UDP and SDN

## Project Overview

This project sends UDP traffic between Mininet hosts and uses an SDN controller
to classify and prioritize traffic.

## Components

- UDP traffic application
- Ryu SDN controller
- Mininet topology

## Run the project

Start the Ryu controller:

```sh
ryu-manager ~/qos-traffic-management/controller/qos_controller.py
```

In another terminal, start the project topology with the same controller port:

```sh
sudo python3 ~/qos-traffic-management/topology/topology.py --controller-port 6633
```

## UDP Traffic Application

The app generates, receives, classifies, and measures UDP traffic. See
[`app/README.md`](app/README.md) for traffic classes, packet format, Mininet
commands, tests, and measurement details.

## Topology and Test

The topology uses an OpenFlow 1.3 switch and Ryu controller. The recorded
baseline connectivity test (`pingall`) passed. A historical UDP test sent 100
packets to h2 port 5004; the server received 100 packets (1900 bytes). For
current app port and controller mappings, see [`app/README.md`](app/README.md).

## Deliverable 2: Dynamic QoS with SRTF

Random traffic classes and destination ports, automatic flow discovery in the
Ryu controller, congestion-aware policies (none / static / SRTF) switchable at
runtime via REST, and automated analysis. See
[`deliverable2/README.md`](deliverable2/README.md).
