"""Dynamic traffic generation: random flows (ports, sizes, rates, start times) drawn from profiles."""
import random


def generate_classes(g, run_time):
    """Create g['num_flows'] flows. Ports are random inside each profile's port_range."""
    rng = random.Random(g.get('seed'))
    profs = g['profiles']
    lo_s, hi_s = g.get('start_window_s', [0, 10])
    used, classes = set(), []
    for _ in range(int(g['num_flows'])):
        p = rng.choices(profs, weights=[x.get('weight', 1) for x in profs])[0]
        free = [x for x in range(p['port_range'][0], p['port_range'][1] + 1) if x not in used]
        port = rng.choice(free)
        used.add(port)
        start = rng.randint(lo_s, hi_s)
        c = {'name': '%s_%d' % (p['name'], port), 'port': port,
             'size': rng.randint(*p['size']), 'rate_kbps': rng.randint(*p['rate_kbps']),
             'start_s': start, 'realtime': p.get('realtime', False), 'priority': p.get('priority', 4)}
        if 'total_bytes' in p:
            c['total_bytes'] = rng.randint(*p['total_bytes'])
        else:
            c['duration_s'] = max(5, int(run_time) - start - 5)
        classes.append(c)
    return classes


def rules_from_profiles(profiles):
    """Classification rules the controller uses to map any destination port to a class."""
    return [{'name': p['name'], 'ports': p['port_range'], 'priority': p.get('priority', 4),
             'realtime': p.get('realtime', False)} for p in profiles]


def offered_mbps(classes):
    return sum(c['rate_kbps'] * (c['size'] + 42) / c['size'] for c in classes) / 1000.0
