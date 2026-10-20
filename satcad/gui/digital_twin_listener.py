"""
Live Digital Twin Sync: listens for satSTK's telemetry on a background thread and articulates the 3D model in real time.

    satSTK  --UDP, JSON, 30 Hz-->  127.0.0.1:9876  --UdpReceiver (QThread)-->  DigitalTwin (GUI thread, 30 Hz)  -->  viewport

WHY THE VIEWPORT NEVER FREEZES
  - Sockets, JSON parsing and validation happen on the worker thread. The GUI thread only ever does a few matrix multiplications per tick.
  - Packets that arrive faster than the GUI can use are kept in a short list and applied in order in ONE pass per tick; the model is posed once.
  - The viewport moves existing actors with per-group matrices (the same mechanism as Motion Studio), so nothing is re-meshed.
  - A silent sender is not an error: the HUD shows WAITING, then STALE, and the model simply holds its last pose.

SAFETY: the socket is bound to the loopback address only (a non-loopback host is refused), datagrams are size-limited and parsed as JSON, never evaluated, and every number
is range-checked and clamped (see satcad.digital_twin). The link carries no commands in either direction.
"""
from __future__ import annotations

import json
import select
import socket
import threading
import time

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal

from ..digital_twin import (HOST, MAX_DATAGRAM, PORT, STATUS_PORT, Packet, PacketError, TwinModel, TwinState, hud_rows, parse_packet)

TICK_MS = 33                                   # about 30 GUI updates per second
MAX_PENDING = 64                               # packets held between two ticks; older ones are dropped (the stream is stateless)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class UdpReceiver(QObject):
    """Runs on its own QThread: receives datagrams, validates them, and hands the GUI the newest ones. Also sends a once-a-second status datagram back."""
    failed = Signal(str)
    listening = Signal(int)

    def __init__(self, host: str = HOST, port: int = PORT, status_port: int | None = STATUS_PORT):
        super().__init__()
        if host not in LOOPBACK:
            raise ValueError(f"The digital twin only listens on the loopback address (127.0.0.1), not '{host}'.")
        self.host, self.port, self.status_port = host, int(port), status_port
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._pending: list[Packet] = []
        self.frames = 0
        self.bad_packets = 0
        self.last_seq: int | None = None
        self.last_error = ""
        self.model_name = ""                                       # filled in by the GUI for the status datagram
        self.bound_port = 0

    # ---- worker thread -------------------------------------------------------------------------------------------------------------
    def run(self) -> None:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            sock.bind((self.host if self.host != "localhost" else "127.0.0.1", self.port))
        except OSError as e:
            sock.close()
            self.failed.emit(f"Can't listen on {self.host}:{self.port}: {e.strerror or e}. Is another satCAD already listening there?")
            return
        self.bound_port = sock.getsockname()[1]
        self.listening.emit(self.bound_port)
        sender = socket.socket(family, socket.SOCK_DGRAM)
        sender.setblocking(False)
        next_status = time.monotonic()
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([sock], [], [], 0.1)
                if ready:
                    for _ in range(256):                          # drain what is waiting, then yield
                        try:
                            data, _addr = sock.recvfrom(MAX_DATAGRAM + 1)
                        except (BlockingIOError, InterruptedError):
                            break
                        except OSError:
                            break
                        self._handle(data)
                        if not select.select([sock], [], [], 0)[0]:
                            break
                now = time.monotonic()
                if self.status_port and now >= next_status:
                    next_status = now + 1.0
                    self._send_status(sender)
        finally:
            sock.close()
            sender.close()

    def _handle(self, data: bytes) -> None:
        try:
            packet = parse_packet(data)
        except PacketError as e:
            with self._lock:
                self.bad_packets += 1
                self.last_error = str(e)
            return
        with self._lock:
            self.frames += 1
            if packet.seq is not None:
                self.last_seq = packet.seq
            self._pending.append(packet)
            del self._pending[:-MAX_PENDING]

    def _send_status(self, sender: socket.socket) -> None:
        with self._lock:
            body = dict(v=1, app="satCAD", twin="online", model=self.model_name, frames=self.frames, bad_packets=self.bad_packets, last_seq=self.last_seq)
        try:
            sender.sendto(json.dumps(body, separators=(",", ":")).encode(), (self.host if self.host != "localhost" else "127.0.0.1", self.status_port))
        except OSError:
            pass                                                     # nobody listening is fine

    # ---- GUI thread ----------------------------------------------------------------------------------------------------------------
    def take(self) -> list[Packet]:
        with self._lock:
            out, self._pending = self._pending, []
        return out

    def counters(self) -> tuple[int, int]:
        with self._lock:
            return self.frames, self.bad_packets

    def stop(self) -> None:
        self._stop.set()


