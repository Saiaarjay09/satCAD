"""
Digital Twin, the pure part: what a telemetry packet from satSTK means for the 3D model. No Qt, no network: just parsing, colour and geometry,
so it is fast and easy to test. The wire format is in docs/TELEMETRY_PROTOCOL.md.

    packet = parse_packet(datagram_bytes)                # validated and clamped, or PacketError
    twin = TwinModel(design)                             # finds the solar arrays, turrets and thermal parts of a model by name
    pose = twin.pose(state)                              # 4x4 matrices for the moving assemblies, laser rays, colours

WHAT MOVES (all by the same kinematic solver Motion Studio uses, so nothing is re-meshed):
  - the +X and -X solar wings turn about the body X axis by `sun_vector_body_deg`
  - each active OISL turret turns its azimuth stage about its outward normal, then its elevation stage about the horizontal axis, and a laser
    ray is drawn from the lens along the boresight, deflected by the fast steering mirror angles
  - the cold plates, hot pipes and radiator panels are coloured from the core temperature: blue at 30 degC, gold-white at 60, crimson at 85
Models without those parts (a plain block) still work: whatever is not found is simply not driven, and `TwinModel.summary()` says what was found.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field

import numpy as np

from .gui.kinematics_solver import Joint, KinematicsSolver

PROTOCOL_VERSION = 1
HOST = "127.0.0.1"
PORT = 9876
STATUS_PORT = 9877
MAX_DATAGRAM = 8192
TURRET_IDS = ("fwd_a", "fwd_b", "aft_a", "aft_b")
RELAY_COLORS = {"SOUTH-INDIA-GS": (0.35, 0.95, 0.45), "GEO-RELAY-IND": (0.1, 0.85, 1.0), "GEO-RELAY-ATL": (1.0, 0.35, 0.85), "GEO-RELAY-PAC": (1.0, 0.62, 0.15)}
DEFAULT_RAY_COLOR = (0.2, 1.0, 0.6)
THERMAL_STOPS = ((30.0, (0.16, 0.45, 0.95)), (60.0, (1.0, 0.90, 0.60)), (85.0, (0.86, 0.08, 0.24)))      # cool blue, nominal gold / white, throttle crimson
STALE_AFTER_S = 1.0


class PacketError(ValueError):
    """A datagram that is not a valid telemetry packet (the message says why). The viewer counts these and carries on."""


# ==============================================================================================================================
#  Packets
# ==============================================================================================================================
@dataclass
class Packet:
    seq: int | None = None
    timestamp: str | None = None
    sun_deg: float | None = None
    relay: str | None = None
    turrets: dict | None = None                    # {turret id: {"azimuth", "elevation", "fsm_x_urad", "fsm_y_urad"}}
    thermal: dict | None = None
    dsp: dict | None = None
    received_at: float = field(default_factory=time.monotonic)


def _number(v, name: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PacketError(f"'{name}' must be a finite number.")
    return float(min(max(v, lo), hi))


def _text(v, name: str, limit: int) -> str:
    if not isinstance(v, str):
        raise PacketError(f"'{name}' must be text.")
    return "".join(ch for ch in v if ch.isprintable())[:limit]


def _section(obj, name: str) -> dict | None:
    if obj is None:
        return None
    if not isinstance(obj, dict):
        raise PacketError(f"'{name}' must be an object.")
    return obj


def _turret_angles(obj, name: str) -> dict:
    t = _section(obj, name) or {}
    out = {}
    for key, lo, hi in (("azimuth", -720.0, 720.0), ("elevation", -90.0, 90.0), ("fsm_x_urad", -5000.0, 5000.0), ("fsm_y_urad", -5000.0, 5000.0)):
        if key in t:
            out[key] = _number(t[key], f"{name}.{key}", lo, hi)
    if "azimuth" in out:
        out["azimuth"] = (out["azimuth"] + 180.0) % 360.0 - 180.0                               # wrapped to -180..180
    return out


def parse_packet(raw: bytes | str) -> Packet:
    """Turn a datagram into a Packet. Every value is type-checked and clamped; anything wrong rejects the whole packet with PacketError."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    if len(raw) > MAX_DATAGRAM:
        raise PacketError(f"Datagram is {len(raw)} bytes; the limit is {MAX_DATAGRAM}.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PacketError("Not valid JSON.") from None
    if not isinstance(data, dict):
        raise PacketError("A packet must be a JSON object.")
    if data.get("v", PROTOCOL_VERSION) != PROTOCOL_VERSION:
        raise PacketError(f"Unsupported protocol version {data.get('v')!r}; this viewer speaks version {PROTOCOL_VERSION}.")
    p = Packet()
    if "seq" in data:
        if isinstance(data["seq"], bool) or not isinstance(data["seq"], int):
            raise PacketError("'seq' must be an integer.")
        p.seq = data["seq"]
    if "timestamp_utc" in data:
        p.timestamp = _text(data["timestamp_utc"], "timestamp_utc", 40)
    if "sun_vector_body_deg" in data:
        p.sun_deg = _number(data["sun_vector_body_deg"], "sun_vector_body_deg", -1e6, 1e6) % 360.0
    if "active_relay_id" in data:
        p.relay = _text(data["active_relay_id"], "active_relay_id", 32)
    turrets = {}
    if "turrets" in data:
        for tid, angles in (_section(data["turrets"], "turrets") or {}).items():
            if tid in TURRET_IDS:
                turrets[tid] = _turret_angles(angles, f"turrets.{tid}")
    elif "turret_angles_deg" in data:
        tid = _text(data.get("active_turret_id", "fwd_a"), "active_turret_id", 16)
        if tid not in TURRET_IDS:
            raise PacketError(f"Unknown turret '{tid}'. Use one of {', '.join(TURRET_IDS)}.")
        turrets[tid] = _turret_angles(data["turret_angles_deg"], "turret_angles_deg")
    if turrets or "turrets" in data or "turret_angles_deg" in data:
        p.turrets = turrets
    th = _section(data.get("thermal_state"), "thermal_state")
    if th is not None:
        p.thermal = {}
        for key, lo, hi in (("core_temp_c", -100.0, 500.0), ("radiator_temp_c", -273.0, 500.0), ("coolant_flow_lpm", 0.0, 10000.0), ("bypass_valve_pct", 0.0, 100.0)):
            if key in th:
                p.thermal[key] = _number(th[key], f"thermal_state.{key}", lo, hi)
        if "throttle_mode" in th:
            p.thermal["throttle_mode"] = _text(th["throttle_mode"], "thermal_state.throttle_mode", 16).upper()
    dsp = _section(data.get("dsp_metrics"), "dsp_metrics")
    if dsp is not None:
        p.dsp = {}
        for key, lo, hi in (("snr_db", -100.0, 200.0), ("ber", 0.0, 1.0), ("throughput_gbps", 0.0, 100000.0)):
            if key in dsp:
                p.dsp[key] = _number(dsp[key], f"dsp_metrics.{key}", lo, hi)
        if "modulation" in dsp:
            p.dsp["modulation"] = _text(dsp["modulation"], "dsp_metrics.modulation", 12).upper()
    return p


# ==============================================================================================================================
#  The state the twin shows: the newest value of everything it has been told
# ==============================================================================================================================
@dataclass
class TwinState:
    seq: int | None = None
    timestamp: str | None = None
    sun_deg: float = 0.0
    relay: str = "NONE"
    turrets: dict = field(default_factory=dict)       # active turrets only
    thermal: dict = field(default_factory=dict)
    dsp: dict = field(default_factory=dict)
    last_packet_at: float | None = None
    frames: int = 0
    bad_packets: int = 0
    dropped_old: int = 0

    def apply(self, p: Packet) -> bool:
        """Merge a packet into the state. Returns False if it is older than the newest one already applied (UDP can reorder)."""
        if p.seq is not None and self.seq is not None and 0 < self.seq - p.seq <= 100:
            self.dropped_old += 1
            return False
        if p.seq is not None:
            self.seq = p.seq
        if p.timestamp is not None:
            self.timestamp = p.timestamp
        if p.sun_deg is not None:
            self.sun_deg = p.sun_deg
        if p.relay is not None:
            self.relay = p.relay or "NONE"
        if p.turrets is not None:
            self.turrets = {k: dict(v) for k, v in p.turrets.items()}
        if p.thermal is not None:
            self.thermal.update(p.thermal)
        if p.dsp is not None:
            self.dsp.update(p.dsp)
        self.last_packet_at = p.received_at
        self.frames += 1
        return True

    def status(self, now: float | None = None) -> str:
        if self.last_packet_at is None:
            return "WAITING"
        return "LIVE" if (now if now is not None else time.monotonic()) - self.last_packet_at < STALE_AFTER_S else "STALE"


# ==============================================================================================================================
#  Colour
# ==============================================================================================================================
def thermal_color(temp_c: float) -> tuple:
    """Cool blue at 30 degC and below, nominal gold / white at 60, throttle crimson at 85 and above; smooth in between."""
    t = float(temp_c)
    stops = THERMAL_STOPS
    if t <= stops[0][0]:
        return stops[0][1]
    if t >= stops[-1][0]:
        return stops[-1][1]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0)
            f = f * f * (3 - 2 * f)                                                            # smoothstep: no visible kink at 60 degC
            return tuple(a + (b - a) * f for a, b in zip(c0, c1))
    return stops[-1][1]


