#!/usr/bin/env python3
"""
record_metrics: save the controller's live slice metrics to a CSV file.

Run it before an experiment, do the experiment (start traffic, resize slices),
then stop it with Ctrl+C (or give --duration). Turn the CSV into graphs with
scripts/plot_metrics.py. Standard library only.

    python3 scripts/record_metrics.py results/step3/run1.csv
    python3 scripts/record_metrics.py results/step3/run1.csv --duration 60

One row per slice per sample:
    t_s, slice, allocated_mbps, throughput_mbps, utilization, drops_per_s,
    latency_ms, loss_pct, max_latency_ms, sla
`t_s` is seconds since the recording started. Empty cell = no data yet.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

FIELDS = ["t_s", "slice", "allocated_mbps", "throughput_mbps", "utilization",
          "drops_per_s", "latency_ms", "loss_pct", "max_latency_ms", "sla"]


def fetch(url):
    with urllib.request.urlopen(url.rstrip("/") + "/metrics", timeout=5) as resp:
        return json.loads(resp.read())


def main():
    p = argparse.ArgumentParser(description="Record live slice metrics to CSV.")
    p.add_argument("csv", help="output file, e.g. results/step3/run1.csv")
    p.add_argument("--url", default="http://127.0.0.1:8080")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between samples")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    a = p.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
    start = time.time()
    rows = 0
    with open(a.csv, "w", newline="") as f:
        out = csv.DictWriter(f, fieldnames=FIELDS)
        out.writeheader()
        print(f"Recording to {a.csv} every {a.interval:g} s. Ctrl+C to stop.")
        try:
            while a.duration is None or time.time() - start < a.duration:
                tick = time.time()
                try:
                    m = fetch(a.url)
                except (urllib.error.URLError, OSError, ValueError) as err:
                    print(f"  controller not reachable ({err}); retrying", file=sys.stderr)
                else:
                    t = round(tick - start, 2)
                    for s in m["slices"]:
                        out.writerow({"t_s": t, "slice": s["name"],
                                      **{k: ("" if s.get(k) is None else s.get(k))
                                         for k in FIELDS[2:]},
                                      "allocated_mbps": s["allocated_mbps"]})
                        rows += 1
                    f.flush()
                    summary = "  ".join(
                        f"{s['name']}: {s['throughput_mbps'] if s['throughput_mbps'] is not None else '-'} Mbps"
                        f" / {s['latency_ms'] if s['latency_ms'] is not None else '-'} ms"
                        for s in m["slices"])
                    print(f"  t={t:6.1f}s  {summary}")
                time.sleep(max(0.0, a.interval - (time.time() - tick)))
        except KeyboardInterrupt:
            pass
    print(f"Saved {rows} rows to {a.csv}")


if __name__ == "__main__":
    main()