class DigitalTwin(QObject):
    """Owns the receiver thread and applies telemetry to the main window's viewport. `start()` / `stop()` are the Live Digital Twin Sync toggle."""
    status_changed = Signal(str)
    running_changed = Signal(bool)

    def __init__(self, window, host: str = HOST, port: int = PORT, status_port: int | None = STATUS_PORT, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.host, self.port, self.status_port = host, port, status_port
        self.state = TwinState()
        self.model: TwinModel | None = None
        self.receiver: UdpReceiver | None = None
        self.thread: QThread | None = None
        self.running = False
        self.bound_port = 0
        self._saved_colors: dict = {}
        self._last_hud: tuple | None = None
        self._last_colors: dict | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        window.session.changed.connect(self._on_design_changed)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)                       # never leave the listener thread running when the program exits

    @property
    def viewport(self):
        return self.window.viewport

    # ---- start / stop --------------------------------------------------------------------------------------------------------------
    def start(self) -> bool:
        if self.running:
            return True
        try:
            self.receiver = UdpReceiver(self.host, self.port, self.status_port)
        except ValueError as e:
            self._say("error", str(e))
            return False
        self.model = self._build_model()
        motion = getattr(self.window, "motion", None)
        if motion is not None:
            motion.suspend()                                         # the twin owns the moving groups and colours while it is on
        self.state = TwinState()
        self._last_hud = self._last_colors = None
        self._bind()
        self.receiver.model_name = self.window.session.design.name
        self.thread = QThread()
        self.receiver.moveToThread(self.thread)
        self.thread.started.connect(self.receiver.run)
        self.receiver.failed.connect(self._on_failed)
        self.receiver.listening.connect(self._on_listening)
        self.thread.start()
        self.running = True
        self.timer.start()
        self.running_changed.emit(True)
        self._say("info", f"Live Digital Twin Sync on: listening for satSTK on {self.host}:{self.port}. {self.model.summary() if self.model else ''}")
        self._tick()
        return True

    def stop(self) -> None:
        if not self.running and self.thread is None:
            return
        self.running = False
        self.timer.stop()
        if self.receiver is not None:
            self.receiver.stop()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait(2000)
            self.thread = None
        self.receiver = None
        v = self.viewport
        v.set_overlay_rays([])
        v.set_hud(None)
        v.set_part_colors(self._saved_colors or None)
        v.bind_motion_groups({})
        motion = getattr(self.window, "motion", None)
        if motion is not None:
            motion.resume()
        self.running_changed.emit(False)
        self._say("info", "Live Digital Twin Sync off.")

    def set_enabled(self, on: bool) -> bool:
        if on:
            return self.start()
        self.stop()
        return True

    # ---- model and binding ---------------------------------------------------------------------------------------------------------
    def _build_model(self) -> TwinModel | None:
        try:
            return TwinModel(self.window.session.design)
        except Exception as e:                                       # a model the twin cannot read must not break the application
            self._say("warn", f"Digital twin: can't read this model's moving parts ({type(e).__name__}: {e}). The HUD still works.")
            return None

    def _bind(self) -> None:
        self.viewport.bind_motion_groups(self.model.groups if self.model else {})

    def _on_design_changed(self) -> None:
        """The part was rebuilt (new actors). The window rebuilds the scene first; then (next turn of the event loop) the twin rebinds and re-poses."""
        if self.running:
            QTimer.singleShot(0, self._rebuild)

    def _rebuild(self) -> None:
        if not self.running or self.receiver is None:
            return
        self.model = self._build_model()
        self.receiver.model_name = self.window.session.design.name
        self._bind()
        self._last_hud = self._last_colors = None
        self._apply(force=True)

    # ---- the GUI tick --------------------------------------------------------------------------------------------------------------
    def _tick(self) -> None:
        if not self.running or self.receiver is None:
            return
        packets = self.receiver.take()
        changed = False
        for p in packets:
            changed |= self.state.apply(p)
        self.state.bad_packets = self.receiver.counters()[1]
        self._apply(force=changed)

    def _apply(self, force: bool = False) -> None:
        v, model, state = self.viewport, self.model, self.state
        if force and model is not None:
            if model.articulated:
                matrices = model.matrices(state)
                v.set_group_matrices(matrices)
                v.set_overlay_rays(model.rays(state, matrices))
            colors = model.colors(state)
            if colors != self._last_colors:
                self._last_colors = colors
                v.set_part_colors(colors or None)
        rows = hud_rows(state)
        key = (tuple(rows),)
        if key != self._last_hud:
            self._last_hud = key
            v.set_hud(rows, state.status())
        self.status_changed.emit(state.status())

    # ---- messages ------------------------------------------------------------------------------------------------------------------
    def _on_listening(self, port: int) -> None:
        self.bound_port = port

    def _on_failed(self, message: str) -> None:
        self._say("error", message)
        if self.running:
            self.stop()

    def _say(self, level: str, text: str) -> None:
        self.window.session.message.emit(level, text)
