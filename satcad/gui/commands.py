"""Commands the ribbon and menus trigger: dialogs for features that need choices, immediate creation for simple primitives,
and the analysis / report actions. Each command talks only to the Session (never to the viewport directly)."""
from __future__ import annotations

import tempfile
import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel, QLineEdit,
                               QMessageBox, QSpinBox, QVBoxLayout)
from PySide6.QtCore import Qt

from ..engine import DesignError
from .properties import CHOICES, parse_value
from .session import Session


class ParamDialog(QDialog):
    """A small modal form. fields: list of dicts with key, label, type ('text' | 'choice' | 'int'), default, choices."""

    def __init__(self, parent, title: str, fields: list[dict], note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        self.fields = fields
        self.widgets: dict = {}
        lay = QVBoxLayout(self)
        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setStyleSheet("color:#9aa2ad")
            lay.addWidget(n)
        form = QFormLayout()
        lay.addLayout(form)
        for f in fields:
            kind = f.get("type", "text")
            if kind == "choice":
                w = QComboBox()
                w.setEditable(f.get("editable", False))
                w.addItems([str(c) for c in f["choices"]])
                if f.get("default") is not None:
                    w.setCurrentText(str(f["default"]))
            elif kind == "int":
                w = QSpinBox()
                w.setRange(f.get("min", 1), f.get("max", 1000))
                w.setValue(int(f.get("default", 1)))
            else:
                w = QLineEdit("" if f.get("default") is None else str(f["default"]))
            self.widgets[f["key"]] = w
            form.addRow(f["label"], w)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def values(self) -> dict:
        out = {}
        for f in self.fields:
            w = self.widgets[f["key"]]
            if isinstance(w, QComboBox):
                text = w.currentText()
            elif isinstance(w, QSpinBox):
                out[f["key"]] = w.value()
                continue
            else:
                text = w.text()
            out[f["key"]] = text
        return out


class Commands:
    def __init__(self, session: Session, window):
        self.s = session
        self.w = window

    # ---- helpers ---------------------------------------------------------------------------------------------------------------------
    def _bodies(self) -> list[str]:
        return self.s.body_names()

    def _default_body(self) -> str | None:
        sel = self.s.selected
        if sel in self.s.design.parts:
            return sel
        f = self.s.design.features.get(sel) if sel else None
        if f is not None and f.raw.get("target") in self.s.design.parts:
            return f.raw["target"]
        bodies = self._bodies()
        return bodies[-1] if bodies else None

    def _face(self):
        """The face picked in the viewport as (body, info) when it is a flat axis-aligned face of an existing body, else None."""
        picked = getattr(self.w, "picked_face", None)
        if picked and picked[0] in self.s.design.parts and picked[1].get("side"):
            return picked
        return None

    def _need_bodies(self, count: int = 1) -> bool:
        if len(self._bodies()) < count:
            self.s.message.emit("warn", "Create a body first (Block, Cylinder or Sphere on the Home tab)." if count == 1
                                else f"This needs {count} bodies. Create them first (Block, Cylinder or Sphere).")
            return False
        return True

    def _ask(self, title: str, fields: list[dict], note: str = "") -> dict | None:
        dlg = ParamDialog(self.w, title, fields, note)
        return dlg.values() if dlg.exec() == QDialog.Accepted else None

    def _num(self, text: str):
        try:
            return parse_value(text)
        except ValueError as e:
            self.s.message.emit("error", str(e))
            return None

    # ---- create ----------------------------------------------------------------------------------------------------------------------
    def block(self) -> None:
        self.s.add("box", length=100, width=100, height=100)

    def cylinder(self) -> None:
        self.s.add("cylinder", radius=30, height=100)

    def sphere(self) -> None:
        self.s.add("sphere", radius=50)

    def boolean(self, operation: str) -> None:
        if not self._need_bodies(2):
            return
        bodies = self._bodies()
        target = self._default_body() or bodies[0]
        tool_choices = [b for b in bodies if b != target] or bodies
        v = self._ask(operation.capitalize(), [
            dict(key="target", label="Target body", type="choice", choices=bodies, default=target),
            dict(key="tool", label="Tool body", type="choice", choices=bodies, default=tool_choices[-1])],
            note={"unite": "Joins the two bodies into one. They must touch or overlap.",
                  "subtract": "Cuts the tool out of the target. The tool is consumed.",
                  "intersect": "Keeps only the volume the two bodies share."}[operation])
        if v:
            self.s.add("boolean", operation=operation, target=v["target"], tool=v["tool"])

    def blend(self, chamfer: bool = False) -> None:
        if not self._need_bodies():
            return
        face = self._face()
        v = self._ask("Chamfer" if chamfer else "Edge Blend", [
            dict(key="target", label="Body", type="choice", choices=self._bodies(), default=face[0] if face else self._default_body()),
            dict(key="edges", label="Edges", type="choice", choices=CHOICES["edges"], default=f"face:{face[1]['side']}" if face else "all"),
            dict(key="size", label="Distance (mm)" if chamfer else "Radius (mm)", default="5")],
            note="'all' = every edge. 'parallel:z' = edges running along Z. 'face:+z' = the edges around the top face.")
        if v:
            size = self._num(v["size"])
            if size is not None:
                self.s.add("fillet_edges", target=v["target"], edges=v["edges"], **({"chamfer": size} if chamfer else {"radius": size}))

    def hole(self) -> None:
        if not self._need_bodies():
            return
        face = self._face()
        if face and not face[1]["at_outer_face"]:
            self.s.message.emit("warn", "The hole is measured from the body's outermost face in that direction, not from the face you picked; "
                                        "set a depth to control how far it goes.")
        v = self._ask("Hole", [
            dict(key="target", label="Body", type="choice", choices=self._bodies(), default=face[0] if face else self._default_body()),
            dict(key="side", label="Face", type="choice", choices=CHOICES["side"], default=face[1]["side"] if face else "+z"),
            dict(key="standard", label="Size", type="choice", choices=CHOICES["standard"], default="M6"),
            dict(key="hole_type", label="Type", type="choice", choices=CHOICES["hole_type"], default="clearance"),
            dict(key="depth", label="Depth (0 = through)", default="0"),
            dict(key="offset_u", label="Offset U (mm)", default=f"{face[1]['offset_u']:g}" if face else "0"),
            dict(key="offset_v", label="Offset V (mm)", default=f"{face[1]['offset_v']:g}" if face else "0")],
            note=("Placed where you clicked." if face else "ISO hole at the centre of the chosen face, shifted by the offsets. "
                  "Tip: switch the selection filter to Face and click a face to place the hole where you click."))
        if v:
            vals = {k: self._num(v[k]) for k in ("depth", "offset_u", "offset_v")}
            if None not in vals.values():
                self.s.add("hole", target=v["target"], side=v["side"], standard=v["standard"], hole_type=v["hole_type"], **vals)

    def shell(self) -> None:
        if not self._need_bodies():
            return
        face = self._face()
        v = self._ask("Shell", [
            dict(key="target", label="Body", type="choice", choices=self._bodies(), default=face[0] if face else self._default_body()),
            dict(key="open_faces", label="Open faces", default=face[1]["side"] if face else "+z"),
            dict(key="thickness", label="Wall thickness (mm)", default="5")],
            note="Hollows the body. List the faces to leave open, separated by commas, for example +z or +z,-z.")
        if v:
            t = self._num(v["thickness"])
            faces = [f.strip() for f in v["open_faces"].split(",") if f.strip()]
            if t is not None:
                self.s.add("shell", target=v["target"], open_faces=faces, thickness=t)

    def assembly(self) -> None:
        v = self._ask("New Assembly", [
            dict(key="name", label="Name", default=self.s.unique_name("assy")),
            dict(key="joint_axis", label="Hinge axis", type="choice", choices=["(none)", "x", "y", "z"], default="(none)"),
            dict(key="angle_deg", label="Hinge angle (deg or =formula)", default="0"),
            dict(key="pivot_y", label="Pivot Y (mm)", default="0"), dict(key="pivot_z", label="Pivot Z (mm)", default="0")],
            note="A group of parts. Give it a hinge axis and the whole group swings about it. Put a part in it by setting the part's 'assembly' property.")
        if v:
            settings = {}
            if v["joint_axis"] != "(none)":
                settings.update(joint_axis=v["joint_axis"], angle_deg=self._num(v["angle_deg"]) or 0,
                                pivot_y=self._num(v["pivot_y"]) or 0, pivot_z=self._num(v["pivot_z"]) or 0)
            self.s.add("assembly", v["name"].strip(), **settings)

    def load_alpha_model(self) -> None:
        """Replace the open part with the Orbital-Node-Alpha assembly (generate_orbital_node_assembly.py)."""
        if not self.w.confirm_discard():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            from generate_orbital_node_assembly import build
            design = build()
        except Exception as e:                                       # a missing module or a failed build must not crash the window
            self.s.message.emit("error", f"Couldn't build Orbital-Node-Alpha: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.s.load_design(design, f"Orbital-Node-Alpha built: {len(design.parts)} bodies in {len(design.features)} features. "
                                   "Untick the bus hull in the Part Navigator to see the server blades and coolant pipes.")

    # ---- edit / files ------------------------------------------------------------------------------------------------------------------
    def delete_selected(self) -> None:
        if self.s.selected:
            self.s.remove(self.s.selected)
        else:
            self.s.message.emit("warn", "Select a feature first.")

    def new(self) -> None:
        if self.w.confirm_discard():
            self.s.new("untitled")

    def open(self) -> None:
        if not self.w.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self.w, "Open part", str(Path.cwd()), "SatCAD parts (*.json);;All files (*)")
        if path:
            self.s.open(path)

    def save(self) -> bool:
        if self.s.path is None:
            return self.save_as()
        return self.s.save()

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self.w, "Save part", str(Path.cwd() / f"{self.s.design.name}.json"), "SatCAD parts (*.json)")
        return bool(path) and self.s.save(path)

    def export(self, fmt: str) -> None:
        d = self.s.design
        if not d.parts:
            self.s.message.emit("warn", "Nothing to export: the part is empty.")
            return
        names = {"step": "STEP", "stl": "STL (mesh)", "glb": "GLB (web / AR)"}
        path, _ = QFileDialog.getSaveFileName(self.w, f"Export {names[fmt]}", str(Path.cwd() / f"{d.name}.{fmt}"), f"{names[fmt]} (*.{fmt})")
        if not path:
            return
        try:
            with tempfile.TemporaryDirectory() as tmp:
                info = d.export(tmp, d.name, [fmt])
                Path(path).write_bytes(Path(info[fmt]["path"]).read_bytes())
            self.s.message.emit("info", f"Exported {path}")
        except (DesignError, OSError) as e:
            self.s.message.emit("error", f"Export failed: {e}")

    # ---- reports and analysis -------------------------------------------------------------------------------------------------------
    def mass_properties(self) -> None:
        m = self.s.design.measure()
        if not m.get("parts"):
            self.s.message.emit("warn", "The part is empty.")
            return
        lines = [f"Mass properties of {self.s.design.name}: {m['parts']} bodies, mass {m['total_mass_kg']:,.3f} kg"]
        if m.get("centre_of_mass_mm"):
            lines.append("  Centre of mass (m): " + ", ".join(f"{v / 1000:,.3f}" for v in m["centre_of_mass_mm"]))
        if m.get("principal_moments_kg_m2"):
            lines.append("  Principal moments (kg m2): " + " / ".join(f"{v:,.4g}" for v in m["principal_moments_kg_m2"]))
        for role, kg in (m.get("mass_by_role_kg") or {}).items():
            lines.append(f"  {role}: {kg:,.3f} kg")
        self.s.message.emit("info", "\n".join(lines))
        self.s.select(None)

    def check(self) -> None:
        if not self.s.design.parts:
            self.s.message.emit("warn", "The part is empty.")
            return
        r = self.s.design.check()
        if r["ok"] and not r["floating_parts"]:
            self.s.message.emit("info", "Interference check: no overlapping or floating bodies.")
            return
        lines = ["Interference check:"]
        lines += [f"  OVERLAP {o['a']} / {o['b']}: {o['overlap_cm3']:,.3f} cm3" for o in r["overlaps"]]
        lines += [f"  FLOATING {f['part']} (nearest body {f['nearest_gap_mm']:,.1f} mm away)" for f in r["floating_parts"]]
        self.s.message.emit("warn" if r["overlaps"] else "info", "\n".join(lines))

    def _analysis_dialog(self, title: str, extra: list[dict]):
        bodies = ["(none: free in orbit)"] + self._bodies()
        fields = [dict(key="part", label="Held fixed", type="choice", choices=bodies, default=bodies[0]),
                  dict(key="side", label="Face", type="choice", choices=CHOICES["side"], default="-z"),
                  dict(key="constraint", label="Clamp", type="choice", choices=["perimeter", "face"], default="perimeter")] + extra
        return self._ask(title, fields, note="Structural analysis (BETA): flat-plate shell model, linear, concept stage. Fold hinges first to analyse a stowed launch state.")

    def _model(self, v):
        from .. import fem
        fixed = None if v["part"].startswith("(none") else {"part": v["part"], "side": v["side"]}
        return fem.from_design(self.s.design, fixed=fixed, constraint=v["constraint"])

    def modal(self) -> None:
        if not self._need_bodies():
            return
        v = self._analysis_dialog("Modal Analysis", [dict(key="modes", label="Modes", type="int", default=6, min=1, max=20)])
        if not v:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            from ..viewer import mode_viewer_html
            model = self._model(v)
            res = model.modal(v["modes"])
            html = Path(tempfile.gettempdir()) / f"{self.s.design.name}_modes.html"
            html.write_text(mode_viewer_html(model, res, self.s.design.name), encoding="utf-8")
        except (DesignError, ValueError) as e:
            self.s.message.emit("error", f"Modal analysis: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        freqs = ", ".join(f"{f:.3f}" for f in res["frequencies_hz"])
        self.s.message.emit("info", f"Modal analysis: frequencies (Hz): {freqs}\n  model mass {res['total_mass_tonne'] * 1000:,.1f} kg; "
                                    f"{res['rigid_body_modes_removed']} rigid-body modes removed. Animated shapes opened in your browser ({html.name}).")
        webbrowser.open(html.as_uri())

    def static(self) -> None:
        if not self._need_bodies():
            return
        v = self._analysis_dialog("Static Analysis", [dict(key="gx", label="Load g, X", default="0"), dict(key="gy", label="Load g, Y", default="0"),
                                                      dict(key="gz", label="Load g, Z", default="6")])
        if not v:
            return
        if v["part"].startswith("(none"):
            self.s.message.emit("error", "A stress analysis needs a support: choose the body that is held fixed.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            model = self._model(v)
            g = [float(v[k]) for k in ("gx", "gy", "gz")]
            r = model.static(accel_g=g)
        except (DesignError, ValueError) as e:
            self.s.message.emit("error", f"Static analysis: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        lines = [f"Static analysis ({g[0]:g}, {g[1]:g}, {g[2]:g}) g: max stress {r['max_von_mises_mpa']:,.2f} MPa in {r['worst_plate']}, "
                 f"max displacement {r['max_displacement_mm']:,.2f} mm"]
        for name, st in sorted(r["stress_by_plate"].items(), key=lambda kv: -kv[1]["max_von_mises_mpa"])[:6]:
            ms = st["margin_of_safety"]
            lines.append(f"  {name}: {st['max_von_mises_mpa']:,.2f} MPa" + ("" if ms is None else f", margin of safety {ms:+.2f}"))
        self.s.message.emit("info", "\n".join(lines))
