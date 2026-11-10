"""
Ryu SDN controller for the QoS Traffic Management System.

* Classifies UDP flows by destination port, maps them to OVS queues (set_queue).
* Polls flow counters, detects congestion, re-assigns queues at run time.
* Pluggable policies:  none (best effort) | static (class priority) | srtf (shortest remaining flow first)
* Everything is configured at run time through a REST API (nothing hardcoded):
    POST /qos/setup    {ingress_dpid, uplink_port, capacity_bps, congestion_threshold, poll_interval}
    POST /qos/flows    {flows:[{name, port, priority, realtime, total_wire_bytes}]}   (also updates live)
    POST /qos/policy   {policy: "none"|"static"|"srtf"}
    POST /qos/reset
    GET  /qos/status
Run:  ryu-manager controller.py
"""
import json
import time

from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import ethernet, packet
from ryu.ofproto import ofproto_v1_3
from webob import Response

QOS_PRIO = 100        # priority of per-flow QoS entries
BE_QUEUE = 4          # lowest-priority / best-effort queue
WIRE_OVERHEAD = 42    # eth(14)+ip(20)+udp(8)
INF = float('inf')


class QoSController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dps = {}
        self.mac_to_port = {}
        self.flows = {}          # udp_dst port -> state dict
        self.policy = 'none'
        self.congested = False
        self.history = []
        self.t0 = time.time()
        self.setup = {'ingress_dpid': 1, 'uplink_port': None, 'capacity_bps': 10e6,
                      'congestion_threshold': 0.9, 'poll_interval': 1.0}
        self.policies = {'none': self._p_none, 'static': self._p_static, 'srtf': self._p_srtf}
        kwargs['wsgi'].register(QoSRest, {'app': self})
        hub.spawn(self._monitor)

    # ---------------- basic switching ----------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        p, ofp = dp.ofproto_parser, dp.ofproto
        self.dps[dp.id] = dp
        self._add(dp, 0, p.OFPMatch(), [p.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])

    def _add(self, dp, prio, match, actions, cmd=None):
        ofp, p = dp.ofproto, dp.ofproto_parser
        inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, command=ofp.OFPFC_ADD if cmd is None else cmd,
                                 priority=prio, match=match, instructions=inst))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        dp, ofp, p = msg.datapath, msg.datapath.ofproto, msg.datapath.ofproto_parser
        in_port = msg.match['in_port']
        eth = packet.Packet(msg.data).get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == 0x88cc:
            return
        table = self.mac_to_port.setdefault(dp.id, {})
        table[eth.src] = in_port
        out = table.get(eth.dst, ofp.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out)]
        if out != ofp.OFPP_FLOOD:
            self._add(dp, 1, p.OFPMatch(in_port=in_port, eth_dst=eth.dst), actions)
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id, in_port=in_port,
                                   actions=actions, data=data))

    # ---------------- QoS flow management ----------------
    def configure(self, d):
        for k in self.setup:
            if k in d:
                self.setup[k] = d[k]

    def register(self, items):
        now = time.time()
        for d in items:
            port = int(d['port'])
            f = self.flows.get(port)
            if f is None:
                f = dict(name=str(port), port=port, priority=BE_QUEUE, realtime=False, total_wire=None,
                         queue=BE_QUEUE, installed=False, bytes=0, last_cnt=0, t_last=now, rate_bps=0.0)
                self.flows[port] = f
            f['name'] = d.get('name', f['name'])
            if 'priority' in d:
                f['priority'] = int(d['priority'])
            if 'realtime' in d:
                f['realtime'] = bool(d['realtime'])
            if 'total_wire_bytes' in d:
                f['total_wire'] = d['total_wire_bytes']
            self._push(port, f['queue'])
        self._evaluate()

    def reset(self):
        dp = self.dps.get(self.setup['ingress_dpid'])
        if dp:
            ofp, p = dp.ofproto, dp.ofproto_parser
            for port, f in self.flows.items():
                if f['installed']:
                    dp.send_msg(p.OFPFlowMod(
                        datapath=dp, command=ofp.OFPFC_DELETE_STRICT, priority=QOS_PRIO,
                        match=p.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=port),
                        out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY))
        self.flows, self.history = {}, []
        self.congested, self.policy = False, 'none'
        self.t0 = time.time()

    def set_policy(self, name):
        if name not in self.policies:
            raise ValueError('unknown policy %s (have: %s)' % (name, list(self.policies)))
        self.policy = name
        self.history.append({'t': round(time.time() - self.t0, 2), 'event': 'policy', 'policy': name})
        self._evaluate()

    def _push(self, port, queue):
        dp = self.dps.get(self.setup['ingress_dpid'])
        if dp is None or self.setup['uplink_port'] is None:
            return
        ofp, p = dp.ofproto, dp.ofproto_parser
        f = self.flows[port]
        match = p.OFPMatch(eth_type=0x0800, ip_proto=17, udp_dst=port)
        actions = [p.OFPActionSetQueue(queue), p.OFPActionOutput(int(self.setup['uplink_port']))]
        cmd = ofp.OFPFC_MODIFY_STRICT if f['installed'] else ofp.OFPFC_ADD   # MODIFY keeps counters
        self._add(dp, QOS_PRIO, match, actions, cmd)
        if f['installed'] and f['queue'] != queue:
            self.history.append({'t': round(time.time() - self.t0, 2), 'event': 'queue', 'flow': f['name'],
                                 'from': f['queue'], 'to': queue, 'policy': self.policy,
                                 'congested': self.congested})
        f['queue'], f['installed'] = queue, True

    # ---------------- monitoring ----------------
    def _monitor(self):
        while True:
            hub.sleep(float(self.setup['poll_interval']))
            dp = self.dps.get(self.setup['ingress_dpid'])
            if dp is None or self.setup['uplink_port'] is None or not self.flows:
                continue
            dp.send_msg(dp.ofproto_parser.OFPFlowStatsRequest(dp))

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _stats(self, ev):
        now = time.time()
        for st in ev.msg.body:
            if st.priority != QOS_PRIO:
                continue
            f = self.flows.get(st.match.get('udp_dst'))
            if not f:
                continue
            cnt = st.byte_count
            delta = cnt - f['last_cnt'] if cnt >= f['last_cnt'] else cnt
            f['last_cnt'] = cnt
            f['bytes'] += delta
            dt = max(now - f['t_last'], 1e-3)
            f['t_last'] = now
            f['rate_bps'] = delta * 8 / dt
        self._evaluate()

    def _remaining(self, f):
        return INF if f['total_wire'] is None else max(f['total_wire'] - f['bytes'], 0)

    def _active(self):
        return [f for f in self.flows.values() if f['rate_bps'] > 1000]

    def _evaluate(self):
        offered = sum(f['rate_bps'] for f in self.flows.values())
        self.congested = offered >= self.setup['congestion_threshold'] * self.setup['capacity_bps']
        if self.policy == 'none' or not self.congested:
            target = self._p_none()          # no congestion -> no need to prioritize
        else:
            target = self.policies[self.policy]()
        for port, q in target.items():
            if self.flows[port]['queue'] != q:
                self._push(port, q)

    # ---------------- policies (add your own here) ----------------
    def _p_none(self):
        return {p: BE_QUEUE for p in self.flows}

    def _p_static(self):
        return {p: min(max(int(f['priority']), 1), BE_QUEUE) for p, f in self.flows.items()}

    def _p_srtf(self):
        """Real-time flows pinned to queue 1; others ranked by remaining bytes (smallest first)."""
        target = {p: BE_QUEUE for p in self.flows}
        for p, f in self.flows.items():
            if f['realtime']:
                target[p] = 1
        rest = sorted((self._remaining(f), f['port']) for f in self._active() if not f['realtime'])
        for rank, (_, port) in enumerate(rest):
            target[port] = min(2 + rank, BE_QUEUE)
        return target

    def status(self):
        return {'policy': self.policy, 'congested': self.congested, 'setup': self.setup,
                'flows': {str(p): {'name': f['name'], 'queue': f['queue'], 'rate_mbps': round(f['rate_bps'] / 1e6, 3),
                                   'bytes': f['bytes'],
                                   'remaining_bytes': None if self._remaining(f) == INF else self._remaining(f)}
                          for p, f in self.flows.items()},
                'history': self.history}


class QoSRest(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data['app']

    def _json(self, body, status=200):
        return Response(content_type='application/json', body=json.dumps(body).encode(), status=status)

    @route('qos', '/qos/setup', methods=['POST'])
    def setup(self, req, **kw):
        self.app.configure(req.json)
        return self._json({'ok': True})

    @route('qos', '/qos/flows', methods=['POST'])
    def flows(self, req, **kw):
        self.app.register(req.json['flows'])
        return self._json({'ok': True})

    @route('qos', '/qos/policy', methods=['POST'])
    def policy(self, req, **kw):
        try:
            self.app.set_policy(req.json['policy'])
        except ValueError as e:
            return self._json({'error': str(e)}, 400)
        return self._json({'ok': True, 'policy': self.app.policy})

    @route('qos', '/qos/reset', methods=['POST'])
    def reset(self, req, **kw):
        self.app.reset()
        return self._json({'ok': True})

    @route('qos', '/qos/status', methods=['GET'])
    def status(self, req, **kw):
        return self._json(self.app.status())
