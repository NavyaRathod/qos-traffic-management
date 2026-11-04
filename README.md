# QoS Traffic Management using UDP and SDN

This project generates multiple classes of UDP traffic, sends them through a
Mininet topology with a constrained link, and uses a Ryu SDN controller to
classify and prioritize flows.

## Application responsibilities

- `app/client.py` generates one selected traffic class at a configured packet
  rate and duration.
- `app/server.py` receives all three traffic classes, echoes valid packets to
  the clients, and reports per-class throughput and Jain's fairness index.
- The client uses echoed packets to measure packet loss, echo throughput, and
  round-trip latency. It can append run measurements to a CSV file.
- Each UDP datagram includes a small application header so the receiver can
  identify the class and sequence number.

The class-to-port contract follows the topology and controller: high = UDP
5001, medium = UDP 5002, and low = UDP 5003. The application generates traffic
and reports measurements; SDN flow rules and their priorities are controlled
by the Ryu controller.

## Run in Mininet

Start the controller:

```sh
ryu-manager ~/qos-traffic-management/controller/qos_controller.py
```

In another terminal, start the project topology (use the same controller port):

```sh
sudo python3 ~/qos-traffic-management/topology/topology.py --controller-port 6633
```

At the Mininet prompt, start one server on `h4` to listen on all class ports:

```text
h4 python3 /home/student/qos-traffic-management/app/server.py --duration 15 &
```

Generate three classes concurrently. These rates and packet sizes exceed the
topology's default 10 Mbps bottleneck when combined, creating controlled
congestion:

```text
h1 python3 /home/student/qos-traffic-management/app/client.py --host 10.0.0.4 --class high --rate 500 --seconds 10 --payload-size 1200 --csv /tmp/qos.csv &
h2 python3 /home/student/qos-traffic-management/app/client.py --host 10.0.0.4 --class medium --rate 500 --seconds 10 --payload-size 1200 --csv /tmp/qos.csv &
h3 python3 /home/student/qos-traffic-management/app/client.py --host 10.0.0.4 --class low --rate 500 --seconds 10 --payload-size 1200 --csv /tmp/qos.csv &
```

Each client prints sent/echoed/lost packets, loss percentage, echo throughput,
and average/minimum/maximum RTT. The server prints per-class receive
throughput, per-flow counts, and Jain's fairness index. The client CSV can be
used to compare individual runs. To reduce or increase offered load, change
`--rate` or `--payload-size`; use `--class` to select the class. The optional
client `--port` overrides the class's default destination port.

To run without a fixed server duration, omit `--duration` and stop the server
with Ctrl+C. For an uncongested baseline, use lower client rates or the
topology's `--baseline` option.
