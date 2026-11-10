#!/usr/bin/env python3
"""Throughput, latency, jitter, loss, flow completion time, Jain fairness + plots."""
import argparse, csv, json, os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

WIRE = 42


def load_exp(d):
    meta = json.load(open(os.path.join(d, 'meta.json')))
    res, series = {}, {}
    t0 = None
    sends = {}
    for c in meta['classes']:
        sends[c['port']] = json.load(open(os.path.join(d, 'send_%d.json' % c['port'])))
    t0 = min(s['t_start'] for s in sends.values())
    for c in meta['classes']:
        s = sends[c['port']]
        with open(os.path.join(d, 'recv_%d.csv' % c['port'])) as f:
            rows = [list(map(float, r)) for r in list(csv.reader(f))[1:]]
        rows = np.array(rows).reshape(-1, 4)
        rows = rows[np.argsort(rows[:, 0])] if len(rows) else rows
        sent, recv = s['sent_packets'], len(rows)
        m = {'sent': sent, 'recv': recv, 'loss_pct': 100.0 * (sent - recv) / max(sent, 1)}
        if recv:
            lat = (rows[:, 2] - rows[:, 1]) * 1000
            span = max(rows[:, 2].max() - s['t_start'], 1e-3)
            m.update(avg_lat_ms=lat.mean(), p95_lat_ms=np.percentile(lat, 95),
                     jitter_ms=np.abs(np.diff(lat)).mean() if recv > 1 else 0.0,
                     thr_mbps=recv * (c['size'] + WIRE) * 8 / span / 1e6,
                     fct_s=span if c.get('total_bytes') else float('nan'))
            bins = np.floor(rows[:, 2] - t0).astype(int)
            n = int(meta['run_time_s']) + 2
            series[c['name']] = np.bincount(bins.clip(0, n - 1), weights=np.full(recv, (c['size'] + WIRE) * 8 / 1e6), minlength=n)
        else:
            m.update(avg_lat_ms=np.nan, p95_lat_ms=np.nan, jitter_ms=np.nan, thr_mbps=0.0, fct_s=np.nan)
        demand = c['rate_kbps'] * (c['size'] + WIRE) / c['size'] / 1000.0
        m['norm_thr'] = m['thr_mbps'] / demand
        res[c['name']] = m
    x = np.array([m['norm_thr'] for m in res.values()])
    jain = float(x.sum() ** 2 / (len(x) * (x ** 2).sum())) if (x ** 2).sum() else float('nan')
    return meta, res, series, jain


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', default='results')
    a = ap.parse_args()
    exps = sorted(e for e in os.listdir(a.results) if os.path.isfile(os.path.join(a.results, e, 'meta.json')))
    allres, jains = {}, {}
    with open(os.path.join(a.results, 'summary.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['experiment', 'class', 'sent', 'recv', 'loss_pct', 'thr_mbps', 'avg_lat_ms',
                    'p95_lat_ms', 'jitter_ms', 'fct_s', 'norm_thr', 'jain_index'])
        for e in exps:
            meta, res, series, jain = load_exp(os.path.join(a.results, e))
            allres[e], jains[e] = res, jain
            print('\n== %s   (Jain fairness index = %.3f)' % (e, jain))
            print('%-11s %7s %7s %8s %9s %9s %9s %8s' % ('class', 'loss%', 'Mbps', 'avg_ms', 'p95_ms', 'jit_ms', 'FCT_s', 'norm'))
            for name, m in res.items():
                print('%-11s %7.2f %7.2f %8.1f %9.1f %9.2f %9.1f %8.2f' % (
                    name, m['loss_pct'], m['thr_mbps'], m['avg_lat_ms'], m['p95_lat_ms'], m['jitter_ms'], m['fct_s'], m['norm_thr']))
                w.writerow([e, name, m['sent'], m['recv'], round(m['loss_pct'], 3), round(m['thr_mbps'], 3),
                            round(m['avg_lat_ms'], 2), round(m['p95_lat_ms'], 2), round(m['jitter_ms'], 3),
                            round(m['fct_s'], 2), round(m['norm_thr'], 3), round(jain, 4)])
            plt.figure(figsize=(9, 4))
            for name, s in series.items():
                plt.plot(s, label=name)
            for st in meta['schedule'][1:]:
                plt.axvline(st['t'], ls='--', c='gray')
                plt.text(st['t'], plt.ylim()[1] * 0.95, st['policy'], rotation=90, va='top')
            plt.axhline(meta['bottleneck_mbps'], c='k', ls=':', label='bottleneck')
            plt.xlabel('time (s)'); plt.ylabel('throughput (Mbps)'); plt.title('Throughput over time - ' + e)
            plt.legend(fontsize=7); plt.tight_layout()
            plt.savefig(os.path.join(a.results, 'timeseries_%s.png' % e), dpi=130); plt.close()

    classes = list(next(iter(allres.values())).keys())
    for metric, label in [('avg_lat_ms', 'Avg latency (ms)'), ('loss_pct', 'Packet loss (%)'),
                          ('thr_mbps', 'Throughput (Mbps)'), ('fct_s', 'Flow completion time (s)')]:
        plt.figure(figsize=(9, 4))
        wd = 0.8 / len(allres)
        for i, e in enumerate(allres):
            plt.bar(np.arange(len(classes)) + i * wd, [allres[e][c][metric] for c in classes], wd, label=e)
        plt.xticks(np.arange(len(classes)) + 0.4 - wd / 2, classes)
        plt.ylabel(label); plt.title(label + ' per class and policy'); plt.legend(fontsize=7); plt.tight_layout()
        plt.savefig(os.path.join(a.results, 'cmp_%s.png' % metric), dpi=130); plt.close()
    plt.figure(figsize=(5, 3.5))
    plt.bar(list(jains.keys()), list(jains.values())); plt.ylim(0, 1.05)
    plt.ylabel("Jain's fairness index"); plt.xticks(rotation=20); plt.tight_layout()
    plt.savefig(os.path.join(a.results, 'cmp_fairness.png'), dpi=130)
    print('\nSaved summary.csv and PNG plots in', a.results)


if __name__ == '__main__':
    main()