# ==============================================================================================================================
#  Finding the moving parts of a model
# ==============================================================================================================================
_TURRET_RE = re.compile(r"^turret_(fwd|aft)_(a|b)_az$")
PLATE_RE = re.compile(r"^(coldplate|cold_plate)")
HOT_RE = re.compile(r"^(hot_manifold|hot_feeder|hot_line|tube_hot|root_header_hot|tip_header|root_manifold)")
RADIATOR_RE = re.compile(r"^(radiator_panel|micro_tube|fin_(top|bottom)_)")


@dataclass
class TurretBinding:
    id: str
    sign: int                                       # +1 forward (outward normal +Z), -1 aft
    az_assembly: str
    el_assembly: str
    base_part: str
    barrel_part: str
    lens_part: str


@dataclass
class Bindings:
    solar: dict = field(default_factory=dict)       # "pos_x" / "neg_x" -> (assembly, SADA part)
    turrets: dict = field(default_factory=dict)     # id -> TurretBinding
    plates: list = field(default_factory=list)
    hot_pipes: list = field(default_factory=list)
    radiators: list = field(default_factory=list)


def discover(design) -> Bindings:
    """Find the parts the twin drives, by the naming of the Orbital-Node-Alpha model (solar_pos_x_wing, turret_fwd_a_az, coldplate, hot_manifold, radiator_panel ...)."""
    asm = {n for n, f in design.features.items() if f.kind == "assembly"}
    parts = set(design.parts)
    b = Bindings()
    for tag in ("pos_x", "neg_x"):
        wing, sada = f"solar_{tag}_wing", f"sada_{tag}"
        if wing in asm and sada in parts:
            b.solar[tag] = (wing, sada)
    for name in sorted(asm):
        m = _TURRET_RE.match(name)
        if not m:
            continue
        tid = f"{m.group(1)}_{m.group(2)}"
        top = f"turret_{tid}"
        need = (f"{top}_el", f"{top}_base", f"{top}_barrel", f"{top}_lens")
        if need[0] in asm and all(n in parts for n in need[1:]):
            b.turrets[tid] = TurretBinding(tid, 1 if m.group(1) == "fwd" else -1, name, need[0], need[1], need[2], need[3])
    b.plates = sorted(n for n in parts if PLATE_RE.match(n))
    b.hot_pipes = sorted(n for n in parts if HOT_RE.match(n))
    b.radiators = sorted(n for n in parts if RADIATOR_RE.match(n))
    return b


