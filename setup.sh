#!/usr/bin/env bash
# Step 1 setup for Ubuntu 22.04: Mininet + Open vSwitch + Ryu
# Ryu is no longer actively maintained and breaks on newer Python,
# so it runs in its own Python 3.9 virtual environment.
set -e

echo "== Installing Mininet, Open vSwitch, iperf =="
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf iperf3 git \
                    software-properties-common

echo "== Installing Python 3.9 for Ryu =="
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.9 python3.9-venv python3.9-dev

echo "== Creating Ryu virtual environment at ~/ryu-env =="
python3.9 -m venv ~/ryu-env
source ~/ryu-env/bin/activate
pip install --upgrade pip
pip install setuptools==67.6.1 wheel   # newer setuptools breaks Ryu's installer
pip install "pbr<7"                    # Ryu's installer needs pbr
# Build with the pinned setuptools above, not a fresh one in an isolated env
pip install --no-build-isolation ryu
pip install eventlet==0.30.2           # newer eventlet breaks Ryu at runtime
deactivate

echo "== Checking installs =="
~/ryu-env/bin/ryu-manager --version
sudo mn --test pingall
sudo mn -c

echo "Done. Activate Ryu with: source ~/ryu-env/bin/activate"
