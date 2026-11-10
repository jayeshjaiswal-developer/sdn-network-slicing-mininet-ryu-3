"""
Ryu controller for dynamic network slicing (Step 2).

What it does:
- Learns MAC addresses like a normal L2 switch.
- Maps every IPv4 flow to a slice and sends it through that slice's queue.
- Drops traffic between hosts in different slices (isolation).
- NEW: exposes a REST API on port 8080 so slices can be listed, created,
  resized and deleted while the network is running. The AI agent in later
  steps will use exactly this API.

REST API (all JSON):
    GET    /slices                    list slices
    GET    /slices/{name}             one slice
    POST   /slices                    create   {"name", "hosts", "bandwidth_mbps"}
    PUT    /slices/{name}             change   {"hosts"?, "bandwidth_mbps"?}
    DELETE /slices/{name}             delete
    GET    /link                      capacity / allocated / free bandwidth

Run (sudo is needed so the controller can change OVS queues):
    sudo ~/ryu-env/bin/ryu-manager src/slice_controller.py
"""
import json
import os
import sys

from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, DEAD_DISPATCHER,
                                    MAIN_DISPATCHER, set_ev_cls)
from ryu.lib.packet import ether_types, ethernet, ipv4, packet
from ryu.ofproto import ofproto_v1_3
from webob import Response

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ovs_qos                                       # noqa: E402
from slice_manager import SliceError, SliceManager   # noqa: E402

CONFIG_PATH = os.environ.get("SLICE_CONFIG", os.path.join(HERE, "..", "config", "slices.json"))
APP_KEY = "slice_app"

PRIO_DEFAULT = 0     # table-miss: ask the controller
PRIO_SLICE = 10      # same slice: forward through its queue
PRIO_DROP = 20       # different slices: drop
FLOW_IDLE_TIMEOUT = 30


class SliceController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}   # dpid -> {mac: port}
        self.datapaths = {}     # dpid -> datapath (connected switches)

        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        self.trunk_ports = cfg["trunk_ports"]
        self.slices = SliceManager.from_config(CONFIG_PATH)
        self.logger.info("Loaded %d slices from %s", len(self.slices.list()), CONFIG_PATH)

        kwargs["wsgi"].register(SliceRestApi, {APP_KEY: self})
        self.logger.info("REST API ready on http://0.0.0.0:8080")

    # ---------------- switch handling ----------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def on_switch_connect(self, ev):
        dp = ev.msg.datapath
        parser, ofp = dp.ofproto_parser, dp.ofproto
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, PRIO_DEFAULT, parser.OFPMatch(), actions)
        self.logger.info("Switch %s connected", dp.id)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def on_state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            # A switch just added its ports: give the shared link its queues
            try:
                self.push_queues()
            except RuntimeError as err:
                self.logger.error("Could not set queues: %s", err)
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)
            self.mac_to_port.pop(dp.id, None)

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
            name, queue_id = self.slices.find_slice(ip.src, ip.dst)
            match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                    ipv4_src=ip.src, ipv4_dst=ip.dst)
            if name is None:
                self.logger.info("s%s DROP %s -> %s (not in the same slice)", dp.id, ip.src, ip.dst)
                self.add_flow(dp, PRIO_DROP, match, [], idle_timeout=FLOW_IDLE_TIMEOUT)
                return
            actions = [parser.OFPActionSetQueue(queue_id), parser.OFPActionOutput(out_port)]
            self.logger.info("s%s %s: %s -> %s via queue %s", dp.id, name, ip.src, ip.dst, queue_id)
            self.add_flow(dp, PRIO_SLICE, match, actions, idle_timeout=FLOW_IDLE_TIMEOUT)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions, data=data))

    # ---------------- applying slice changes ----------------

    def push_queues(self):
        """Make the OVS queues on the shared link match the slice table.

        Ports that don't exist yet (network not started) are skipped; they
        get their queues when their switch connects.
        """
        capacity_bps = int(self.slices.link_capacity_mbps * 1_000_000)
        rates = self.slices.queue_rates_bps()
        done = []
        for port in self.trunk_ports:
            if not ovs_qos.port_exists(port):
                continue
            try:
                ovs_qos.apply_queues(port, capacity_bps, rates)
            except RuntimeError as err:
                raise RuntimeError(f"{err} (is the controller running with sudo?)")
            done.append(port)
        if done:
            self.logger.info("Queues on %s: %s", ", ".join(done),
                             {q: f"{r / 1e6:g} Mbps" for q, r in sorted(rates.items())})

    def flush_ip_flows(self):
        """Delete all slice/drop rules so new packets are re-classified.

        Needed when slice membership changes. The table-miss rule stays, so
        the next packet of every flow comes back to the controller.
        """
        for dp in self.datapaths.values():
            parser, ofp = dp.ofproto_parser, dp.ofproto
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, command=ofp.OFPFC_DELETE,
                out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
                match=parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP)))
        self.logger.info("Flushed IP flow rules on %d switch(es)", len(self.datapaths))

    def apply_change(self, change, hosts_changed):
        """Run a slice-table change, then sync OVS. Rolls back if OVS fails."""
        snapshot = self.slices.snapshot()
        result = change()
        try:
            self.push_queues()
        except RuntimeError as err:
            self.slices.restore(snapshot)
            try:
                self.push_queues()      # put the switches back as they were
            except RuntimeError:
                pass
            raise SliceError(f"could not update switch queues: {err}", 500)
        if hosts_changed(result):
            self.flush_ip_flows()
        return result


