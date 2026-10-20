# Digital Twin telemetry protocol (satSTK to satCAD), version 1

satSTK (the orbital mission and laser-link simulator) streams its live state to satCAD (the 3D CAD application), which articulates the satellite model, colours it by temperature
and shows a heads-up display. Both programs run on the same computer; nothing leaves it.

## Transport

| | |
|---|---|
| Protocol | **UDP**, one JSON object per datagram (UTF-8, no framing, no newline needed) |
| Telemetry | satSTK **sends** to `127.0.0.1:9876`; satCAD **listens** there (bound to the loopback address only, never to the network) |
| Rate | **30 Hz** of wall-clock time while a simulation is running or playing, whatever the playback speed (a paused simulation may send at 1 Hz or stop) |
| Size | under 1 KB typical; satCAD ignores any datagram over 8 KB |
| Return channel | satCAD **sends** a small status datagram to `127.0.0.1:9877` once per second while Live Digital Twin Sync is on, so satSTK can show "satCAD connected" (optional for satSTK) |
| Loss | UDP may drop or reorder packets. The stream is stateless (every packet carries the full state), so a lost packet costs nothing; satCAD always applies the newest packet and drops older ones |

Why UDP and not WebSocket: it needs no extra libraries on either side, a sender never blocks waiting for a viewer, and the viewer never has to be started first.

## Telemetry packet (satSTK to satCAD)

```json
{
  "v": 1,
  "seq": 18204,
  "timestamp_utc": "2026-12-21T03:14:15.926Z",
  "sun_vector_body_deg": 217.4,
  "active_relay_id": "GEO-RELAY-IND",
  "active_turret_id": "fwd_a",
  "turret_angles_deg": {"azimuth": 12.5, "elevation": 31.0, "fsm_x_urad": -42.0, "fsm_y_urad": 17.5},
  "thermal_state": {"core_temp_c": 58.2, "radiator_temp_c": 21.4, "coolant_flow_lpm": 39.8, "bypass_valve_pct": 12.0, "throttle_mode": "NOMINAL"},
  "dsp_metrics": {"modulation": "QPSK", "snr_db": 14.2, "ber": 3.1e-9, "throughput_gbps": 18.0}
}
```

| Field | Type | Meaning |
|---|---|---|
| `v` | int | protocol version, `1` |
| `seq` | int, optional | counts up by one per packet; satCAD drops a packet older than the newest it has seen (a drop of more than 100 is treated as a restart) |
| `timestamp_utc` | string | the simulation epoch (ISO 8601, UTC) of this tick |
| `sun_vector_body_deg` | number | the Solar Array Drive Assembly (SADA) angle needed to point the arrays at the Sun, **0 to 360 degrees**, right-handed about the body **+X** axis; both solar wings turn together by this angle |
| `active_relay_id` | string | the locked target: `SOUTH-INDIA-GS`, `GEO-RELAY-IND`, `GEO-RELAY-ATL`, `GEO-RELAY-PAC`, or `NONE` when no link is locked |
| `active_turret_id` | string, optional | which OISL turret is active: `fwd_a`, `fwd_b`, `aft_a` or `aft_b` (default `fwd_a`) |
| `turret_angles_deg` | object | pointing of the active turret (see conventions below): `azimuth` and `elevation` in degrees, `fsm_x_urad` and `fsm_y_urad` (fast steering mirror deflection) in microradians |
| `turrets` | object, optional | several active turrets at once: `{"fwd_a": {azimuth, elevation, fsm_x_urad, fsm_y_urad}, "aft_b": {...}}`; when present it replaces `active_turret_id` / `turret_angles_deg` |
| `thermal_state` | object | `core_temp_c` (compute core, degC), `radiator_temp_c`, `coolant_flow_lpm` (litres per minute), `bypass_valve_pct` (0 to 100), `throttle_mode` (`NOMINAL`, `THROTTLE` or `SHUTDOWN`) |
| `dsp_metrics` | object | `modulation` (for example `BPSK`, `QPSK`, `8PSK`, `16QAM`), `snr_db`, `ber` (bit error rate, 0 to 1), `throughput_gbps` |

Every section is optional: satCAD updates only what a packet contains. Unknown fields are ignored (so the protocol can grow). Numbers must be finite; a packet with a wrong type in a field
is ignored as a whole (counted, never crashes the viewer).

### Turret angle conventions (satCAD model frame)

The satellite model has four turrets: `fwd_a`, `fwd_b` (on the +Z end) and `aft_a`, `aft_b` (on the -Z end). Each has an **outward normal** (+Z for forward, -Z for aft).

- `azimuth`: right-handed rotation about the turret's outward normal. `0` is the home pose, the boresight pointing along body **+Y**.
- `elevation`: tilt of the boresight out of the hull plane toward the outward normal. `0` is the home pose (flat); positive tilts the telescope away from the hull.
- The laser ray is drawn from the lens along the boresight, rotated by azimuth then elevation, then deflected by `fsm_x_urad` / `fsm_y_urad`.

## Status packet (satCAD to satSTK), optional

Sent to `127.0.0.1:9877` once per second while the twin is listening:

```json
{"v": 1, "app": "satCAD", "twin": "online", "model": "Orbital-Node-Alpha", "frames": 1830, "bad_packets": 0, "last_seq": 18204}
```

satSTK may show "Digital twin: satCAD connected" while these arrive and "not connected" when they stop for 3 seconds. It must never wait for them.

## Safety and limits

- satCAD binds the loopback address only and refuses any other address; a packet is parsed with `json` only (never evaluated), size-limited, and every value is range-checked and clamped.
- All values are for visualisation. This link carries no commands: satCAD cannot control satSTK, and satSTK cannot change the CAD model.
