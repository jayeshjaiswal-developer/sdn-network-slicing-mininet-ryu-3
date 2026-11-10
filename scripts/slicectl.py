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

Use --url to reach a controller somewhere else (default http://127.0.0.1:8080).
"""
import argparse
import json
import sys
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
    print(f"{'SLICE':<10}{'QUEUE':<7}{'MBPS':>6}  HOSTS")
    for s in rows:
        print(f"{s['name']:<10}{s['queue']:<7}{s['bandwidth_mbps']:>6g}  {', '.join(s['hosts'])}")
    link = result["link"]
    print(f"\nLink: {link['allocated_mbps']:g} of {link['capacity_mbps']:g} Mbps allocated, "
          f"{link['free_mbps']:g} Mbps free")


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
    a = p.parse_args()

    if a.cmd == "list":
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
    else:
        status, result = call(a.url, "DELETE", f"/slices/{a.name}")

    if status >= 400:
        sys.exit(f"Error {status}: {result.get('error', result)}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
