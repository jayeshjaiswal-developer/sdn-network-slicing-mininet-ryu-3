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

## Platform

Mininet and Open vSwitch need the Linux kernel. On a Mac, use an Ubuntu VM
(UTM, Multipass or QEMU; ARM Ubuntu on Apple Silicon works).
