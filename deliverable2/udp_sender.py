#!/usr/bin/env python3
"""Paced UDP traffic generator for one traffic class (run inside a Mininet host)."""
import argparse, json, socket, struct, time

HDR = struct.Struct('!IdQ')  # seq, send timestamp, declared flow size on the wire (0 = unknown)

ap = argparse.ArgumentParser()
ap.add_argument('--dst', required=True)
ap.add_argument('--port', type=int, required=True)
ap.add_argument('--size', type=int, default=1000, help='UDP payload bytes')
ap.add_argument('--rate-kbps', type=float, required=True)
ap.add_argument('--duration', type=float, default=10, help='used when --packets is 0')
ap.add_argument('--packets', type=int, default=0, help='finite flow: stop after N packets')
ap.add_argument('--delay', type=float, default=0, help='start offset (s)')
ap.add_argument('--name', default='flow')
ap.add_argument('--out', required=True)
a = ap.parse_args()

time.sleep(a.delay)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
interval = a.size * 8 / (a.rate_kbps * 1000.0)
pad = b'\0' * (a.size - HDR.size)   # --size must be >= 20
total_wire = a.packets * (a.size + 42) if a.packets else 0
t_start = time.time()
nxt, seq = t_start, 0
while True:
    now = time.time()
    if a.packets and seq >= a.packets:
        break
    if not a.packets and now - t_start >= a.duration:
        break
    if now < nxt:
        time.sleep(nxt - now)
    try:
        sock.sendto(HDR.pack(seq, time.time(), total_wire) + pad, (a.dst, a.port))
    except OSError:
        pass
    seq += 1
    nxt += interval

json.dump({'name': a.name, 'port': a.port, 'sent_packets': seq, 'sent_payload_bytes': seq * a.size,
           't_start': t_start, 't_end': time.time()}, open(a.out, 'w'))
