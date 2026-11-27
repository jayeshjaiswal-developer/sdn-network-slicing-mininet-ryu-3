# Troubleshooting

## Ryu installation problems

Ryu (4.34) is no longer maintained and its installer breaks on modern Python
and setuptools. `setup.sh` already handles all of the fixes below; this page
explains them in case you install by hand.

### `AttributeError: ... has no attribute 'get_script_args'`
**Cause:** pip builds Ryu in an isolated environment with the newest
setuptools, which removed something Ryu's installer uses.
**Fix:** pin setuptools and turn off build isolation:
```bash
source ~/ryu-env/bin/activate
pip install setuptools==67.6.1 wheel
pip install --no-build-isolation ryu
```

### `Unknown distribution option: 'pbr'` / `Multiple top-level packages discovered`
**Cause:** with build isolation off, Ryu's helper package `pbr` is no longer
installed automatically.
**Fix:**
```bash
pip install "pbr<7"
pip install --no-build-isolation ryu
```

### `ryu-manager: command not found`
Ryu didn't install (check for an error above it), or the virtual environment
isn't active. Run `source ~/ryu-env/bin/activate` first.

### Ryu starts but crashes with an eventlet / dnspython error
Pin the versions known to work with Ryu:
```bash
pip install eventlet==0.30.2     # pulls dnspython 1.16
```

### Fallback: os-ken
If Ryu cannot be installed at all, use **os-ken**, the maintained fork used by
OpenStack: `pip install os-ken`. It works the same way; imports change from
`ryu` to `os_ken`, and the command is `osken-manager`.

## Mininet problems

### `Exception: Error creating interface pair` or "file exists"
A previous run didn't clean up. Run:
```bash
sudo mn -c
```

### Hosts can't ping each other at all
- Is the controller running in the other terminal, and did it print
  `Switch 1 connected` and `Switch 2 connected`?
- Start the controller **before** Mininet.

### iperf shows full speed (≈10 Mbps or higher) instead of the slice limit
The queues weren't applied. Check for `QoS queues configured on s1-eth3` at
startup, and inspect with `sudo ovs-vsctl list qos`.

## Step 2 (REST API) problems

### `WARNING: no queues on s1-eth3 ... Slicing is NOT active`
Since Step 2 the controller creates the queues, and it needs root for that.
Start it with sudo: `sudo ~/ryu-env/bin/ryu-manager src/slice_controller.py`.
The controller log will show `Could not set queues: ... Permission denied`.

### `Error 500: could not update switch queues ... (is the controller running with sudo?)`
Same cause. The change was rolled back, so nothing is half-applied. Restart
the controller with sudo.

### `Cannot reach the controller at http://127.0.0.1:8080`
The controller isn't running, or it crashed (check its terminal). If its log
shows `Address already in use`, another program has port 8080: find it with
`sudo ss -ltnp | grep 8080`.

### A host moved to another slice but traffic still behaves the old way
Flow rules are flushed on every membership change, so this should not
happen. Check the controller log for `Flushed IP flow rules on 2 switch(es)`.
If it says `0 switch(es)`, the switches aren't connected to the controller.

## Platform

Mininet and Open vSwitch need the Linux kernel. On a Mac, use an Ubuntu VM
(UTM, Multipass or QEMU; ARM Ubuntu on Apple Silicon works).

## Step 3 (monitoring) problems

### The controller died when I ran `sudo mn -c`
`mn -c` kills every process with "controller" in its command line, and
that includes `slice_controller.py`. Always run `sudo mn -c` **before**
starting the controller, never while it runs.

### `slicectl top` shows `-` for throughput
No fresh counters. Is Mininet running, and did the controller log print
`Monitoring s1 shared-link port(s): s1-eth3 (port 3)`? If not, check that
`trunk_ports` in `config/slices.json` matches the real port names
(`sudo ovs-vsctl list-ports s1`).

### `slicectl top` shows `-` for latency, SLA `unknown`
The prober isn't reporting. Check that `slice_topo.py` printed
`Latency prober running`, and that each slice has at least two hosts that
exist in Mininet (h1–h4). A slice whose only hosts are 10.0.0.5/10.0.0.6
has nobody to ping.

### `plot_metrics.py`: `No module named 'matplotlib'`
`sudo apt install python3-matplotlib` (or `pip3 install matplotlib`).

