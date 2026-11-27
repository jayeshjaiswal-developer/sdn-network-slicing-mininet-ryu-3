import os

import pytest

from slice_manager import SliceError, SliceManager

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "slices.json")


@pytest.fixture
def mgr():
    return SliceManager.from_config(CONFIG)


def test_loads_config(mgr):
    names = [s["name"] for s in mgr.list()]
    assert names == ["eMBB", "URLLC"]
    assert mgr.get("eMBB")["queue"] == 0
    assert mgr.get("URLLC")["queue"] == 1
    assert mgr.link_status() == {"capacity_mbps": 10, "allocated_mbps": 10, "free_mbps": 0}


def test_classification(mgr):
    assert mgr.find_slice("10.0.0.1", "10.0.0.3") == ("eMBB", 0)
    assert mgr.find_slice("10.0.0.2", "10.0.0.4") == ("URLLC", 1)
    assert mgr.find_slice("10.0.0.1", "10.0.0.4") == (None, None)


def test_resize_within_capacity(mgr):
    mgr.update("eMBB", bandwidth_mbps=5)
    s, hosts_changed = mgr.update("URLLC", bandwidth_mbps=5)
    assert s["bandwidth_mbps"] == 5 and not hosts_changed
    assert mgr.queue_rates_bps() == {0: 5_000_000, 1: 5_000_000}


def test_no_overprovisioning(mgr):
    with pytest.raises(SliceError) as e:
        mgr.update("URLLC", bandwidth_mbps=4)       # 7 + 4 > 10
    assert e.value.status == 409
    assert mgr.get("URLLC")["bandwidth_mbps"] == 3  # unchanged


def test_create_gets_next_free_queue(mgr):
    mgr.update("eMBB", bandwidth_mbps=6)
    s = mgr.create("mMTC", ["10.0.0.5", "10.0.0.6"], 1)
    assert s["queue"] == 2
    mgr.delete("URLLC")
    s = mgr.create("URLLC2", ["10.0.0.2", "10.0.0.4"], 3)
    assert s["queue"] == 1                           # freed id is reused


def test_host_can_only_be_in_one_slice(mgr):
    mgr.update("eMBB", bandwidth_mbps=6)
    with pytest.raises(SliceError) as e:
        mgr.create("mMTC", ["10.0.0.1"], 1)
    assert e.value.status == 409 and "eMBB" in e.value.message


def test_move_host_between_slices(mgr):
    mgr.update("URLLC", hosts=["10.0.0.2"])
    s, hosts_changed = mgr.update("eMBB", hosts=["10.0.0.1", "10.0.0.3", "10.0.0.4"])
    assert hosts_changed and "10.0.0.4" in s["hosts"]
    assert mgr.find_slice("10.0.0.1", "10.0.0.4") == ("eMBB", 0)


def test_failed_update_changes_nothing(mgr):
    with pytest.raises(SliceError):
        mgr.update("URLLC", hosts=["10.0.0.2"], bandwidth_mbps=50)
    assert mgr.get("URLLC")["hosts"] == ["10.0.0.2", "10.0.0.4"]


@pytest.mark.parametrize("name,hosts,bw", [
    ("", ["10.0.0.5"], 1),
    ("bad name", ["10.0.0.5"], 1),
    ("ok", [], 1),
    ("ok", ["not-an-ip"], 1),
    ("ok", ["10.0.0.5"], 0),
    ("ok", ["10.0.0.5"], "5"),
    ("ok", ["10.0.0.5"], True),
])
def test_rejects_bad_input(name, hosts, bw):
    mgr = SliceManager(10)
    with pytest.raises(SliceError) as e:
        mgr.create(name, hosts, bw)
    assert e.value.status == 400


def test_errors_for_missing_slice(mgr):
    for call in (lambda: mgr.get("x"), lambda: mgr.delete("x"),
                 lambda: mgr.update("x", bandwidth_mbps=1)):
        with pytest.raises(SliceError) as e:
            call()
        assert e.value.status == 404


def test_snapshot_restore(mgr):
    snap = mgr.snapshot()
    mgr.delete("eMBB")
    mgr.restore(snap)
    assert [s["name"] for s in mgr.list()] == ["eMBB", "URLLC"]


def test_latency_target(mgr):
    assert mgr.get("URLLC")["max_latency_ms"] == 10          # from config
    assert mgr.get("eMBB")["max_latency_ms"] is None
    mgr.update("URLLC", max_latency_ms=5)
    assert mgr.get("URLLC")["max_latency_ms"] == 5
    mgr.update("URLLC", bandwidth_mbps=3)                     # not given: kept
    assert mgr.get("URLLC")["max_latency_ms"] == 5
    mgr.update("URLLC", max_latency_ms=None)                  # null: removed
    assert mgr.get("URLLC")["max_latency_ms"] is None
    for bad in (0, -1, "5", True):
        with pytest.raises(SliceError):
            mgr.update("URLLC", max_latency_ms=bad)


def test_snapshot_keeps_latency_target(mgr):
    snap = mgr.snapshot()
    mgr.update("URLLC", max_latency_ms=50)
    mgr.restore(snap)
    assert mgr.get("URLLC")["max_latency_ms"] == 10
