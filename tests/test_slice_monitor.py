import pytest

from latency_prober import parse_ping, probe_pairs
from slice_monitor import SliceMonitor

SLICES = [
    {"name": "eMBB", "queue": 0, "hosts": ["10.0.0.1", "10.0.0.3"],
     "bandwidth_mbps": 7.0, "max_latency_ms": None},
    {"name": "URLLC", "queue": 1, "hosts": ["10.0.0.2", "10.0.0.4"],
     "bandwidth_mbps": 3.0, "max_latency_ms": 10.0},
]


def by_name(snapshot):
    return {s["name"]: s for s in snapshot}


def test_rate_from_counters():
    m = SliceMonitor()
    assert m.update_queue("s1-eth3", 0, 0, 0, 0, now=100.0) is None   # first poll: no rate
    # 1.75 MB in 2 s = 7 Mbps
    sample = m.update_queue("s1-eth3", 0, 1_750_000, 1200, 10, now=102.0)
    assert sample["mbps"] == pytest.approx(7.0)
    assert sample["pps"] == pytest.approx(600)
    assert sample["drops_ps"] == pytest.approx(5)


def test_snapshot_uses_busier_direction_and_utilization():
    m = SliceMonitor()
    for port, b in (("s1-eth3", 1_750_000), ("s2-eth3", 50_000)):   # data one way, ACKs back
        m.update_queue(port, 0, 0, 0, 0, now=0.0)
        m.update_queue(port, 0, b, 100, 0, now=2.0)
    e = by_name(m.snapshot(SLICES, now=3.0))["eMBB"]
    assert e["throughput_mbps"] == pytest.approx(7.0)
    assert e["utilization"] == pytest.approx(1.0)
    assert set(e["per_port"]) == {"s1-eth3", "s2-eth3"}
    u = by_name(m.snapshot(SLICES, now=3.0))["URLLC"]
    assert u["throughput_mbps"] is None and u["utilization"] is None   # no data, not zero


def test_counter_reset_skips_one_interval():
    m = SliceMonitor()
    m.update_queue("p", 0, 5_000_000, 5000, 0, now=0.0)
    assert m.update_queue("p", 0, 1000, 10, 0, now=2.0) is None        # queue recreated
    assert m.update_queue("p", 0, 251_000, 210, 0, now=4.0)["mbps"] == pytest.approx(1.0)


def test_stale_data_is_hidden():
    m = SliceMonitor()
    m.update_queue("p", 0, 0, 0, 0, now=0.0)
    m.update_queue("p", 0, 250_000, 10, 0, now=2.0)
    assert by_name(m.snapshot(SLICES, now=4.0))["eMBB"]["throughput_mbps"] is not None
    assert by_name(m.snapshot(SLICES, now=60.0))["eMBB"]["throughput_mbps"] is None


@pytest.mark.parametrize("rtts,loss,expected", [
    ([], [], "unknown"),                 # target set, no measurements yet
    ([2.0, 3.0, 4.0], [0, 0, 0], "ok"),
    ([20.0, 25.0, 30.0], [0, 0, 0], "violated"),
    ([None], [100], "violated"),         # nothing gets through
])
def test_latency_and_sla(rtts, loss, expected):
    m = SliceMonitor()
    for i, (r, l) in enumerate(zip(rtts, loss)):
        m.record_latency("URLLC", r, l, now=float(i))
    u = by_name(m.snapshot(SLICES, now=float(len(rtts))))["URLLC"]
    assert u["sla"] == expected
    assert by_name(m.snapshot(SLICES, now=0.0))["eMBB"]["sla"] == "no-target"


def test_latency_averages_last_three():
    m = SliceMonitor()
    for i, r in enumerate([100.0, 1.0, 2.0, 3.0]):
        m.record_latency("URLLC", r, 0, now=float(i))
    assert by_name(m.snapshot(SLICES, now=4.0))["URLLC"]["latency_ms"] == pytest.approx(2.0)


def test_forget_queue_and_history():
    m = SliceMonitor()
    m.update_queue("p", 1, 0, 0, 0, now=0.0)
    m.update_queue("p", 1, 250_000, 10, 0, now=2.0)
    m.record_latency("URLLC", 1.5, 0, now=2.0)
    h = m.history("URLLC", 1, since=0.0)
    assert len(h["rates"]["p"]) == 1 and len(h["latency"]) == 1
    m.forget_queue(1)
    m.forget_slice("URLLC")
    assert m.history("URLLC", 1, since=0.0) == {"rates": {}, "latency": []}


def test_link_snapshot_sums_slices():
    m = SliceMonitor()
    for q, b in ((0, 1_750_000), (1, 750_000)):
        m.update_queue("s1-eth3", q, 0, 0, 0, now=0.0)
        m.update_queue("s1-eth3", q, b, 10, 0, now=2.0)
    link = m.link_snapshot(10.0, now=2.0)
    assert link["s1-eth3"]["mbps"] == pytest.approx(10.0)
    assert link["s1-eth3"]["utilization"] == pytest.approx(1.0)


def test_parse_ping():
    out = ("--- 10.0.0.4 ping statistics ---\n"
           "3 packets transmitted, 3 received, 0% packet loss, time 402ms\n"
           "rtt min/avg/max/mdev = 0.051/0.083/0.120/0.028 ms\n")
    assert parse_ping(out) == (pytest.approx(0.083), 0.0)
    lost = "3 packets transmitted, 0 received, 100% packet loss, time 2045ms\n"
    assert parse_ping(lost) == (None, 100.0)
    assert parse_ping("ping: connect: Network is unreachable") == (None, 100.0)


def test_probe_pairs():
    hosts = {"10.0.0.1": "h1", "10.0.0.2": "h2", "10.0.0.3": "h3"}   # h4 missing
    pairs = probe_pairs(SLICES, hosts)
    assert pairs == [("eMBB", "h1", "10.0.0.3")]                      # URLLC has only one host
