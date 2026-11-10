#!/usr/bin/env python3
"""
Mininet topology + experiment runner. Everything comes from config.json.

  h1..hN (one per traffic class) --- s1 ===(bottleneck, OVS HTB queues)=== s2 --- srv
Run:  sudo python3 topology.py --config config.json   (controller.py must already be running)
"""
import argparse, copy, json, math, os, subprocess, sys, time, urllib.request

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

from generator import generate_classes, offered_mbps, rules_from_profiles

HERE = os.path.dirname(os.path.abspath(__file__))
SRV_IP = '10.0.0.100'
WIRE = 42


def api(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def setup_qos(intf, rate_bps, shares):
    """HTB queues on the bottleneck port: queue i has min-rate share and priority i (1 = highest)."""
    parts = ['ovs-vsctl -- set port %s qos=@newqos' % intf,
             '-- --id=@newqos create qos type=linux-htb other-config:max-rate=%d %s' % (
                 rate_bps, ' '.join('queues:%d=@q%d' % (i + 1, i + 1) for i in range(len(shares))))]
    for i, s in enumerate(shares):
        parts.append('-- --id=@q%d create queue other-config:min-rate=%d other-config:max-rate=%d '
                     'other-config:priority=%d' % (i + 1, int(rate_bps * s), rate_bps, i + 1))
    subprocess.run(' '.join(parts), shell=True, check=True)


def build(cfg):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)
    net.addController('c0', controller=RemoteController, ip=cfg['controller']['ip'],
                      port=cfg['controller']['of_port'])
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    srv = net.addHost('srv', ip=SRV_IP + '/24')
    hosts = {}
    for i, c in enumerate(cfg['classes'], 1):
        hosts[c['name']] = net.addHost('h%d' % i, ip='10.0.0.%d/24' % i)
        net.addLink(hosts[c['name']], s1)
    up = net.addLink(s1, s2)
    net.addLink(s2, srv, delay='%dms' % cfg['network']['server_delay_ms'])
    net.start()
    return net, s1, up, srv, hosts


