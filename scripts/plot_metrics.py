#!/usr/bin/env python3
"""
plot_metrics: turn a CSV from record_metrics.py into a PNG for the thesis.

    python3 scripts/plot_metrics.py results/step3/run1.csv
    python3 scripts/plot_metrics.py results/step3/run1.csv -o figure.png --title "Resize under load"

Two panels that share the time axis (one measure per panel, never two
y-scales on one chart):
    top     throughput of each slice (solid) and its allocation (dashed)
    bottom  round-trip latency of each slice (solid) and its target (dashed)

Needs matplotlib:  pip3 install matplotlib   (or: sudo apt install python3-matplotlib)
"""
import argparse
import csv
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

# Fixed categorical order: the first slice seen always gets the first colour,
# so a slice keeps its colour across every figure of the same experiment.
SERIES = ["#257de0", "#f47230", "#00b894", "#f2a600", "#ee7fac", "#008a17"]
INK, INK_SOFT, GRID = "#191919", "#666666", "#e6e6e6"


def num(v):
    return float(v) if v not in ("", None) else math.nan


def load(path):
    data, order = {}, []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            name = r["slice"]
            if name not in data:
                order.append(name)
                data[name] = {k: [] for k in ("t", "alloc", "thr", "lat", "target")}
            d = data[name]
            d["t"].append(num(r["t_s"]))
            d["alloc"].append(num(r["allocated_mbps"]))
            d["thr"].append(num(r["throughput_mbps"]))
            d["lat"].append(num(r["latency_ms"]))
            d["target"].append(num(r["max_latency_ms"]))
    return order, data


def last_value(ts, ys):
    for t, y in zip(reversed(ts), reversed(ys)):
        if not math.isnan(y):
            return t, y
    return None


def label_ends(ax, ends, min_gap_frac=0.06):
    """Name each line at its last point, pushing labels apart when they would overlap."""
    if not ends:
        return
    lo, hi = ax.get_ylim()
    gap = (hi - lo) * min_gap_frac
    ends = sorted(ends, key=lambda e: e[2])          # (name, t, y), bottom to top
    placed = []
    for name, t, y in ends:
        if placed and y - placed[-1] < gap:
            y = placed[-1] + gap
        placed.append(y)
        ax.annotate(name, (t, y), xytext=(6, 0), textcoords="offset points",
                    va="center", color=INK, fontsize=9, annotation_clip=False)


def style(ax, ylabel):
    ax.set_ylabel(ylabel, color=INK_SOFT)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_SOFT, length=0)
    ax.set_ylim(bottom=0)


def main():
    p = argparse.ArgumentParser(description="Plot slice metrics recorded by record_metrics.py.")
    p.add_argument("csv")
    p.add_argument("-o", "--out", help="output PNG (default: next to the CSV)")
    p.add_argument("--title", help="figure title")
    a = p.parse_args()

    order, data = load(a.csv)
    if not order:
        raise SystemExit(f"{a.csv} has no rows")
    out = a.out or os.path.splitext(a.csv)[0] + ".png"

    plt.rcParams.update({"font.size": 10, "font.family": "DejaVu Sans"})
    fig, (ax_t, ax_l) = plt.subplots(2, 1, figsize=(9, 6.5), sharex=True,
                                     gridspec_kw={"hspace": 0.28})
    has_latency = False
    ends_t, ends_l = [], []
    for i, name in enumerate(order):
        c = SERIES[i % len(SERIES)]
        d = data[name]
        ax_t.plot(d["t"], d["thr"], color=c, linewidth=2, label=f"{name} throughput")
        ax_t.step(d["t"], d["alloc"], where="post", color=c, linewidth=1.2,
                  linestyle=(0, (4, 3)), label=f"{name} allocation")
        end = last_value(d["t"], d["thr"])
        if end:
            ends_t.append((name, *end))

        if any(not math.isnan(v) for v in d["lat"]):
            has_latency = True
            ax_l.plot(d["t"], d["lat"], color=c, linewidth=2, marker="o", markersize=3,
                      label=f"{name} latency")
            end = last_value(d["t"], d["lat"])
            if end:
                ends_l.append((name, *end))
        if any(not math.isnan(v) for v in d["target"]):
            ax_l.step(d["t"], d["target"], where="post", color=c, linewidth=1.2,
                      linestyle=(0, (4, 3)), label=f"{name} target")

    style(ax_t, "Throughput (Mbps)")
    style(ax_l, "Round-trip latency (ms)")
    label_ends(ax_t, ends_t)
    label_ends(ax_l, ends_l)
    ax_l.set_xlabel("Time since recording started (s)", color=INK_SOFT)
    ax_t.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=9)
    if has_latency:
        ax_l.legend(loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False, fontsize=9)
    else:
        ax_l.text(0.5, 0.5, "no latency data in this recording", transform=ax_l.transAxes,
                  ha="center", va="center", color=INK_SOFT)
    ax_t.set_title("Throughput per slice (solid) vs allocated bandwidth (dashed)",
                   loc="left", color=INK, fontsize=10)
    ax_l.set_title("Latency per slice (solid) vs latency target (dashed)",
                   loc="left", color=INK, fontsize=10)
    if a.title:
        fig.suptitle(a.title, x=0.06, ha="left", fontsize=13, fontweight="bold", color=INK)

    fig.savefig(out, dpi=200, bbox_inches="tight", facecolor="white")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
