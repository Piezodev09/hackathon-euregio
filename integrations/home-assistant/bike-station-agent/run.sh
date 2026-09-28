#!/bin/sh
# Add-on start: read /data/options.json, pair on the first start, take MQTT credentials from the
# Supervisor, then run the agent (all in bikeagent/homeassistant.py).
set -eu
exec python3 -m bikeagent.homeassistant
