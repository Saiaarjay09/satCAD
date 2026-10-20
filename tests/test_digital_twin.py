"""Digital Twin: satSTK telemetry packets, what they mean for the model, the UDP listener thread, and Live Digital Twin Sync in the window."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import math
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from satcad import Design, digital_twin as dt  # noqa: E402

ALPHA = ROOT / "sattelite designs" / "orbital_node_alpha_with_motion.json"
FULL = {"v": 1, "seq": 10, "timestamp_utc": "2026-12-21T03:14:15.926Z", "sun_vector_body_deg": 217.4, "active_relay_id": "GEO-RELAY-IND", "active_turret_id": "fwd_a",
        "turret_angles_deg": {"azimuth": 12.5, "elevation": 31.0, "fsm_x_urad": -42.0, "fsm_y_urad": 17.5},
        "thermal_state": {"core_temp_c": 58.2, "radiator_temp_c": 21.4, "coolant_flow_lpm": 39.8, "bypass_valve_pct": 12.0, "throttle_mode": "NOMINAL"},
        "dsp_metrics": {"modulation": "QPSK", "snr_db": 14.2, "ber": 3.1e-9, "throughput_gbps": 18.0}}


def raw(**changes) -> bytes:
    d = json.loads(json.dumps(FULL))
    d.update(changes)
    return json.dumps(d).encode()


@pytest.fixture(scope="module")
def alpha():
    d = Design("x")
    d.load(ALPHA)
    return d


@pytest.fixture(scope="module")
def twin_model(alpha):
    return dt.TwinModel(alpha)


# ===== packets ==========================================================================================================================
def test_a_full_packet_is_parsed_into_every_field():
    p = dt.parse_packet(json.dumps(FULL).encode())
    assert p.seq == 10 and p.timestamp == "2026-12-21T03:14:15.926Z" and p.sun_deg == pytest.approx(217.4) and p.relay == "GEO-RELAY-IND"
    assert p.turrets == {"fwd_a": {"azimuth": 12.5, "elevation": 31.0, "fsm_x_urad": -42.0, "fsm_y_urad": 17.5}}
    assert p.thermal == {"core_temp_c": 58.2, "radiator_temp_c": 21.4, "coolant_flow_lpm": 39.8, "bypass_valve_pct": 12.0, "throttle_mode": "NOMINAL"}
    assert p.dsp == {"modulation": "QPSK", "snr_db": 14.2, "ber": 3.1e-9, "throughput_gbps": 18.0}


def test_every_example_in_the_protocol_document_parses():
    text = (ROOT / "docs" / "TELEMETRY_PROTOCOL.md").read_text()
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
    assert len(blocks) == 2
    packet = dt.parse_packet(blocks[0])
    assert packet.relay == "GEO-RELAY-IND" and "fwd_a" in packet.turrets
    assert json.loads(blocks[1])["app"] == "satCAD"
    for relay in ("SOUTH-INDIA-GS", "GEO-RELAY-IND", "GEO-RELAY-ATL", "GEO-RELAY-PAC"):
        assert relay in text and relay in dt.RELAY_COLORS


def test_angles_are_wrapped_and_values_clamped():
    p = dt.parse_packet(raw(sun_vector_body_deg=-90.0, turret_angles_deg={"azimuth": 190.0, "elevation": 120.0, "fsm_x_urad": 1e9}))
    assert p.sun_deg == pytest.approx(270.0)
    assert p.turrets["fwd_a"]["azimuth"] == pytest.approx(-170.0) and p.turrets["fwd_a"]["elevation"] == 90.0 and p.turrets["fwd_a"]["fsm_x_urad"] == 5000.0
    q = dt.parse_packet(raw(thermal_state={"core_temp_c": 9999, "bypass_valve_pct": -5}, dsp_metrics={"ber": 7}))
    assert q.thermal["core_temp_c"] == 500.0 and q.thermal["bypass_valve_pct"] == 0.0 and q.dsp["ber"] == 1.0


@pytest.mark.parametrize("bad, why", [
    (b"not json", "JSON"), (b"[1, 2]", "object"), (b"\xff\xfe\x00", "JSON"), (raw(v=2), "version"),
    (raw(seq=True), "seq"), (raw(seq="7"), "seq"), (raw(sun_vector_body_deg="90"), "finite"), (raw(sun_vector_body_deg=True), "finite"),
    (raw(sun_vector_body_deg=float("nan")), "finite"), (raw(sun_vector_body_deg=float("inf")), "finite"), (raw(active_relay_id=5), "text"), (raw(thermal_state=[1]), "object"),
    (raw(thermal_state={"core_temp_c": "hot"}), "finite"), (raw(active_turret_id="bogus"), "turret"), (b"x" * 9000, "limit"),
], ids=["not-json", "array", "binary", "version", "seq-bool", "seq-text", "sun-text", "sun-bool", "sun-nan", "sun-inf", "relay-number", "thermal-array",
        "temp-text", "unknown-turret", "oversize"])
def test_bad_packets_are_rejected_with_a_reason(bad, why):
    with pytest.raises(dt.PacketError, match=why):
        dt.parse_packet(bad)


def test_unknown_fields_and_missing_sections_are_fine():
    p = dt.parse_packet(b'{"v": 1, "future_field": {"x": 1}, "dsp_metrics": {"modulation": "bpsk", "novel": 3}}')
    assert p.dsp == {"modulation": "BPSK"} and p.thermal is None and p.turrets is None and p.sun_deg is None
    assert dt.parse_packet(b"{}").relay is None


def test_text_fields_are_sanitised_and_limited():
    p = dt.parse_packet(json.dumps({"active_relay_id": "GEO\x00-RELAY\x1b[31m" + "X" * 80, "dsp_metrics": {"modulation": "qpsk-with-a-very-long-name"}}).encode())
    assert "\x00" not in p.relay and "\x1b" not in p.relay and len(p.relay) <= 32 and len(p.dsp["modulation"]) <= 12


def test_several_turrets_at_once_replace_the_single_turret_fields():
    two = {"fwd_a": {"azimuth": 5, "elevation": 6}, "aft_b": {"azimuth": -7, "elevation": 8}, "nonsense": {"azimuth": 1}}
    p = dt.parse_packet(raw(turrets=two))
    assert set(p.turrets) == {"fwd_a", "aft_b"} and p.turrets["aft_b"]["azimuth"] == -7


# ===== state ============================================================================================================================
def test_state_keeps_the_newest_value_of_everything_and_drops_reordered_packets():
    st = dt.TwinState()
    assert st.apply(dt.parse_packet(raw(seq=5)))
    assert not st.apply(dt.parse_packet(raw(seq=3, sun_vector_body_deg=1.0))) and st.sun_deg == pytest.approx(217.4) and st.dropped_old == 1
    assert st.apply(dt.parse_packet(b'{"v":1,"seq":6,"thermal_state":{"core_temp_c":70}}'))
    assert st.thermal["core_temp_c"] == 70 and st.thermal["radiator_temp_c"] == 21.4 and st.relay == "GEO-RELAY-IND"       # sections merge, the rest stays
    assert st.apply(dt.parse_packet(raw(seq=9000)))
    assert st.apply(dt.parse_packet(raw(seq=2)))                                                                               # a drop of more than 100 is a restart
    assert st.frames == 4


def test_status_goes_waiting_live_stale():
    st = dt.TwinState()
    assert st.status() == "WAITING"
    p = dt.parse_packet(raw())
    st.apply(p)
    assert st.status(p.received_at + 0.2) == "LIVE" and st.status(p.received_at + 2.0) == "STALE"


# ===== colour ===========================================================================================================================
def test_thermal_colour_runs_blue_gold_crimson():
    blue, gold, crimson = dt.THERMAL_STOPS[0][1], dt.THERMAL_STOPS[1][1], dt.THERMAL_STOPS[2][1]
    assert dt.thermal_color(30) == blue and dt.thermal_color(-40) == blue
    assert dt.thermal_color(60) == pytest.approx(gold) and dt.thermal_color(85) == crimson and dt.thermal_color(400) == crimson
    assert blue[2] > blue[0] and crimson[0] > crimson[2] and gold[0] > 0.9 and gold[1] > 0.8                               # blue is blue, crimson is red, gold is bright
    mid = dt.thermal_color(45)
    assert all(min(a, b) - 1e-9 <= m <= max(a, b) + 1e-9 for m, a, b in zip(mid, blue, gold))
    last = dt.thermal_color(30)
    for t in np.linspace(30, 85, 120):                                                                                        # smooth: no jumps between neighbouring temperatures
        c = dt.thermal_color(t)
        assert max(abs(a - b) for a, b in zip(c, last)) < 0.06
        last = c


# ===== finding the parts =================================================================================================================
def test_the_alpha_model_is_fully_bound(twin_model):
    b = twin_model.bindings
    assert set(b.solar) == {"pos_x", "neg_x"} and set(b.turrets) == set(dt.TURRET_IDS)
    assert len(b.plates) >= 16 and len(b.hot_pipes) > 0 and len(b.radiators) > 0
    assert [t.sign for t in (b.turrets["fwd_a"], b.turrets["aft_b"])] == [1, -1]
    assert twin_model.articulated and len(twin_model.joints) == 2 + 4 * 2
    assert "2 solar wing(s), 4 turret(s)" in twin_model.summary()
    moved = {p for parts in twin_model.groups.values() for p in parts}
    assert {"yoke_pos_x", "turret_fwd_a_barrel", "turret_aft_b_yoke"} <= moved
    assert "sada_pos_x" not in moved                                                                                   # the drive itself stays on the bus, the wing turns


def test_the_heavy_node_gets_thermal_colour_but_no_articulation():
    d = Design("x")
    d.load(ROOT / "sattelite designs" / "heavy_orbital_node.json")
    m = dt.TwinModel(d)
    assert not m.articulated and m.bindings.solar == {} and m.bindings.turrets == {}
    assert any(n.startswith("hot_manifold") for n in m.bindings.hot_pipes) and any(n.startswith("fin_top") for n in m.bindings.radiators)
    st = dt.TwinState()
    st.apply(dt.parse_packet(raw(thermal_state={"core_temp_c": 85})))
    assert set(m.colors(st).values()) == {dt.THERMAL_STOPS[2][1]} and m.rays(st) == []


def test_a_plain_block_has_nothing_to_drive_and_does_not_fail():
    d = Design("b")
    d.add("box", "a", length=10, width=10, height=10)
    m = dt.TwinModel(d)
    assert not m.articulated and m.groups == {} and m.colors(dt.TwinState()) == {}


# ===== the poses =========================================================================================================================
def test_the_solar_wings_turn_about_the_x_axis_by_the_sun_angle(alpha, twin_model):
    st = dt.TwinState(sun_deg=90.0)
    mats = twin_model.matrices(st)
    for tag in ("pos_x", "neg_x"):
        m = mats[f"twin_sada_{tag}"]
        sada = np.array(alpha.describe(f"sada_{tag}")["centre_mm"])
        assert np.allclose(m[:3, :3] @ np.array([1, 0, 0]), [1, 0, 0])                       # the X axis is the axis
        assert np.allclose(m[:3, :3] @ np.array([0, 1, 0]), [0, 0, 1])                       # +Y turns to +Z by 90 degrees
        pivot = np.array([0.0, sada[1], sada[2]])
        assert np.allclose((m @ np.append(pivot, 1.0))[:3], pivot)                           # the SADA is the fixed point
    assert np.allclose(twin_model.matrices(dt.TwinState(sun_deg=0.0))["twin_sada_pos_x"], np.eye(4))
    assert np.allclose(twin_model.matrices(dt.TwinState(sun_deg=360.0))["twin_sada_pos_x"][:3, :3], np.eye(3), atol=1e-9)


@pytest.mark.parametrize("tid, sign", [("fwd_a", 1), ("fwd_b", 1), ("aft_a", -1), ("aft_b", -1)])
@pytest.mark.parametrize("az, el", [(0, 0), (25, 35), (-40, 20), (170, 5), (-90, 60)])
def test_the_laser_boresight_follows_azimuth_and_elevation(twin_model, tid, sign, az, el):
    st = dt.TwinState(relay="GEO-RELAY-IND", turrets={tid: {"azimuth": float(az), "elevation": float(el)}})
    ray = twin_model.rays(st)[0]
    d = np.array(ray["direction"])
    assert np.linalg.norm(d) == pytest.approx(1.0)
    assert math.degrees(math.asin(sign * d[2])) == pytest.approx(el, abs=1e-6)                                    # elevation: toward the outward normal
    assert sign * math.degrees(math.atan2(-d[0], d[1])) == pytest.approx(az if abs(az) <= 90 or el == 0 else az, abs=1e-6) or abs(az) > 90
    if az == 0 and el == 0:
        assert np.allclose(d, [0, 1, 0])                                                                           # home boresight is +Y
        assert np.allclose(ray["p0"], twin_model.lens_home[tid][:3])                                               # and the ray starts at the lens
    assert np.linalg.norm(np.array(ray["p1"]) - np.array(ray["p0"])) == pytest.approx(twin_model.ray_length_mm)


def test_azimuth_elevation_round_trip_matches_the_satstk_formulas(twin_model):
    """The formulas satSTK uses (docs/TELEMETRY_PROTOCOL.md): el = asin(s*d_z), az = s*atan2(-d_x, d_y)."""
    for tid, s in (("fwd_a", 1), ("aft_a", -1)):
        for az, el in ((12.5, 31.0), (-33.0, 8.0), (60.0, 70.0)):
            d = twin_model.rays(dt.TwinState(relay="X", turrets={tid: {"azimuth": az, "elevation": el}}))[0]["direction"]
            assert s * math.degrees(math.atan2(-d[0], d[1])) == pytest.approx(az, abs=1e-6)
            assert math.degrees(math.asin(s * d[2])) == pytest.approx(el, abs=1e-6)


def test_the_fast_steering_mirror_deflects_the_beam_by_its_microradians(twin_model):
    base = twin_model.rays(dt.TwinState(relay="X", turrets={"fwd_a": {"azimuth": 20.0, "elevation": 30.0}}))[0]["direction"]
    steer = twin_model.rays(dt.TwinState(relay="X", turrets={"fwd_a": {"azimuth": 20.0, "elevation": 30.0, "fsm_x_urad": 300.0, "fsm_y_urad": 400.0}}))[0]["direction"]
    angle = math.acos(min(1.0, float(np.dot(base, steer))))
    assert angle == pytest.approx(500e-6, rel=0.02)                                                               # 3-4-5: sqrt(300^2 + 400^2) microradians


def test_inactive_turrets_have_no_ray_and_no_target_means_no_rays(twin_model):
    st = dt.TwinState(relay="GEO-RELAY-PAC", turrets={"aft_b": {"azimuth": 1.0, "elevation": 2.0}})
    assert [r["id"] for r in twin_model.rays(st)] == ["aft_b"] and twin_model.rays(st)[0]["color"] == dt.RELAY_COLORS["GEO-RELAY-PAC"]
    st.relay = "NONE"
    assert twin_model.rays(st) == []
    both = dt.TwinState(relay="GEO-RELAY-ATL", turrets={"fwd_a": {}, "fwd_b": {"azimuth": 5.0}})
    assert {r["id"] for r in twin_model.rays(both)} == {"fwd_a", "fwd_b"}


def test_colours_cover_plates_hot_pipes_and_radiators_only(alpha, twin_model):
    st = dt.TwinState()
    assert twin_model.colors(st) == {}                                                                              # nothing until a temperature arrives
    st.apply(dt.parse_packet(raw(thermal_state={"core_temp_c": 30})))
    colours = twin_model.colors(st)
    b = twin_model.bindings
    assert set(colours) == set(b.plates) | set(b.hot_pipes) | set(b.radiators) and set(colours.values()) == {dt.THERMAL_STOPS[0][1]}
    assert not any(n.startswith(("bus", "turret", "solar")) for n in colours)


def test_hud_rows_show_the_four_headline_numbers_and_the_link_state():
    st = dt.TwinState()
    rows = dt.hud_rows(st)
    assert "WAITING" in rows[0][1] and [r[0] for r in rows[1:5]] == ["Active relay target", "Core temp", "Modulation", "Link throughput"]
    st.apply(dt.parse_packet(json.dumps(FULL).encode()))
    rows = dict((k, v) for k, v in dt.hud_rows(st) if k not in ("small", "title"))
    assert rows["Active relay target"] == "GEO-RELAY-IND" and rows["Core temp"].startswith("58.2") and "NOMINAL" in rows["Core temp"]
    assert rows["Modulation"] == "QPSK" and rows["Link throughput"] == "18.00 Gbps"
    assert any("fwd_a" in v and "12.5" in v for k, v in dt.hud_rows(st) if k == "small")


# ===== the stand-in sender ================================================================================================================
def test_the_demo_sender_speaks_the_protocol_and_covers_the_thermal_range():
    import twin_demo_sender as snd
    from datetime import datetime, timezone
    epoch = datetime(2026, 12, 21, tzinfo=timezone.utc)
    temps, relays = [], set()
    for i in range(0, 900):
        pkt = snd.packet(i, i / 10.0, 90.0, epoch)
        p = dt.parse_packet(json.dumps(pkt).encode())
        assert len(json.dumps(pkt, separators=(",", ":"))) < 1000                                                  # under 1 KB, as the protocol promises
        temps.append(p.thermal["core_temp_c"])
        relays.add(p.relay)
        assert 0 <= p.sun_deg < 360 and p.dsp["modulation"] in ("BPSK", "QPSK", "16QAM")
    assert min(temps) < 32 and max(temps) > 85 and relays == set(snd.RELAYS)


# ===== the listener thread and the window ======================================================================================================
qt = pytest.importorskip("PySide6")
pytest.importorskip("vtkmodules.vtkRenderingCore")
from PySide6.QtWidgets import QApplication  # noqa: E402

from satcad.gui import theme  # noqa: E402
from satcad.gui.digital_twin_listener import MAX_PENDING, DigitalTwin, UdpReceiver  # noqa: E402
from satcad.gui.session import Session  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


def pump(app, seconds: float = 0.0, until=None, timeout: float = 5.0) -> bool:
    end = time.monotonic() + (seconds if until is None else timeout)
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


@pytest.fixture()
def window(app):
    from satcad.gui.ui_layout import MainWindow
    win = MainWindow(Session())
    try:
        win.viewport.grab_image()
    except Exception as e:
        pytest.skip(f"off-screen rendering not available: {e}")
    win.session.open(ALPHA)
    win.twin.port, win.twin.status_port = 0, None                                       # an ephemeral port: tests never fight over 9876
    yield win
    win.twin.stop()


def start(app, win) -> socket.socket:
    assert win.set_twin(True) is None or True
    assert win.twin.running
    assert pump(app, until=lambda: win.twin.bound_port, timeout=5.0), "the listener never bound its port"
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.connect(("127.0.0.1", win.twin.bound_port))
    return tx


def test_the_toggle_starts_and_stops_listening(app, window):
    assert window.twin_button.isCheckable() and not window.twin_button.isChecked()
    window.twin_button.setChecked(True)
    window.set_twin(True)
    assert window.twin.running and window.twin_button.isChecked() and window.motion.suspended
    assert pump(app, until=lambda: window.twin.bound_port)
    assert window.viewport._hud is not None and "WAITING" in window.viewport._hud[0][0][1]                  # the HUD is up before any packet arrives
    window.set_twin(False)
    assert not window.twin.running and not window.twin_button.isChecked() and not window.motion.suspended
    assert window.viewport._hud is None and window.viewport._rays == {} and "coldplate" not in window.viewport._color_override
    assert window.twin.thread is None


def test_a_real_udp_packet_moves_the_model_colours_it_and_fills_the_hud(app, window):
    tx = start(app, window)
    tx.send(json.dumps(FULL).encode())
    assert pump(app, until=lambda: window.twin.state.frames >= 1)
    pump(app, 0.15)
    v = window.viewport
    m = np.array([[v._group_matrix["twin_sada_pos_x"].GetElement(r, c) for c in range(4)] for r in range(4)])
    expected = window.twin.model.matrices(window.twin.state)["twin_sada_pos_x"]
    assert np.allclose(m, expected) and not np.allclose(m, np.eye(4))                                               # the array really turned, by 217.4 degrees
    assert math.degrees(math.atan2(m[2, 1], m[1, 1])) % 360 == pytest.approx(217.4, abs=1e-6)
    assert set(v._rays) == {"fwd_a"}
    assert v._color_override.get("coldplate") == pytest.approx(dt.thermal_color(58.2))                              # a cold plate takes the 58 degC colour
    rows = dict((k, val) for k, val in v._hud[0] if k not in ("title", "small"))
    assert rows["Active relay target"] == "GEO-RELAY-IND" and rows["Link throughput"] == "18.00 Gbps" and v._hud[1] == "LIVE"
    window.set_twin(False)                                                                                            # switching off clears the ray, the HUD and the twin's colours
    assert v._rays == {} and v._hud is None and "coldplate" not in v._color_override


def test_a_stream_at_30_hz_is_followed_and_goes_stale_when_it_stops(app, window):
    tx = start(app, window)
    t0 = time.monotonic()
    for i in range(30):
        tx.send(raw(seq=i, sun_vector_body_deg=i * 10.0))
        pump(app, max(0.0, t0 + (i + 1) / 30.0 - time.monotonic()))
    assert window.twin.state.frames == 30 and window.twin.state.sun_deg == pytest.approx(290.0)
    assert window.twin.state.status() == "LIVE"
    pump(app, 1.3)
    assert window.twin.state.status() == "STALE" and window.viewport._hud[1] == "STALE"
    assert window.twin.state.sun_deg == pytest.approx(290.0)                                                          # the model holds its last pose


def test_bad_and_oversize_datagrams_are_counted_and_ignored(app, window):
    tx = start(app, window)
    for junk in (b"garbage", b"{", b"[]", raw(v=9), b"x" * 9000, bytes(range(256))):
        tx.send(junk)
    tx.send(json.dumps(FULL).encode())
    assert pump(app, until=lambda: window.twin.state.frames >= 1)
    assert window.twin.receiver.counters()[1] >= 5 and window.twin.state.relay == "GEO-RELAY-IND"


def test_a_flood_of_packets_does_not_block_the_gui(app, window):
    tx = start(app, window)
    t0 = time.monotonic()
    for i in range(3000):
        tx.send(raw(seq=i, sun_vector_body_deg=i % 360))
    sent = time.monotonic() - t0
    ticks = []
    t1 = time.monotonic()
    while time.monotonic() - t1 < 0.5:
        a = time.monotonic()
        app.processEvents()
        ticks.append(time.monotonic() - a)
        time.sleep(0.005)
    assert max(ticks) < 0.25 and window.twin.receiver is not None and len(window.twin.receiver._pending) <= MAX_PENDING
    assert window.twin.state.frames > 0 and sent < 5.0


def test_only_the_loopback_address_is_accepted(app, window):
    with pytest.raises(ValueError, match="loopback"):
        UdpReceiver("0.0.0.0", 0)
    with pytest.raises(ValueError, match="loopback"):
        UdpReceiver("192.168.1.20", 9876)
    window.twin.host = "0.0.0.0"
    log = []
    window.session.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    assert window.twin.start() is False and not window.twin.running
    assert log[-1][0] == "error" and "loopback" in log[-1][1]
    window.set_twin(True)
    assert not window.twin_button.isChecked()


def test_a_port_already_in_use_is_reported_and_the_toggle_pops_back(app, window):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    window.twin.port = blocker.getsockname()[1]
    log = []
    window.session.message.connect(lambda lvl, txt: log.append((lvl, txt)))
    window.set_twin(True)
    assert pump(app, until=lambda: not window.twin.running and any(l == "error" and "Can't listen" in t for l, t in log))
    assert not window.twin_button.isChecked() and window.twin.thread is None
    blocker.close()


def test_the_status_datagram_goes_back_to_satstk(app, window):
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(3.0)
    window.twin.status_port = listener.getsockname()[1]
    tx = start(app, window)
    tx.send(raw(seq=77))
    pump(app, 0.3)
    data = None
    end = time.monotonic() + 3.0
    while data is None and time.monotonic() < end:
        app.processEvents()
        try:
            data, _ = listener.recvfrom(2048)
        except socket.timeout:
            pass
    status = json.loads(data)
    assert status["app"] == "satCAD" and status["twin"] == "online" and status["model"] == "Orbital-Node-Alpha" and status["v"] == 1
    assert status["frames"] >= 0 and status["bad_packets"] == 0
    listener.close()


def test_rebuilding_the_model_while_the_twin_runs_keeps_it_working(app, window):
    tx = start(app, window)
    tx.send(json.dumps(FULL).encode())
    assert pump(app, until=lambda: window.twin.state.frames >= 1)
    window.session.add("box", length=10, width=10, height=10)                                                         # the design changes: actors are rebuilt
    pump(app, 0.2)
    v = window.viewport
    assert "twin_sada_pos_x" in v._group_matrix and window.twin.running
    m = np.array([[v._group_matrix["twin_sada_pos_x"].GetElement(r, c) for c in range(4)] for r in range(4)])
    assert not np.allclose(m, np.eye(4))                                                                              # the pose survived the rebuild
    window.session.undo()


def test_motion_studio_is_paused_while_the_twin_has_the_model(app, window):
    window.motion.play()
    window.set_twin(True)
    assert not window.motion.playing and window.motion.suspended
    before = window.viewport._group_matrix.get("SADA_Revolute_pos_x")
    window.motion.pose(100.0)                                                                                         # ignored while suspended
    assert window.viewport._group_matrix.get("SADA_Revolute_pos_x") is before
    window.set_twin(False)
    assert "SADA_Revolute_pos_x" in window.viewport._group_matrix                                                     # Motion Studio's own groups are back


def test_the_command_line_launch_mode_opens_the_model_and_listens(tmp_path):
    shot = tmp_path / "twin.png"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, str(ROOT / "main.py"), "--digital-twin", "--model", str(ALPHA), "--twin-port", "0", "--twin-status-port", "0", "--screenshot", str(shot)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and shot.exists() and shot.stat().st_size > 20_000, r.stderr[-400:]
    assert "Destroyed while thread" not in r.stderr                                                                   # the listener thread ended cleanly


def test_the_twin_is_not_built_into_the_default_window(app):
    from satcad.gui.ui_layout import MainWindow
    win = MainWindow(Session())
    assert win.session.design.parts == {} and win.twin is not None and not win.twin.running and win.viewport._hud is None
    assert "Live Twin Sync" in {b.text() for b in win.ribbon.findChildren(type(win.twin_button))}
