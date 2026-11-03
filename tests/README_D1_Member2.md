# Member 2 — Mininet Topology and D1 Testing

## Topology

The network uses a dumbbell layout. Three traffic hosts (h1, h2, h3) connect to switch s1. Server host h4 connects to switch s2. The s1–s2 link is shared, so it is the bottleneck where congestion occurs.

Access links are 100 Mbps with 1 ms delay. The bottleneck defaults to 10 Mbps with 10 ms delay and a 100-packet queue. Random loss defaults to 0%, so loss under overload is caused by congestion and queue drops.

Baseline mode runs without the SDN controller. The normal mode connects to the remote OpenFlow 1.3 controller at 127.0.0.1:6653.

## Tests

The D1 script checks:

- **T1 — Connectivity:** `pingall` should report 0% loss.
- **T2 — Idle latency:** measures round-trip time from h1 to h4.
- **T3 — Single flow:** offers 8 Mbps, below the 10 Mbps bottleneck.
- **T4 — Congestion:** offers three simultaneous 7 Mbps flows, about 21 Mbps total, above the bottleneck capacity.

## Baseline results

Connectivity passed with 0% loss. Idle average RTT was 26.722 ms. The single flow received 8.39 Mbit/s with 0% loss. Under the three-flow overload, received rates were 3.10–3.37 Mbit/s per flow, with 54–57% packet loss. This confirms congestion at the shared 10 Mbps link.

These are baseline results without SDN prioritization. They provide a comparison point for later controller-enabled tests.
