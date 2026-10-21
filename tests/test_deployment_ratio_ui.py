"""The DEPLOYMENT_RATIO slider in the Property Editor and the four one-click view presets, on a small model (no design files needed)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from satcad import Design  # noqa: E402


# ===== the window: DEPLOYMENT_RATIO slider and presets =======================================================================================
@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    pytest.importorskip("vtkmodules.vtkRenderingCore")
    from PySide6.QtWidgets import QApplication
    from satcad.gui import theme
    a = QApplication.instance() or QApplication([])
    theme.apply(a)
    return a


@pytest.fixture()
def window(app):
    from satcad.gui.session import Session
    from satcad.gui.ui_layout import MainWindow
    d = Design("mini")
    d.add("assembly", "arm")
    d.add("box", "hub", length=100, width=100, height=100, layer="shell")
    d.add("box", "boom", length=400, width=40, height=40, x=250, assembly="arm", layer="external")
    d.add("cylinder", "wire", radius=5, height=300, layer="power", y=200)
    d.add("cylinder", "pipe", radius=5, height=300, layer="fluid", y=-200)
    session = Session(d)
    win = MainWindow(session)
    try:
        win.show()
        win.viewport.grab_image()
    except Exception as e:
        pytest.skip(f"off-screen rendering is not available here: {e}")
    win.motion.load_dict({"engine": {"equations": ["arm_a(t) = 90 * (1 - DEPLOYMENT_RATIO)"], "params": {"DEPLOYMENT_RATIO": 1.0}, "ranges": {"DEPLOYMENT_RATIO": [0, 1]}},
                          "joints": [{"name": "Arm_A", "kind": "revolute", "assembly": "arm", "axis": [0, 0, 1], "pivot": [50, 0, 0], "expr": "arm_a"}],
                          "expose": ["DEPLOYMENT_RATIO"]})
    session.select(None)
    return win


def shown(win):
    return {n: bool(win.viewport._surfaces[n].GetVisibility()) for n in win.session.design.parts}


def test_deployment_ratio_slider_in_the_property_editor_moves_the_model_live(window):
    props = window.right.properties
    slider, label, lo, hi = props._motion_widgets["DEPLOYMENT_RATIO"]
    assert (lo, hi) == (0.0, 1.0) and slider.value() == 1000
    deployed = np.array(window.viewport._surfaces["boom"].GetBounds())
    slider.setValue(0)                                                                                       # drag to stowed
    assert window.motion.engine.get_param("DEPLOYMENT_RATIO") == 0.0 and label.text() == "0"
    folded = np.array(window.viewport._surfaces["boom"].GetBounds())
    assert deployed[1] > 400 and folded[1] < 100 and folded[3] > 300                                         # swung from along +X to along +Y
    slider.setValue(500)
    assert window.motion.engine.get_param("DEPLOYMENT_RATIO") == pytest.approx(0.5)


def test_motion_studio_changes_move_the_property_editor_slider(window):
    props = window.right.properties
    window.motion.set_parameter("DEPLOYMENT_RATIO", 0.25)
    assert props._motion_widgets["DEPLOYMENT_RATIO"][0].value() == 250


def test_view_presets_1_to_4(window):
    window.apply_preset(2)                                                                                   # stowed launch
    assert window.motion.engine.get_param("DEPLOYMENT_RATIO") == 0.0
    assert shown(window) == {"hub": True, "boom": True, "wire": False, "pipe": False} and not window.xray
    window.apply_preset(3)                                                                                   # internal schematics only
    assert shown(window) == {"hub": False, "boom": False, "wire": True, "pipe": True} and not window.xray
    window.apply_preset(4)                                                                                   # x-ray ghost hull with the diagrams
    assert shown(window) == {"hub": True, "boom": False, "wire": True, "pipe": True} and window.xray
    assert window.xray_map() == {"hub": window.XRAY_OPACITY}
    window.apply_preset(1)                                                                                   # full production
    assert shown(window) == {"hub": True, "boom": True, "wire": True, "pipe": True} and not window.xray
    assert window.motion.engine.get_param("DEPLOYMENT_RATIO") == 1.0
    assert window.right.properties._motion_widgets["DEPLOYMENT_RATIO"][0].value() == 1000


def test_presets_are_buttons_in_the_view_tab(window):
    from PySide6.QtWidgets import QToolButton
    texts = {b.text() for b in window.ribbon.findChildren(QToolButton)}
    assert {"1 Full", "2 Stowed", "3 Schematics", "4 X-Ray"} <= texts
    assert "Outside" in texts
