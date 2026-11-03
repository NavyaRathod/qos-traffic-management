#!/usr/bin/env python3
"""
QoS Traffic Management System - Mininet topology (Member 2, Deliverable 1)

Dumbbell topology:

    h1 (Class A: real-time) --\
    h2 (Class B: streaming) ---- s1 ==== bottleneck ==== s2 ---- h4 (UDP server)
    h3 (Class C: bulk)      --/

Access links are fast; the s1-s2 link is the bottleneck where congestion
is created and where the SDN controller applies priority policies.

Usage:
    sudo python3 topology.py                      # Ryu on 127.0.0.1:6653, CLI
    sudo python3 topology.py --controller-port 6633
    sudo python3 topology.py --baseline           # no SDN: plain learning switches
    sudo python3 topology.py --bw 5 --delay 20 --queue 50
"""
import argparse

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

# ---- Addressing / interface contract shared with Member 1 and Member 3 ----
SERVER_IP = "10.0.0.4"
CLIENTS = {
    "h1": {"ip": "10.0.0.1", "class": "A-realtime",  "udp_port": 5001},
    "h2": {"ip": "10.0.0.2", "class": "B-streaming", "udp_port": 5002},
    "h3": {"ip": "10.0.0.3", "class": "C-bulk",      "udp_port": 5003},
}


def build(args):
    net = Mininet(
        controller=None,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,
        build=False,
    )

    if args.baseline:
        # Baseline: standalone OVS acting as a learning switch (no controller)
        sw_opts = {"failMode": "standalone"}
    else:
        sw_opts = {"protocols": "OpenFlow13"}
        net.addController(
            "c0",
            controller=RemoteController,
            ip=args.controller_ip,
            port=args.controller_port,
        )

    s1 = net.addSwitch("s1", **sw_opts)
    s2 = net.addSwitch("s2", **sw_opts)

    hosts = {}
    for name, cfg in CLIENTS.items():
        hosts[name] = net.addHost(name, ip=cfg["ip"] + "/24")
    srv = net.addHost("h4", ip=SERVER_IP + "/24")

    # Access links: fast, low delay, so they never limit traffic
    for name in CLIENTS:
        net.addLink(hosts[name], s1, bw=100, delay="1ms")
    net.addLink(srv, s2, bw=100, delay="1ms")

    # Bottleneck link: this is where congestion happens
    net.addLink(
        s1, s2,
        bw=args.bw,                 # Mbps
        delay=f"{args.delay}ms",
        loss=args.loss,             # % random loss (0 = only queue drops)
        max_queue_size=args.queue,  # packets
        use_htb=True,
    )
    return net


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--controller-ip", default="127.0.0.1")
    p.add_argument("--controller-port", type=int, default=6653)
    p.add_argument("--baseline", action="store_true", help="no SDN controller")
    p.add_argument("--bw", type=float, default=10, help="bottleneck Mbps")
    p.add_argument("--delay", type=int, default=10, help="bottleneck delay ms")
    p.add_argument("--loss", type=float, default=0, help="bottleneck random loss %%")
    p.add_argument("--queue", type=int, default=100, help="bottleneck queue (pkts)")
    p.add_argument("--no-cli", action="store_true")
    args = p.parse_args()

    setLogLevel("info")
    net = build(args)
    net.build()
    net.start()
    info("*** Topology up. Server: %s | bottleneck: %s Mbps, %s ms\n"
         % (SERVER_IP, args.bw, args.delay))
    if not args.no_cli:
        CLI(net)
    net.stop()


if __name__ == "__main__":
    main()

