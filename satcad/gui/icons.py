"""Ribbon and tree icons, drawn with QPainter so the application needs no image files (and scales crisply on any screen)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

LINE = QColor("#d6dae0")
FILL = QColor("#4d96ff")
FILL2 = QColor("#2f6fcf")
FILL3 = QColor("#8cc0ff")
WARN = QColor("#ffa31a")
GREEN = QColor("#5fd08a")
RED = QColor("#ff6b6b")


def _pen(p: QPainter, color=LINE, width=1.6):
    p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))


def _poly(pts):
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _cube(p, x=16, y=16, s=11, tops=FILL3, left=FILL, right=FILL2):
    """Isometric cube centred near (x, y) with edge ~s."""
    h = s * 0.5
    top = [(x, y - s), (x + s, y - h), (x, y), (x - s, y - h)]
    lft = [(x - s, y - h), (x, y), (x, y + s), (x - s, y + h)]
    rgt = [(x, y), (x + s, y - h), (x + s, y + h), (x, y + s)]
    for pts, c in ((top, tops), (lft, left), (rgt, right)):
        p.setBrush(c)
        _pen(p, QColor("#101318"), 1.0)
        p.drawPolygon(_poly(pts))


def _block(p):
    _cube(p)


def _cylinder(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawRect(QRectF(8, 9, 16, 15))
    p.setBrush(FILL2)
    p.drawEllipse(QRectF(8, 21, 16, 6))
    p.setBrush(FILL)
    p.drawRect(QRectF(8.5, 9, 15, 14))
    p.setBrush(FILL3)
    p.drawEllipse(QRectF(8, 5, 16, 8))


def _sphere(p):
    from PySide6.QtGui import QRadialGradient
    g = QRadialGradient(13, 12, 14)
    g.setColorAt(0, FILL3)
    g.setColorAt(1, FILL2)
    p.setBrush(QBrush(g))
    _pen(p, QColor("#101318"), 1.0)
    p.drawEllipse(QRectF(5, 5, 22, 22))


def _extrude(p):
    p.setBrush(FILL3)
    _pen(p, QColor("#101318"), 1.0)
    p.drawRect(QRectF(7, 17, 14, 10))
    p.setBrush(FILL)
    p.drawPolygon(_poly([(7, 17), (13, 10), (27, 10), (21, 17)]))
    p.setBrush(FILL2)
    p.drawPolygon(_poly([(21, 17), (27, 10), (27, 20), (21, 27)]))
    _pen(p, WARN, 1.6)
    p.drawLine(QPointF(14, 14), QPointF(14, 4))
    p.drawLine(QPointF(14, 4), QPointF(11, 7))
    p.drawLine(QPointF(14, 4), QPointF(17, 7))


def _revolve(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawEllipse(QRectF(6, 20, 20, 7))
    p.drawRect(QRectF(11, 8, 10, 15))
    p.setBrush(FILL3)
    p.drawEllipse(QRectF(11, 5, 10, 6))
    _pen(p, WARN, 1.4)
    p.drawLine(QPointF(16, 2), QPointF(16, 30))


def _unite(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    path = QPainterPath()
    path.addRect(QRectF(5, 6, 15, 15))
    path.addRect(QRectF(12, 12, 15, 15))
    p.drawPath(path.simplified())


def _subtract(p):
    path = QPainterPath()
    path.addRect(QRectF(5, 5, 20, 20))
    hole = QPainterPath()
    hole.addEllipse(QRectF(14, 14, 15, 15))
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawPath(path.subtracted(hole))
    p.setBrush(Qt.NoBrush)
    _pen(p, LINE, 1.2)
    p.setPen(QPen(LINE, 1.2, Qt.DashLine))
    p.drawEllipse(QRectF(14, 14, 15, 15))


def _intersect(p):
    a = QPainterPath()
    a.addRect(QRectF(5, 5, 18, 18))
    b = QPainterPath()
    b.addEllipse(QRectF(11, 11, 18, 18))
    p.setBrush(QColor(77, 150, 255, 60))
    p.setPen(QPen(LINE, 1.2, Qt.DashLine))
    p.drawPath(a)
    p.drawPath(b)
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawPath(a.intersected(b))


def _blend(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    path = QPainterPath()
    path.moveTo(5, 27)
    path.lineTo(5, 14)
    path.quadTo(5, 5, 14, 5)
    path.lineTo(27, 5)
    path.lineTo(27, 27)
    path.closeSubpath()
    p.drawPath(path)
    _pen(p, WARN, 1.6)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(5, 5, 18, 18), 90 * 16, 90 * 16)


def _chamfer(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawPolygon(_poly([(5, 27), (5, 13), (13, 5), (27, 5), (27, 27)]))
    _pen(p, WARN, 1.6)
    p.drawLine(QPointF(5, 13), QPointF(13, 5))


def _hole(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawRect(QRectF(5, 7, 22, 18))
    p.setBrush(QColor("#14171c"))
    p.drawEllipse(QRectF(10, 11, 12, 10))


def _shell(p):
    p.setBrush(FILL2)
    _pen(p, QColor("#101318"), 1.0)
    p.drawPolygon(_poly([(5, 12), (16, 6), (27, 12), (27, 24), (16, 30), (5, 24)]))
    p.setBrush(QColor("#14171c"))
    p.drawPolygon(_poly([(9, 13), (16, 9.5), (23, 13), (16, 17)]))


def _datum(p):
    _pen(p, RED, 2)
    p.drawLine(QPointF(8, 24), QPointF(26, 24))
    _pen(p, GREEN, 2)
    p.drawLine(QPointF(8, 24), QPointF(8, 6))
    _pen(p, FILL, 2)
    p.drawLine(QPointF(8, 24), QPointF(17, 29))


def _assembly(p):
    _cube(p, 11, 21, 7)
    _cube(p, 21, 12, 7, tops=QColor("#ffd08a"), left=WARN, right=QColor("#c97a00"))


def _hinge(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawRect(QRectF(4, 17, 24, 6))
    p.setBrush(FILL3)
    p.drawPolygon(_poly([(4, 17), (20, 4), (25, 8), (4, 17)]))
    _pen(p, WARN, 1.5)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(8, 8, 18, 18), 20 * 16, 70 * 16)


def _check(p):
    p.setBrush(QColor(255, 107, 107, 90))
    _pen(p, RED, 1.5)
    p.drawRect(QRectF(5, 7, 14, 14))
    p.setBrush(QColor(77, 150, 255, 90))
    _pen(p, FILL, 1.5)
    p.drawRect(QRectF(13, 13, 14, 14))


def _mass(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawPolygon(_poly([(6, 26), (26, 26), (21, 11), (11, 11)]))
    p.setBrush(Qt.NoBrush)
    _pen(p, WARN, 1.8)
    p.drawEllipse(QRectF(12, 4, 8, 8))


def _modal(p):
    _pen(p, FILL3, 2)
    path = QPainterPath()
    path.moveTo(3, 16)
    for i in range(1, 30):
        path.lineTo(3 + i, 16 + 9 * __import__("math").sin(i / 3.2) * (1 - i / 36))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    _pen(p, LINE, 1)
    p.drawLine(QPointF(3, 28), QPointF(29, 28))


def _static(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawRect(QRectF(5, 22, 22, 5))
    _pen(p, WARN, 2)
    for x in (9, 16, 23):
        p.drawLine(QPointF(x, 5), QPointF(x, 18))
        p.drawLine(QPointF(x, 18), QPointF(x - 2.5, 14.5))
        p.drawLine(QPointF(x, 18), QPointF(x + 2.5, 14.5))


def _new(p):
    p.setBrush(QColor("#e8ebef"))
    _pen(p, QColor("#101318"), 1.0)
    p.drawPolygon(_poly([(8, 4), (19, 4), (25, 10), (25, 28), (8, 28)]))
    p.setBrush(FILL3)
    p.drawPolygon(_poly([(19, 4), (19, 10), (25, 10)]))


def _open(p):
    p.setBrush(WARN)
    _pen(p, QColor("#101318"), 1.0)
    p.drawPolygon(_poly([(4, 8), (12, 8), (15, 11), (27, 11), (27, 26), (4, 26)]))
    p.setBrush(QColor("#ffcf7a"))
    p.drawPolygon(_poly([(4, 26), (8, 15), (30, 15), (27, 26)]))


def _save(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawRect(QRectF(5, 5, 22, 22))
    p.setBrush(QColor("#e8ebef"))
    p.drawRect(QRectF(9, 5, 14, 8))
    p.setBrush(FILL2)
    p.drawRect(QRectF(9, 18, 14, 9))


def _undo(p, flip=False):
    _pen(p, LINE, 2.4)
    p.setBrush(Qt.NoBrush)
    if flip:
        p.translate(32, 0)
        p.scale(-1, 1)
    path = QPainterPath()
    path.moveTo(9, 13)
    path.cubicTo(22, 8, 28, 16, 22, 24)
    p.drawPath(path)
    p.drawLine(QPointF(9, 13), QPointF(14, 8))
    p.drawLine(QPointF(9, 13), QPointF(15, 17))


def _redo(p):
    _undo(p, True)


def _delete(p):
    _pen(p, RED, 3)
    p.drawLine(QPointF(8, 8), QPointF(24, 24))
    p.drawLine(QPointF(24, 8), QPointF(8, 24))


def _export(p):
    p.setBrush(FILL)
    _pen(p, QColor("#101318"), 1.0)
    p.drawRect(QRectF(5, 14, 16, 13))
    _pen(p, GREEN, 2.2)
    p.drawLine(QPointF(14, 18), QPointF(27, 7))
    p.drawLine(QPointF(27, 7), QPointF(20, 7))
    p.drawLine(QPointF(27, 7), QPointF(27, 14))


def _view(p, which: str):
    h = 10
    if which == "iso":
        _cube(p, 16, 17, 10)
        return
    p.setBrush(QColor("#4b5058"))
    _pen(p, LINE, 1.2)
    p.drawRect(QRectF(6, 6, 20, 20))
    face = {"top": QRectF(6, 6, 20, 9), "front": QRectF(6, 15, 20, 11), "right": QRectF(17, 6, 9, 20), "left": QRectF(6, 6, 9, 20)}[which]
    p.setBrush(FILL)
    p.drawRect(face)


def _fit(p):
    _pen(p, LINE, 2)
    for a, b, c, d in ((5, 5, 12, 5), (5, 5, 5, 12), (27, 5, 20, 5), (27, 5, 27, 12), (5, 27, 12, 27), (5, 27, 5, 20), (27, 27, 20, 27), (27, 27, 27, 20)):
        p.drawLine(QPointF(a, b), QPointF(c, d))
    p.setBrush(FILL)
    p.drawRect(QRectF(11, 11, 10, 10))


def _shaded(p):
    _cube(p, 16, 17, 10)


def _wire(p):
    p.setBrush(Qt.NoBrush)
    _pen(p, LINE, 1.4)
    s, x, y = 10, 16, 17
    h = s * 0.5
    p.drawPolygon(_poly([(x, y - s), (x + s, y - h), (x + s, y + h), (x, y + s), (x - s, y + h), (x - s, y - h)]))
    p.drawLine(QPointF(x, y), QPointF(x, y - s))
    p.drawLine(QPointF(x, y), QPointF(x - s, y + h))
    p.drawLine(QPointF(x, y), QPointF(x + s, y + h))


def _expr(p):
    _pen(p, FILL3, 2)
    p.setFont(p.font())
    f = p.font()
    f.setPixelSize(20)
    f.setItalic(True)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(2, 2, 28, 28), Qt.AlignCenter, "fx")


ORANGE = QColor("#ff9a1f")
CYAN = QColor("#27c9ff")


def _sys_wire(p):
    _pen(p, ORANGE, 2.4)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline(_poly([(5, 24), (5, 10), (16, 10), (16, 22), (27, 22), (27, 8)]))
    p.setBrush(LINE)
    _pen(p, QColor("#101318"), 1.0)
    for x, y in ((5, 24), (27, 8)):
        p.drawEllipse(QPointF(x, y), 3.2, 3.2)


def _sys_pipe(p):
    p.setBrush(Qt.NoBrush)
    _pen(p, RED, 4.2)
    p.drawPolyline(_poly([(4, 9), (17, 9), (17, 17)]))
    _pen(p, FILL, 4.2)
    p.drawPolyline(_poly([(15, 25), (28, 25), (28, 17)]))
    _pen(p, LINE, 1.4)
    p.drawLine(QPointF(17, 17), QPointF(15, 25))


def _sys_fibre(p):
    p.setBrush(Qt.NoBrush)
    _pen(p, CYAN, 2.4)
    path = QPainterPath()
    path.moveTo(4, 24)
    path.cubicTo(4, 8, 28, 28, 28, 8)
    p.drawPath(path)
    p.setBrush(CYAN)
    _pen(p, QColor("#101318"), 1.0)
    for x, y in ((4, 24), (28, 8)):
        p.drawEllipse(QPointF(x, y), 3.0, 3.0)


def _sys_route3d(p):
    _pen(p, QColor("#7c8794"), 1.2)
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(_poly([(16, 3), (28, 9), (28, 23), (16, 29), (4, 23), (4, 9)]))
    p.drawLine(QPointF(16, 15), QPointF(16, 29))
    p.drawLine(QPointF(16, 15), QPointF(4, 9))
    p.drawLine(QPointF(16, 15), QPointF(28, 9))
    _pen(p, ORANGE, 2.4)
    p.drawPolyline(_poly([(8, 21), (14, 24), (14, 17), (22, 21), (22, 12)]))


def _sys_update(p):
    _pen(p, GREEN, 2.4)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(6, 6, 20, 20), 40 * 16, 270 * 16)
    p.setBrush(GREEN)
    p.drawPolygon(_poly([(24, 3), (28, 13), (18, 11)]))


def _sys_table(p):
    _pen(p, LINE, 1.4)
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(4, 6, 24, 20))
    p.drawLine(QPointF(4, 12), QPointF(28, 12))
    p.drawLine(QPointF(4, 19), QPointF(28, 19))
    p.drawLine(QPointF(14, 6), QPointF(14, 26))


_DRAW = {"block": _block, "cylinder": _cylinder, "sphere": _sphere, "extrude": _extrude, "revolve": _revolve, "unite": _unite,
         "subtract": _subtract, "intersect": _intersect, "blend": _blend, "chamfer": _chamfer, "hole": _hole, "shell": _shell,
         "datum": _datum, "assembly": _assembly, "hinge": _hinge, "check": _check, "mass": _mass, "modal": _modal, "static": _static,
         "new": _new, "open": _open, "save": _save, "undo": _undo, "redo": _redo, "delete": _delete, "export": _export, "fit": _fit,
         "shaded": _shaded, "wire": _wire, "expr": _expr, "sys_wire": _sys_wire, "sys_pipe": _sys_pipe, "sys_fibre": _sys_fibre, "sys_route3d": _sys_route3d,
         "sys_update": _sys_update, "sys_table": _sys_table,
         "view_iso": lambda p: _view(p, "iso"), "view_top": lambda p: _view(p, "top"), "view_front": lambda p: _view(p, "front"),
         "view_right": lambda p: _view(p, "right"), "view_left": lambda p: _view(p, "left")}
_CACHE: dict = {}


def icon(name: str, size: int = 32) -> QIcon:
    """The named icon (see _DRAW), rendered at 2x for sharpness. Unknown names give an empty icon rather than an error."""
    key = (name, size)
    if key in _CACHE:
        return _CACHE[key]
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    draw = _DRAW.get(name)
    if draw:
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size * 2 / 32.0, size * 2 / 32.0)
        draw(p)
        p.end()
    _CACHE[key] = QIcon(pix)
    return _CACHE[key]


ICON_NAMES = tuple(_DRAW)
