import json
import os
import stat
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


@pytest.fixture
def fake_ovs(tmp_path, monkeypatch):
    """Put a fake ovs-vsctl first on PATH, with ports s1-eth3 and s2-eth3."""
    state = tmp_path / "ovs.json"
    state.write_text(json.dumps({"ports": {"s1-eth3": None, "s2-eth3": None},
                                 "qos": {}, "queue": {}}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "ovs-vsctl"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} "
                   f"{os.path.join(ROOT, 'tests', 'fake_ovs_vsctl.py')} \"$@\"\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_OVS_STATE", str(state))

    def rates(port):
        db = json.loads(state.read_text())
        qos = db["ports"][port]
        if qos is None:
            return None
        return {int(q): db["queue"][u]["max_rate"] for q, u in db["qos"][qos]["queues"].items()}

    def queue_count():
        return len(json.loads(state.read_text())["queue"])

    rates.queue_count = queue_count
    return rates
