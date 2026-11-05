"""
Ryu controller for basic network slicing.

- Learns MAC addresses like a normal L2 switch.
- Maps every IPv4 flow to a slice using the host IPs in SLICES.
- Sends each slice's traffic through its own OVS queue (bandwidth share).
- Drops traffic between hosts in different slices (isolation).

Run:
    source ~/ryu-env/bin/activate
    ryu-manager src/slice_controller.py
"""
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib.packet import ether_types, ethernet, ipv4, packet
from ryu.ofproto import ofproto_v1_3

# slice name -> (OVS queue id, member host IPs)
# Later phases will change this table at runtime from the AI agent.
SLICES = {
    "eMBB": (0, {"10.0.0.1", "10.0.0.3"}),
    "URLLC": (1, {"10.0.0.2", "10.0.0.4"}),
}


def find_slice(src_ip, dst_ip):
    for name, (queue_id, members) in SLICES.items():
        if src_ip in members and dst_ip in members:
            return name, queue_id
    return None, None


class SliceController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}   # dpid -> {mac: port}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_switch_connect(self, ev):
        dp = ev.msg.datapath
        parser, ofp = dp.ofproto_parser, dp.ofproto
        # Table-miss: send unknown packets to the controller
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, parser.OFPMatch(), actions)
        self.logger.info("Switch %s connected", dp.id)

    def add_flow(self, dp, priority, match, actions, idle_timeout=0):
        parser, ofp = dp.ofproto_parser, dp.ofproto
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority, match=match,
                                      instructions=inst, idle_timeout=idle_timeout))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def on_packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        parser, ofp = dp.ofproto_parser, dp.ofproto
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        table = self.mac_to_port.setdefault(dp.id, {})
        table[eth.src] = in_port
        out_port = table.get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        ip = pkt.get_protocol(ipv4.ipv4)
        if ip and out_port != ofp.OFPP_FLOOD:
            name, queue_id = find_slice(ip.src, ip.dst)
            match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                    ipv4_src=ip.src, ipv4_dst=ip.dst)
            if name is None:
                # Different slices: install a drop rule (no actions)
                self.logger.info("s%s DROP %s -> %s (cross-slice)", dp.id, ip.src, ip.dst)
                self.add_flow(dp, 20, match, [], idle_timeout=30)
                return
            actions = [parser.OFPActionSetQueue(queue_id),
                       parser.OFPActionOutput(out_port)]
            self.logger.info("s%s %s: %s -> %s via queue %s",
                             dp.id, name, ip.src, ip.dst, queue_id)
            self.add_flow(dp, 10, match, actions, idle_timeout=30)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions, data=data))