def _centre(design, part: str) -> tuple:
    lo, hi = design.parts[part].bbox()
    return tuple((a + b) / 2 for a, b in zip(lo, hi))


class TwinModel:
    """The twin's view of one design: joints for the solar wings and turrets, and the groups of parts to colour. Build it again after the model changes."""

    def __init__(self, design, ray_length_mm: float | None = None):
        self.design = design
        self.bindings = discover(design)
        self.joints: list[Joint] = []
        self.lens_home: dict[str, np.ndarray] = {}
        for tag, (wing, sada) in self.bindings.solar.items():
            c = _centre(design, sada)
            self.joints.append(Joint(f"twin_sada_{tag}", "revolute", wing, axis=(1, 0, 0), pivot=(0.0, c[1], c[2])))
        for tid, t in self.bindings.turrets.items():
            base, barrel = _centre(design, t.base_part), _centre(design, t.barrel_part)
            self.joints.append(Joint(f"twin_az_{tid}", "revolute", t.az_assembly, axis=(0, 0, t.sign), pivot=base))
            self.joints.append(Joint(f"twin_el_{tid}", "revolute", t.el_assembly, axis=(t.sign, 0, 0), pivot=barrel))
            self.lens_home[tid] = np.append(np.asarray(_centre(design, t.lens_part)), 1.0)
        self.solver = KinematicsSolver(self.joints, design)
        if ray_length_mm is None:
            sizes = [hi - lo for lo, hi in zip(*_extent(design))]
            ray_length_mm = max(8000.0, 0.6 * max(sizes, default=0.0))
        self.ray_length_mm = float(ray_length_mm)

    # ---- what was found --------------------------------------------------------------------------------------------------------------
    @property
    def groups(self) -> dict[str, list[str]]:
        """{joint name: parts it moves}: what the viewport binds as moving groups."""
        return {j.name: self.solver.parts_of(j.name) for j in self.solver.joints if self.solver.parts_of(j.name)}

    @property
    def articulated(self) -> bool:
        return bool(self.joints)

    def summary(self) -> str:
        b = self.bindings
        bits = [f"{len(b.solar)} solar wing(s)", f"{len(b.turrets)} turret(s)", f"{len(b.plates)} cold plate part(s)", f"{len(b.hot_pipes)} hot pipe part(s)", f"{len(b.radiators)} radiator part(s)"]
        return "Digital Twin bindings: " + ", ".join(bits)

    # ---- poses -----------------------------------------------------------------------------------------------------------------------
    def joint_values(self, state: TwinState) -> dict[str, tuple]:
        vals = {}
        for tag in self.bindings.solar:
            vals[f"twin_sada_{tag}"] = (state.sun_deg,)
        for tid in self.bindings.turrets:
            a = state.turrets.get(tid)
            vals[f"twin_az_{tid}"] = (a.get("azimuth", 0.0) if a else 0.0,)
            vals[f"twin_el_{tid}"] = (a.get("elevation", 0.0) if a else 0.0,)
        return vals

    def matrices(self, state: TwinState) -> dict[str, np.ndarray]:
        return self.solver.joint_matrices(self.joint_values(state))

    def rays(self, state: TwinState, matrices: dict | None = None) -> list[dict]:
        """One laser ray per active turret: from its lens along its boresight. {id, p0, p1, color} in model millimetres."""
        world = matrices if matrices is not None else self.matrices(state)
        out = []
        color = RELAY_COLORS.get(state.relay, DEFAULT_RAY_COLOR)
        if state.relay == "NONE":
            return out
        for tid, angles in state.turrets.items():
            t = self.bindings.turrets.get(tid)
            if t is None:
                continue
            m = world[f"twin_el_{tid}"]
            start = (m @ self.lens_home[tid])[:3]
            d = m[:3, :3] @ np.array([0.0, 1.0, 0.0])
            fx, fy = angles.get("fsm_x_urad", 0.0), angles.get("fsm_y_urad", 0.0)
            if fx or fy:                                                                        # the mirror steers the beam: x in the elevation direction, y in azimuth
                d = _deflect(d, t.sign, fx * 1e-6, fy * 1e-6)
            out.append(dict(id=tid, p0=tuple(start), p1=tuple(start + d * self.ray_length_mm), direction=tuple(d), color=color))
        return out

    def colors(self, state: TwinState, source: str = "core_temp_c") -> dict[str, tuple]:
        """Display colour for the cold plates, hot pipes and radiator panels from the temperature (empty until a temperature has arrived)."""
        t = state.thermal.get(source)
        if t is None:
            return {}
        c = thermal_color(t)
        b = self.bindings
        return {name: c for name in (*b.plates, *b.hot_pipes, *b.radiators)}


