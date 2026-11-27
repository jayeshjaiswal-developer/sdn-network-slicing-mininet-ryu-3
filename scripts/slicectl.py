#!/usr/bin/env python3
"""
slicectl: command-line client for the slice controller's REST API.

No extra packages needed (standard library only). Examples:

    python3 scripts/slicectl.py list
    python3 scripts/slicectl.py link
    python3 scripts/slicectl.py show URLLC
    python3 scripts/slicectl.py resize URLLC 5
    python3 scripts/slicectl.py create mMTC 10.0.0.5,10.0.0.6 1
    python3 scripts/slicectl.py hosts eMBB 10.0.0.1,10.0.0.3,10.0.0.4
    python3 scripts/slicectl.py delete mMTC
    python3 scripts/slicectl.py sla URLLC 10          # latency target (ms)
    python3 scripts/slicectl.py sla URLLC none        # remove the target
    python3 scripts/slicectl.py metrics               # live metrics, once
    python3 scripts/slicectl.py top                   # live metrics, refreshing

Use --url to reach a controller somewhere else (default http://127.0.0.1:8080).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _decode(raw):
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"error": raw.decode(errors="replace").strip()[:500]}


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, _decode(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, _decode(err.read())
    except urllib.error.URLError as err:
        sys.exit(f"Cannot reach the controller at {base} ({err.reason}). Is ryu-manager running?")


def ips(text):
    return [h.strip() for h in text.split(",") if h.strip()]


def print_table(result):
    rows = result["slices"]
    print(f"{'SLICE':<10}{'QUEUE':<7}{'MBPS':>6}{'TARGET':>9}  HOSTS")
    for s in rows:
        target = f"{s['max_latency_ms']:g}ms" if s.get("max_latency_ms") else "-"
        print(f"{s['name']:<10}{s['queue']:<7}{s['bandwidth_mbps']:>6g}{target:>9}  "
              f"{', '.join(s['hosts'])}")
    link = result["link"]
    print(f"\nLink: {link['allocated_mbps']:g} of {link['capacity_mbps']:g} Mbps allocated, "
          f"{link['free_mbps']:g} Mbps free")


def _fmt(value, spec, unit=""):
    return "-" if value is None else f"{value:{spec}}{unit}"


def print_metrics(m):
    """One screen of live metrics. '-' means no data yet."""
    stamp = time.strftime("%H:%M:%S", time.localtime(m["time"]))
    print(f"Slice metrics at {stamp} (switch polled every {m['poll_interval_s']:g} s)\n")
    print(f"{'SLICE':<9}{'ALLOC':>7}{'THROUGHPUT':>12}{'USE':>7}{'DROPS/s':>9}"
          f"{'LATENCY':>10}{'LOSS':>7}{'TARGET':>9}  SLA")
    for s in m["slices"]:
        print(f"{s['name']:<9}"
              f"{_fmt(s['allocated_mbps'], 'g'):>7}"
              f"{_fmt(s['throughput_mbps'], '.2f', ' Mbps'):>12}"
              f"{_fmt(None if s['utilization'] is None else s['utilization'] * 100, '.0f', '%'):>7}"
              f"{_fmt(s['drops_per_s'], '.0f'):>9}"
              f"{_fmt(s['latency_ms'], '.2f', ' ms'):>10}"
              f"{_fmt(s['loss_pct'], '.0f', '%'):>7}"
              f"{_fmt(s['max_latency_ms'], 'g', ' ms'):>9}  "
              f"{s['sla'].upper() if s['sla'] == 'violated' else s['sla']}")
    link = m["link"]
    print(f"\nLink: {link['allocated_mbps']:g} of {link['capacity_mbps']:g} Mbps allocated")
    for port, v in link["per_port"].items():
        print(f"  {port}: {v['mbps']:.2f} Mbps in use ({v['utilization'] * 100:.0f}% of the link)")
    if not link["per_port"]:
        print("  no traffic counters yet (is Mininet running?)")


def watch(url, interval):
    try:
        while True:
            status, m = call(url, "GET", "/metrics")
            print("\033[2J\033[H", end="")      # clear the screen
            if status >= 400:
                print(f"Error {status}: {m.get('error', m)}")
            else:
                print_metrics(m)
            print("\n(Ctrl+C to stop)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


def main():
    p = argparse.ArgumentParser(description="Control network slices at runtime.")
    p.add_argument("--url", default="http://127.0.0.1:8080", help="controller REST API address")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list all slices")
    sub.add_parser("link", help="show link capacity and free bandwidth")
    sp = sub.add_parser("show", help="show one slice"); sp.add_argument("name")
    sp = sub.add_parser("create", help="create a slice")
    sp.add_argument("name"); sp.add_argument("hosts", help="comma-separated IPs")
    sp.add_argument("mbps", type=float)
    sp = sub.add_parser("resize", help="change a slice's bandwidth")
    sp.add_argument("name"); sp.add_argument("mbps", type=float)
    sp = sub.add_parser("hosts", help="replace a slice's host list")
    sp.add_argument("name"); sp.add_argument("hosts", help="comma-separated IPs")
    sp = sub.add_parser("delete", help="delete a slice"); sp.add_argument("name")
    sp = sub.add_parser("sla", help="set or remove a slice's latency target")
    sp.add_argument("name"); sp.add_argument("ms", help="milliseconds, or 'none'")
    sub.add_parser("metrics", help="show live metrics once")
    sp = sub.add_parser("top", help="show live metrics, refreshing")
    sp.add_argument("-i", "--interval", type=float, default=2.0, help="seconds between refreshes")
    sp = sub.add_parser("history", help="recent samples of one slice (JSON)")
    sp.add_argument("name"); sp.add_argument("--seconds", type=float, default=60)
    a = p.parse_args()

    if a.cmd == "top":
        watch(a.url, a.interval)
        return

    if a.cmd == "metrics":
        status, result = call(a.url, "GET", "/metrics")
        if status == 200:
            print_metrics(result)
            return
    elif a.cmd == "list":
        status, result = call(a.url, "GET", "/slices")
        if status == 200:
            print_table(result)
            return
    elif a.cmd == "link":
        status, result = call(a.url, "GET", "/link")
    elif a.cmd == "show":
        status, result = call(a.url, "GET", f"/slices/{a.name}")
    elif a.cmd == "create":
        status, result = call(a.url, "POST", "/slices",
                              {"name": a.name, "hosts": ips(a.hosts), "bandwidth_mbps": a.mbps})
    elif a.cmd == "resize":
        status, result = call(a.url, "PUT", f"/slices/{a.name}", {"bandwidth_mbps": a.mbps})
    elif a.cmd == "hosts":
        status, result = call(a.url, "PUT", f"/slices/{a.name}", {"hosts": ips(a.hosts)})
    elif a.cmd == "sla":
        ms = None if a.ms.lower() == "none" else float(a.ms)
        status, result = call(a.url, "PUT", f"/slices/{a.name}", {"max_latency_ms": ms})
    elif a.cmd == "history":
        status, result = call(a.url, "GET", f"/metrics/{a.name}?seconds={a.seconds:g}")
    else:
        status, result = call(a.url, "DELETE", f"/slices/{a.name}")

    if status >= 400:
        sys.exit(f"Error {status}: {result.get('error', result)}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