# ---------------- REST API ----------------

def _json(data, status=200):
    return Response(status=status, content_type="application/json", charset="utf-8",
                    body=(json.dumps(data, indent=2) + "\n").encode())


def _body(req):
    try:
        data = json.loads(req.body or b"{}")
    except ValueError:
        raise SliceError("request body is not valid JSON")
    if not isinstance(data, dict):
        raise SliceError("request body must be a JSON object")
    return data


def _api(handler):
    """Turn SliceError into a JSON error response."""
    def wrapper(self, req, **kwargs):
        try:
            return handler(self, req, **kwargs)
        except SliceError as err:
            return _json({"error": err.message}, err.status)
    wrapper.__name__ = handler.__name__
    return wrapper


class SliceRestApi(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data[APP_KEY]

    @route("slices", "/slices", methods=["GET"])
    @_api
    def list_slices(self, req, **kwargs):
        return _json({"slices": self.app.slices.list(), "link": self.app.slices.link_status()})

    @route("slices", "/slices/{name}", methods=["GET"])
    @_api
    def get_slice(self, req, name, **kwargs):
        return _json(self.app.slices.get(name))

    @route("slices", "/slices", methods=["POST"])
    @_api
    def create_slice(self, req, **kwargs):
        b = _body(req)
        for key in ("name", "hosts", "bandwidth_mbps"):
            if key not in b:
                raise SliceError(f"missing '{key}'")
        created = self.app.apply_change(
            lambda: self.app.slices.create(b["name"], b["hosts"], b["bandwidth_mbps"]),
            hosts_changed=lambda _: True)
        self.app.logger.info("API: created slice %s", created)
        return _json(created, 201)

    @route("slices", "/slices/{name}", methods=["PUT"])
    @_api
    def update_slice(self, req, name, **kwargs):
        b = _body(req)
        updated, hosts_changed = self.app.apply_change(
            lambda: self.app.slices.update(name, hosts=b.get("hosts"),
                                           bandwidth_mbps=b.get("bandwidth_mbps")),
            hosts_changed=lambda result: result[1])
        self.app.logger.info("API: updated slice %s", updated)
        return _json(updated)

    @route("slices", "/slices/{name}", methods=["DELETE"])
    @_api
    def delete_slice(self, req, name, **kwargs):
        self.app.apply_change(lambda: self.app.slices.delete(name),
                              hosts_changed=lambda _: True)
        self.app.logger.info("API: deleted slice %s", name)
        return _json({"deleted": name})

    # Note: handler names must not clash with ControllerBase attributes
    # (e.g. `link`), or Ryu calls the attribute instead of the handler.
    @route("link", "/link", methods=["GET"])
    @_api
    def get_link_status(self, req, **kwargs):
        return _json(self.app.slices.link_status())