def _extent(design):
    los, his = [], []
    for p in design.parts.values():
        lo, hi = p.bbox()
        los.append(lo)
        his.append(hi)
    if not los:
        return [0, 0, 0], [0, 0, 0]
    return np.min(los, axis=0), np.max(his, axis=0)


def _deflect(d: np.ndarray, sign: int, d_el: float, d_az: float) -> np.ndarray:
    """Tilt a boresight by small angles: d_el toward the turret's outward normal, d_az about it."""
    n = np.array([0.0, 0.0, float(sign)])
    az_dir = np.cross(n, d)                                                                     # the direction a +azimuth rotation moves the boresight
    norm = np.linalg.norm(az_dir)
    az_dir = az_dir / norm if norm > 1e-9 else np.zeros(3)
    el_dir = n - d * float(np.dot(n, d))
    en = np.linalg.norm(el_dir)
    el_dir = el_dir / en if en > 1e-9 else np.zeros(3)
    out = d + az_dir * d_az + el_dir * d_el
    return out / np.linalg.norm(out)


# ==============================================================================================================================
#  The heads-up display
# ==============================================================================================================================
def hud_rows(state: TwinState, now: float | None = None) -> list[tuple]:
    """(label, value) rows for the HUD: the four headline numbers first, then the smaller ones."""
    status = state.status(now)
    th, dsp = state.thermal, state.dsp

    def fmt(d, key, spec, unit=""):
        return f"{d[key]:{spec}}{unit}" if key in d else "-"
    mode = th.get("throttle_mode")
    core = fmt(th, "core_temp_c", ".1f", " °C") + (f"  {mode}" if mode and "core_temp_c" in th else "")
    rows = [("title", f"SATSTK DIGITAL TWIN  ● {status}"),
            ("Active relay target", state.relay if status != "WAITING" else "-"),
            ("Core temp", core),
            ("Modulation", dsp.get("modulation", "-")),
            ("Link throughput", fmt(dsp, "throughput_gbps", ".2f", " Gbps")),
            ("small", f"SNR {fmt(dsp, 'snr_db', '.1f', ' dB')}   BER {fmt(dsp, 'ber', '.1e')}"),
            ("small", f"Sun {state.sun_deg:.0f}°   Radiator {fmt(th, 'radiator_temp_c', '.1f', ' °C')}   Flow {fmt(th, 'coolant_flow_lpm', '.1f', ' L/min')}")]
    for tid, a in state.turrets.items():
        rows.append(("small", f"Turret {tid}  az {a.get('azimuth', 0):.1f}°  el {a.get('elevation', 0):.1f}°  FSM {a.get('fsm_x_urad', 0):.0f}/{a.get('fsm_y_urad', 0):.0f} µrad"))
    if state.timestamp:
        rows.append(("small", state.timestamp))
    return rows
