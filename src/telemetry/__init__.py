"""M6 live telemetry: MQTT wire protocol, configuration, ingest and simulator.

The contract every module here implements is design/M6_LIVE_TELEMETRY.md.
protocol.py and config.py are deliberately dependency-light (no paho, no DB)
so the simulator, the ingest service, and their tests can all share one
definition of "a valid snapshot" without dragging in the network stack.
"""
