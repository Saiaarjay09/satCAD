"""A stand-in for satSTK: streams made-up but plausible telemetry to the Digital Twin at 30 Hz, so you can see the twin working without running a simulation.
It is also the reference implementation of docs/TELEMETRY_PROTOCOL.md (what satSTK's telemetry_broadcaster.py sends).

    python tools/twin_demo_sender.py                       # one fast orbit (90 s) in a loop, to 127.0.0.1:9876
    python tools/twin_demo_sender.py --period 60 --seconds 120

The numbers are NOT physics: the Sun angle goes round once per period, the target relay changes every quarter, the turret sweeps toward it, the core temperature
swings from cool to the 85 degC throttle point and back so every colour of the thermal map shows, and the modulation steps down when the SNR falls.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time
from datetime import datetime, timedelta, timezone

RELAYS = ["SOUTH-INDIA-GS", "GEO-RELAY-IND", "GEO-RELAY-ATL", "GEO-RELAY-PAC"]
TURRET_FOR = {"SOUTH-INDIA-GS": ("aft_a", -20.0, 12.0), "GEO-RELAY-IND": ("fwd_a", 25.0, 30.0), "GEO-RELAY-ATL": ("fwd_b", -55.0, 18.0), "GEO-RELAY-PAC": ("aft_b", 40.0, 22.0)}


def packet(seq: int, t: float, period: float, epoch: datetime) -> dict:
    phase = (t % period) / period
    relay = RELAYS[int(phase * 4) % 4]
    tid, az0, el0 = TURRET_FOR[relay]
    wobble = math.sin(2 * math.pi * 0.4 * t)
    core = 58.0 + 30.0 * math.sin(2 * math.pi * (t / period) * 2 - 0.6)                       # about 28 to 88 degC
    snr = 15.0 + 6.0 * math.sin(2 * math.pi * phase * 3)
    mod, gbps = ("16QAM", 40.0) if snr > 19 else ("QPSK", 20.0) if snr > 12 else ("BPSK", 10.0)
    return {"v": 1, "seq": seq, "timestamp_utc": (epoch + timedelta(seconds=t * 60)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "sun_vector_body_deg": round((360.0 * phase) % 360.0, 3), "active_relay_id": relay, "active_turret_id": tid,
            "turret_angles_deg": {"azimuth": round(az0 + 8 * wobble, 3), "elevation": round(el0 + 4 * math.sin(2 * math.pi * 0.25 * t), 3),
                                  "fsm_x_urad": round(40 * wobble, 2), "fsm_y_urad": round(-25 * math.cos(2 * math.pi * 0.4 * t), 2)},
            "thermal_state": {"core_temp_c": round(core, 2), "radiator_temp_c": round(18 + 0.3 * (core - 58), 2), "coolant_flow_lpm": round(39.8 + 0.2 * (core - 58), 2),
                              "bypass_valve_pct": round(max(0.0, 100.0 - core), 1) if core < 100 else 0.0, "throttle_mode": "THROTTLE" if core >= 85 else "NOMINAL"},
            "dsp_metrics": {"modulation": mod, "snr_db": round(snr, 2), "ber": float(f"{10 ** (-4 - 0.4 * snr):.2e}"), "throughput_gbps": gbps}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send demo telemetry to the satCAD Digital Twin.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9876)
    ap.add_argument("--rate", type=float, default=30.0, help="packets per second (default 30)")
    ap.add_argument("--period", type=float, default=90.0, help="seconds per orbit (default 90)")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after this long (default: run until Ctrl+C)")
    args = ap.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("This demo only sends to the loopback address.", file=sys.stderr)
        return 2
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    epoch = datetime(2026, 12, 21, tzinfo=timezone.utc)
    t0 = time.monotonic()
    seq = 0
    print(f"Streaming demo telemetry to {args.host}:{args.port} at {args.rate:g} Hz. Ctrl+C to stop.")
    try:
        while args.seconds <= 0 or time.monotonic() - t0 < args.seconds:
            t = time.monotonic() - t0
            sock.sendto(json.dumps(packet(seq, t, args.period, epoch), separators=(",", ":")).encode(), (args.host, args.port))
            seq += 1
            time.sleep(max(0.0, t0 + seq / args.rate - time.monotonic()))
    except KeyboardInterrupt:
        pass
    print(f"Sent {seq} packets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
