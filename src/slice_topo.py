#!/usr/bin/env python3
r"""
Two-slice Mininet topology for SDN network slicing.

    h1 (eMBB)  \                      / h3 (eMBB)
                s1 ===== 10 Mbps ===== s2
    h2 (URLLC) /    shared link       \ h4 (URLLC)

The shared s1<->s2 link gets one OVS HTB queue per slice. The controller
creates those queues when the switches connect (from config/slices.json)
and changes them through its REST API while the network runs; this script
builds the network, checks that the queues appeared, and (Step 3) runs the
latency prober that pings inside each slice and reports to the controller.

Run (after starting the controller):
    sudo python3 src/slice_topo.py
"""
import json
import os
import time

from mininet.cli import CLI
from mininet.log import info, setLogLevel, warn
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

import ovs_qos
from latency_prober import LatencyProber

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("SLICE_CONFIG", os.path.join(HERE, "..", "config", "slices.json"))


class SliceTopo(Topo):
    def build(self):
        s1 = self.addSwitch("s1", protocols="OpenFlow13")
        s2 = self.addSwitch("s2", protocols="OpenFlow13")
        h1 = self.addHost("h1", ip="10.0.0.1/24")
        h2 = self.addHost("h2", ip="10.0.0.2/24")
        h3 = self.addHost("h3", ip="10.0.0.3/24")
        h4 = self.addHost("h4", ip="10.0.0.4/24")
        # Link order fixes port numbers: hosts get ports 1-2, the shared link port 3
        self.addLink(h1, s1)
        self.addLink(h2, s1)
        self.addLink(h3, s2)
        self.addLink(h4, s2)
        self.addLink(s1, s2)


def wait_for_queues(ports, timeout=10):
    """The controller sets the queues when the switches connect; wait for them."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        missing = [p for p in ports if not ovs_qos.has_qos(p)]
        if not missing:
            for p in ports:
                info(f"*** QoS queues configured on {p}\n")
            return
        time.sleep(0.5)
    warn(f"*** WARNING: no queues on {', '.join(missing)}. Slicing is NOT active.\n"
         "*** Is the controller running, and was it started with sudo?\n")


def main():
    setLogLevel("info")
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    net = Mininet(
        topo=SliceTopo(),
        switch=OVSSwitch,
        controller=lambda name: RemoteController(name, ip="127.0.0.1", port=6633),
        autoSetMacs=True,
    )
    ovs_qos.clear_all()     # leftovers from a crashed run
    net.start()
    wait_for_queues(cfg["trunk_ports"])
    prober = LatencyProber(net, interval=float(cfg.get("probe_interval_s", 2.0)))
    prober.start()
    info("*** Latency prober running (pings inside each slice every few seconds)\n")

    info("\n*** Step 1 tests:\n"
         "  h3 iperf -s &\n  h4 iperf -s &\n"
         "  h1 iperf -c 10.0.0.3 -t 10   (eMBB, expect ~7 Mbps)\n"
         "  h2 iperf -c 10.0.0.4 -t 10   (URLLC, expect ~3 Mbps)\n"
         "  h1 ping -c 3 10.0.0.4        (cross-slice, expect 100% loss)\n"
         "*** Step 2: change slices from another terminal with scripts/slicectl.py\n"
         "*** Step 3: watch live metrics with: python3 scripts/slicectl.py top\n\n")
    try:
        CLI(net)
    finally:
        prober.stop()
        net.stop()
        ovs_qos.clear_all()


if __name__ == "__main__":
    main()
