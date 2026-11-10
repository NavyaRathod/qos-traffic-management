#!/usr/bin/env python3
"""Receives all traffic classes on the server; writes per-packet logs on SIGTERM."""
import argparse, csv, os, selectors, signal, socket, struct, sys, time

HDR = struct.Struct('!Id')

ap = argparse.ArgumentParser()
ap.add_argument('--ports', type=int, nargs='+', required=True)
ap.add_argument('--out', required=True)
a = ap.parse_args()

sel = selectors.DefaultSelector()
data = {}
for p in a.ports:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    s.bind(('0.0.0.0', p))
    s.setblocking(False)
    sel.register(s, selectors.EVENT_READ, p)
    data[p] = []

def dump(*_):
    os.makedirs(a.out, exist_ok=True)
    for p, rows in data.items():
        with open(os.path.join(a.out, f'recv_{p}.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['seq', 'send_ts', 'recv_ts', 'payload_bytes'])
            w.writerows(rows)
    sys.exit(0)

signal.signal(signal.SIGTERM, dump)
signal.signal(signal.SIGINT, dump)

while True:
    for key, _ in sel.select():
        try:
            pkt, _addr = key.fileobj.recvfrom(4096)
        except BlockingIOError:
            continue
        seq, ts = HDR.unpack_from(pkt)
        data[key.data].append((seq, ts, time.time(), len(pkt)))
