# Member 1: UDP Traffic Application

The app generates, receives, classifies, and measures UDP traffic. It does not
configure the Mininet topology or install SDN policies.

## Traffic classes and compatibility

| App class | UDP destination port | Controller class |
|---|---:|---|
| HIGH (`high`) | 5001 | HIGH |
| MEDIUM (`medium`) | 5002 | MEDIUM |
| LOW (`low`) | 5003 | LOW |

These are the ports configured in both the current topology and Ryu controller.
The Ryu code classifies UDP packets using the destination port and does not
inspect the application payload, so the app packet header is compatible.
Using `--port` to override a class's default port is supported by the app, but
may cause the controller to apply a different class. The client prints a
warning for such overrides.

## Packet format

Each UDP payload begins with a 37-byte network-byte-order header:

| Field | Size | Description |
|---|---:|---|
| Magic (`QOS1`) | 4 bytes | Identifies this app's packet format |
| Class ID | 1 byte | HIGH=1, MEDIUM=2, LOW=3 |
| Sequence number | 8 bytes | Unsigned, starts at zero per flow |
| Send timestamp | 8 bytes | Monotonic clock timestamp in nanoseconds |
| Flow ID | 16 bytes | UUID identifying this UDP flow |

Remaining bytes pad the packet to the requested UDP payload size. This size
includes the 37-byte app header and excludes IP/UDP headers. Maximum UDP
payload size is 65,507 bytes.

## Run Member 1's app

On the Mininet host `h4`, start the receiver on all three standard ports just
before the clients. Set its duration to cover the traffic generation plus the
reply timeout so class throughputs share the same measurement window:

```text
h4 python3 /home/student/qos-traffic-management/app/server.py --duration 12 --experiment-id run-001 --csv /tmp/qos-server.csv
```

In the Mininet CLI, start a client per source host. Use the same experiment ID
to associate client and server CSV records. Keep each client CSV separate so
simultaneous clients do not contend while appending:

```text
h1 python3 /home/student/qos-traffic-management/app/client.py --host 10.0.0.4 --class high --source-port 5001 --rate 500 --packet-size 1200 --seconds 10 --experiment-id run-001 --csv /tmp/qos-high.csv &
h2 python3 /home/student/qos-traffic-management/app/client.py --host 10.0.0.4 --class medium --source-port 5002 --rate 500 --packet-size 1200 --seconds 10 --experiment-id run-001 --csv /tmp/qos-medium.csv &
h3 python3 /home/student/qos-traffic-management/app/client.py --host 10.0.0.4 --class low --source-port 5003 --rate 500 --packet-size 1200 --seconds 10 --experiment-id run-001 --csv /tmp/qos-low.csv &
```

Client options:

- `--class high|medium|low` selects the app class and default UDP port.
- `--rate` sets the target packet rate in packets per second.
- `--packet-size` (or legacy alias `--payload-size`) sets the UDP payload
  size, including the app header.
- `--seconds` sets the traffic generation duration.
- `--host` sets the destination address; the default is `10.0.0.4`.
- `--port` optionally overrides the class's default destination port.
- `--source-port` optionally binds the local UDP port. In Mininet, use the
  same class port so the controller classifies echoed reply traffic correctly.
- `--flow-id` optionally supplies a UUID; otherwise a unique UUID is generated.
- `--experiment-id` associates results from several clients and the server.
- `--reply-timeout` controls how long the client waits for delayed echoes.
- `--csv` appends results; existing incompatible CSV headers are rejected.

The server defaults to all class ports and runs until Ctrl+C. `--port PORT`
listens on one port; `--ports PORT...` listens on a selected set. `--duration`
stops it after the specified seconds. The optional server `--csv` appends
per-class receive results, per-flow results, an experiment fairness summary,
and invalid/unexpected packet counts. Each execution appends records instead
of replacing prior experiments.

Run the app's network-independent unit tests from the project root:

```sh
python3 -m unittest discover -s app -p "test_*.py" -v
```

## Measurements and interpretation

The client reports:

- Packets sent and unique, valid echo packets received.
- Packets lost and loss percentage. An unreturned echo by the end of
  `--reply-timeout` is counted as lost; this is end-to-end echo loss and does
  not isolate forward-path loss from return-path loss.
- **Offered throughput:** sent UDP payload bits divided by the traffic
  generation time.
- **Echo throughput:** valid echoed UDP payload bits received by the client
  divided by its measurement window. This is not the same as the server's
  **received throughput**.
- Average, minimum, and maximum **round-trip time (RTT)**, measured from client
  send to matching echo receive. It is not one-way latency.
- Duplicate, out-of-order, invalid, and unexpected echo counts. Out-of-order
  valid packets are still counted as received.

The server reports received packets, bytes, and per-class/per-flow
**received throughput**. Its unique packet counts suppress duplicate sequence
numbers within a flow. Invalid headers and class/port mismatches are reported
separately.

For fairness, the server computes Jain's index from measured receive
throughputs using `J = (sum(x_i))^2 / (n * sum(x_i^2))`. It reports both:

- `jain_fairness_all_classes`: HIGH, MEDIUM, and LOW are always in the formula;
  inactive classes have zero throughput.
- `jain_fairness_active_classes`: only classes that received packets are
  included. The active-class count/list is reported too.

If all rates are zero, the index is reported as zero (the formula is
undefined for all-zero inputs). Fairness measures equality of throughput; it
does **not** show whether the configured QoS priority policy was followed.

Client CSV rows contain per-flow packet counts, loss, offered and echo
throughput, RTTs, and packet anomalies. Server CSV rows contain per-class and
per-flow received throughput and a three-class/active-class fairness summary.
Fields not measurable at that endpoint are left blank. Each CSV file has a
fixed schema and uses append-only logging to preserve prior experiments.

For the network and controller commands, see the project-level
[README](../README.md).
