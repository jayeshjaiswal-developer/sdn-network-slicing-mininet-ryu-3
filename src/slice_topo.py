#!/usr/bin/env python3
r"""
Two-slice Mininet topology for SDN network slicing.

    h1 (eMBB)  \                      / h3 (eMBB)
                s1 ===== 10 Mbps ===== s2
    h2 (URLLC) /    shared link       \ h4 (URLLC)

The shared s1<->s2 link is split with OVS HTB queues:
    queue 0 -> eMBB  slice, max 7 Mbps
    queue 1 -> URLLC slice, max 3 Mbps
The Ryu controller (slice_controller.py) decides which queue each flow uses.

Run (after starting the controller):
    sudo python3 src/slice_topo.py
"""
import subprocess

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

LINK_MAX = 10_000_000   # bits/s on the shared link
SLICE_QUEUES = {
    0: 7_000_000,       # eMBB
    1: 3_000_000,       # URLLC
}


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


def configure_queues(port):
    """Attach HTB queues for each slice to an OVS port."""
    cmd = [
        "ovs-vsctl", "--", "set", "port", port, "qos=@newqos",
        "--", "--id=@newqos", "create", "qos", "type=linux-htb",
        f"other-config:max-rate={LINK_MAX}",
        "queues=" + ",".join(f"{q}=@q{q}" for q in SLICE_QUEUES),
    ]
    for q, rate in SLICE_QUEUES.items():
        cmd += ["--", f"--id=@q{q}", "create", "queue",
                f"other-config:max-rate={rate}"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    info(f"*** QoS queues configured on {port}\n")


def clear_queues():
    subprocess.run(["ovs-vsctl", "--all", "destroy", "qos", "--",
                    "--all", "destroy", "queue"], check=False)


def main():
    setLogLevel("info")
    net = Mininet(
        topo=SliceTopo(),
        switch=OVSSwitch,
        controller=lambda name: RemoteController(name, ip="127.0.0.1", port=6633),
        autoSetMacs=True,
    )
    net.start()
    configure_queues("s1-eth3")
    configure_queues("s2-eth3")

    info("\n*** Try these tests:\n"
         "  h3 iperf -s &\n  h4 iperf -s &\n"
         "  h1 iperf -c 10.0.0.3 -t 10   (eMBB, expect ~7 Mbps)\n"
         "  h2 iperf -c 10.0.0.4 -t 10   (URLLC, expect ~3 Mbps)\n"
         "  h1 ping -c 3 10.0.0.4        (cross-slice, expect 100% loss)\n\n")
    try:
        CLI(net)
    finally:
        net.stop()
        clear_queues()


if __name__ == "__main__":
    main()
