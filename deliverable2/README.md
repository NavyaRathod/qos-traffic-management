# QoS Traffic Management System (UDP + SDN)

Mininet + Ryu (OpenFlow 1.3) + Open vSwitch HTB queues.

## Install (Ubuntu)
    sudo apt install mininet openvswitch-switch
    pip install ryu numpy matplotlib      # if ryu complains about eventlet: pip install eventlet==0.30.2

## Run
    # terminal 1
    ryu-manager controller.py
    # terminal 2
    sudo python3 topology.py --config config.json        # runs all experiments (~4 min)
    python3 analyze.py --results results                  # tables + plots

Useful: `--only srtf`, `--bottleneck 5`, `--run-time 30`, `--cli`.

## Dynamic traffic classes and destination ports
- `generator.py` creates the flows at run time: random destination ports (inside each profile's port range),
  packet sizes, rates, start times and flow sizes. Profiles live in `config.json -> generator`.
      sudo python3 topology.py --flows 10 --seed 3      # 10 random flows, reproducible with the seed
  Same seed = same flows in every experiment, so policies are compared fairly. Generated flows are saved in
  `results/generated_classes.json`.
- The controller needs NO port list. The first packet of any new UDP flow is sent to the controller, which
  classifies it by port-range rules (`class_rules`, changeable over REST), reads the flow-size hint from the
  packet header (used by SRTF), installs the queue rule and keeps monitoring it. Watch the controller log for
  `new flow discovered: dst_port=...`.
- Old behaviour (pre-registered flows): set `"discovery": "preregister"` in config.json.

## What is dynamic now (not hardcoded)
- Classes, ports, packet sizes, rates, start times, finite flow sizes, bottleneck, queue shares,
  experiments: all in `config.json`.
- Controller is configured over REST; priorities / policy can be changed while traffic is running:
      curl -X POST localhost:8080/qos/policy -d '{"policy":"srtf"}'
      curl -X POST localhost:8080/qos/flows  -d '{"flows":[{"port":5003,"priority":1}]}'
      curl localhost:8080/qos/status
- Prioritization only activates when measured offered load >= threshold * capacity (congestion-aware).
- New policies: add a `_p_xxx` method in controller.py and register it in `self.policies`.

## Policies
- none   : best effort (baseline, all flows share one queue)
- static : fixed class priority (deliverable 1 behaviour, but values come from config / REST)
- srtf   : Shortest Remaining Time First. Remaining bytes = declared flow size - bytes seen by the switch
           counters. Real-time classes (VoIP/video) stay in queue 1; other flows are ranked by remaining
           bytes: smallest -> queue 2, next -> 3, rest -> 4. Re-ranked every poll interval.

## Objective mapping
1 UDP traffic classes -> udp_sender.py + config.json     2 Classification -> match on UDP dst port
3 Priority policies -> static / srtf                      4 Controlled congestion -> offered ~13 Mbps vs 10 Mbps
5 Throughput/latency/loss -> analyze.py                  6 Fairness -> Jain index (+ FCT, per-class throughput)
7 Dynamic policy change -> REST, congestion-aware logic, `dynamic_switch` experiment (none -> static -> srtf)