def run_experiment(cfg, exp, outdir, hosts, srv):
    base = cfg['controller']['rest']
    os.makedirs(outdir, exist_ok=True)
    classes = cfg['classes']
    api(base, 'POST', '/qos/reset')
    flows = []
    for c in classes:
        wire_total = None
        if c.get('total_bytes'):
            wire_total = math.ceil(c['total_bytes'] / c['size']) * (c['size'] + WIRE)
        flows.append({'name': c['name'], 'port': c['port'], 'priority': c.get('priority', 4),
                      'realtime': c.get('realtime', False), 'total_wire_bytes': wire_total})
    if cfg['control'].get('discovery', 'auto') == 'preregister':
        api(base, 'POST', '/qos/flows', {'flows': flows})

    json.dump({'experiment': exp['name'], 'schedule': exp['schedule'], 'classes': classes,
               'bottleneck_mbps': cfg['network']['bottleneck_mbps'], 'run_time_s': cfg['run_time_s']},
              open(os.path.join(outdir, 'meta.json'), 'w'), indent=2)

    recv = srv.popen(['python3', os.path.join(HERE, 'udp_receiver.py'), '--ports'] +
                     [str(c['port']) for c in classes] + ['--out', outdir])
    time.sleep(1)
    senders = []
    for c in classes:
        cmd = ['python3', os.path.join(HERE, 'udp_sender.py'), '--dst', SRV_IP, '--port', str(c['port']),
               '--size', str(c['size']), '--rate-kbps', str(c['rate_kbps']), '--delay', str(c.get('start_s', 0)),
               '--name', c['name'], '--out', os.path.join(outdir, 'send_%d.json' % c['port'])]
        if c.get('total_bytes'):
            cmd += ['--packets', str(math.ceil(c['total_bytes'] / c['size']))]
        else:
            cmd += ['--duration', str(c.get('duration_s', 10))]
        senders.append(hosts[c['name']].popen(cmd))

    t0 = time.time()
    for step in sorted(exp['schedule'], key=lambda s: s['t']):
        wait = t0 + step['t'] - time.time()
        if wait > 0:
            time.sleep(wait)
        api(base, 'POST', '/qos/policy', {'policy': step['policy']})
        info('  [t=%4.1fs] policy -> %s\n' % (time.time() - t0, step['policy']))
    wait = t0 + cfg['run_time_s'] - time.time()
    if wait > 0:
        time.sleep(wait)
    for s in senders:
        s.wait()
    time.sleep(1)
    recv.terminate()
    recv.wait()
    json.dump(api(base, 'GET', '/qos/status'), open(os.path.join(outdir, 'controller_status.json'), 'w'), indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=os.path.join(HERE, 'config.json'))
    ap.add_argument('--out', default=os.path.join(HERE, 'results'))
    ap.add_argument('--only', help='run only this experiment name')
    ap.add_argument('--bottleneck', type=float, help='override bottleneck Mbps')
    ap.add_argument('--run-time', type=float, help='override run time (s)')
    ap.add_argument('--flows', type=int, help='number of random flows (enables the generator)')
    ap.add_argument('--seed', type=int, help='random seed for the generator')
    ap.add_argument('--cli', action='store_true', help='open Mininet CLI instead of running experiments')
    a = ap.parse_args()
    cfg = json.load(open(a.config))
    if a.bottleneck:
        cfg['network']['bottleneck_mbps'] = a.bottleneck
    if a.run_time:
        cfg['run_time_s'] = a.run_time

    g = cfg.get('generator', {})
    if a.flows:
        g['enabled'], g['num_flows'] = True, a.flows
    if a.seed is not None:
        g['seed'] = a.seed
    if g.get('enabled'):
        cfg['classes'] = generate_classes(g, cfg['run_time_s'])
        rules = rules_from_profiles(g['profiles'])
    else:
        rules = [{'name': c['name'], 'ports': [c['port'], c['port']], 'priority': c.get('priority', 4),
                  'realtime': c.get('realtime', False)} for c in cfg['classes']]
    rules = cfg.get('class_rules') or rules
    os.makedirs(a.out, exist_ok=True)
    json.dump(cfg['classes'], open(os.path.join(a.out, 'generated_classes.json'), 'w'), indent=2)
    print('\nTraffic classes for this run (%d flows):' % len(cfg['classes']))
    for c in cfg['classes']:
        print('  %-14s port=%-5d size=%-5d rate=%-5d kbps start=%ss %s' % (
            c['name'], c['port'], c['size'], c['rate_kbps'], c.get('start_s', 0),
            ('total=%d B' % c['total_bytes']) if c.get('total_bytes') else 'continuous'))
    load = offered_mbps(cfg['classes'])
    print('Offered load if all overlap: %.1f Mbps vs bottleneck %.1f Mbps' % (load, cfg['network']['bottleneck_mbps']))
    if load < 1.2 * cfg['network']['bottleneck_mbps']:
        print('WARNING: little congestion expected - use more --flows or a lower --bottleneck')

    setLogLevel('info')
    net, s1, up, srv, hosts = build(cfg)
    try:
        rate = int(cfg['network']['bottleneck_mbps'] * 1e6)
        setup_qos(up.intf1.name, rate, cfg['network']['queue_min_share'])
        for h in hosts.values():           # let the switches learn MACs
            net.ping([h, srv], timeout='1')
        api(cfg['controller']['rest'], 'POST', '/qos/setup', {
            'ingress_dpid': 1, 'uplink_port': s1.ports[up.intf1], 'capacity_bps': rate,
            'congestion_threshold': cfg['control']['congestion_threshold'],
            'poll_interval': cfg['control']['poll_interval_s'],
            'discovery': cfg['control'].get('discovery', 'auto'), 'class_rules': rules})
        if a.cli:
            CLI(net)
            return
        for exp in cfg['experiments']:
            if a.only and exp['name'] != a.only:
                continue
            info('*** experiment: %s\n' % exp['name'])
            run_experiment(cfg, exp, os.path.join(a.out, exp['name']), hosts, srv)
    finally:
        net.stop()
        subprocess.run('ovs-vsctl --all destroy qos -- --all destroy queue', shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    if os.geteuid() != 0:
        sys.exit('run with sudo')
    main()
