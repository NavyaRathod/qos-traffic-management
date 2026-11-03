#!/usr/bin/env python3
"""
D1 initial tests (Member 2): verify the topology works end to end.

T1 pingall connectivity
T2 idle RTT (should be ~ 2*(1+10+1) ms = ~24 ms one way x2)
T3 bottleneck capacity check with a single UDP flow (iperf)
T4 congestion check: 3 simultaneous UDP flows > bottleneck -> loss appears

Run (controller must already be running unless --baseline):
    sudo python3 d1_tests.py
    sudo python3 d1_tests.py --baseline
Results are written to results/d1_<mode>.txt
"""
import argparse
import os
import re
import time

from mininet.log import setLogLevel
from mininet.net import Mininet  # noqa: F401  (type reference)

from topology import build, SERVER_IP

LOSS_RE = re.compile(r"\((\d+(?:\.\d+)?)%\)")
BW_RE = re.compile(r"([\d.]+)\s+([KMG]?)bits/sec")


def udp_flow_test(net, senders, rate_mbps, secs=8):
    """Start iperf UDP server per sender (distinct ports), run clients in
    parallel, return {host: (throughput_str, loss_pct)}."""
    srv = net.get("h4")
    ports = {}
    for i, h in enumerate(senders):
        port = 5001 + i
        ports[h] = port
        srv.cmd(f"iperf -s -u -p {port} -i 1 > /tmp/srv_{port}.txt 2>&1 &")
    time.sleep(1)

    for h in senders:
        net.get(h).cmd(
            f"iperf -c {SERVER_IP} -u -p {ports[h]} -b {rate_mbps}M -t {secs} "
            f"> /tmp/cli_{h}.txt 2>&1 &"
        )
    time.sleep(secs + 3)
    srv.cmd("pkill -f 'iperf -s'")

    out = {}
    for h in senders:
        text = open(f"/tmp/srv_{ports[h]}.txt").read()
        lines = [l for l in text.splitlines() if "bits/sec" in l]
        last = lines[-1] if lines else ""
        bw = BW_RE.search(last)
        loss = LOSS_RE.search(last)
        out[h] = (
            f"{bw.group(1)} {bw.group(2)}bits/s" if bw else "n/a",
            float(loss.group(1)) if loss else None,
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--controller-ip", default="127.0.0.1")
    ap.add_argument("--controller-port", type=int, default=6653)
    ap.add_argument("--bw", type=float, default=10)
    ap.add_argument("--delay", type=int, default=10)
    ap.add_argument("--loss", type=float, default=0)
    ap.add_argument("--queue", type=int, default=100)
    args = ap.parse_args()

    setLogLevel("warning")
    net = build(args)
    net.build()
    net.start()
    time.sleep(3)  # let switches connect to the controller

    mode = "baseline" if args.baseline else "sdn"
    os.makedirs("results", exist_ok=True)
    lines = [f"D1 tests | mode={mode} | bottleneck={args.bw}Mbps/{args.delay}ms"]

    # T1
    drop = net.pingAll()
    lines.append(f"T1 pingall packet loss: {drop}%  -> {'PASS' if drop == 0 else 'FAIL'}")

    # T2
    rtt = net.get("h1").cmd(f"ping -c 5 {SERVER_IP}").splitlines()[-1]
    lines.append(f"T2 idle RTT h1->h4: {rtt}")

    # T3
    r = udp_flow_test(net, ["h1"], rate_mbps=args.bw * 0.8)
    lines.append(f"T3 single flow @{args.bw*0.8:.1f}Mbps (below capacity): {r}")

    # T4
    r = udp_flow_test(net, ["h1", "h2", "h3"], rate_mbps=args.bw * 0.7)
    lines.append(f"T4 3 flows @{args.bw*0.7:.1f}Mbps each (~{args.bw*2.1:.0f}Mbps offered): {r}")

    net.stop()
    report = "\n".join(lines)
    print(report)
    with open(f"results/d1_{mode}.txt", "w") as f:
        f.write(report + "\n")


if __name__ == "__main__":
    main()
