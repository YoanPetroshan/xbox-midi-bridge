"""Controller diagram with leader lines to labels showing the current mapping.

Everything is painted with QPainter in logical coordinates (1200 wide) and scaled.
Two layouts: Xbox and PlayStation (DualSense / DualShock 4).
The drawing is original (simple shapes), not a copy of protected artwork.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from mapping import (GYRO_IDS, INPUT_LABELS, PADDLE_IDS, TOUCH_IDS, TRIGGER_IDS, ButtonMapping,
                     Profile, describe_axis, describe_button, layer_label, layer_mods, midi_signature,
                     unreliable_inputs)
from i18n import tr
from ui import theme

W, BASE_H = 1200.0, 720.0  # the controller is drawn in 1200×720; the label columns may be taller
LABEL_W = 262.0
LEFT_X, RIGHT_X = 16.0, W - 16.0 - LABEL_W
TOP_Y = 22.0
STICK_R, THUMB_R, STICK_TRAVEL = 40.0, 22.0, 18.0
FACE_OFF, FACE_R = 38.0, 17.0
PS_FACE_COLORS = {"a": "#6c9ce8", "b": "#e0605e", "x": "#d77fc0", "y": "#3fbfa8"}


def _cross(c: QPointF, off: float = 22.0) -> dict[str, QPointF]:
    return {"dpup": QPointF(c.x(), c.y() - off), "dpdown": QPointF(c.x(), c.y() + off),
            "dpleft": QPointF(c.x() - off, c.y()), "dpright": QPointF(c.x() + off, c.y())}


def _face(c: QPointF, off: float = FACE_OFF) -> dict[str, QPointF]:
    return {"y": QPointF(c.x(), c.y() - off), "a": QPointF(c.x(), c.y() + off),
            "x": QPointF(c.x() - off, c.y()), "b": QPointF(c.x() + off, c.y())}


@dataclass
class Layout:
    """Where every part of one controller family sits, and the label order per column."""
    family: str
    lt: QPointF
    rt: QPointF
    lb: QPointF
    rb: QPointF
    lstick: QPointF
    rstick: QPointF
    dpad: QPointF
    face: QPointF
    back: QPointF
    start: QPointF
    guide: QPointF
    misc1: QPointF
    paddles: dict
    left: list
    right: list
    body: QPainterPath
    touchpad: QRectF | None = None
    gyro: QPointF | None = None

    @property
    def dpad_pos(self):
        return _cross(self.dpad)

    @property
    def face_pos(self):
        return _face(self.face)

    def centers(self) -> dict[str, QPointF]:
        c = dict(self.face_pos)
        c.update(self.dpad_pos)
        c.update({"lefttrigger": self.lt, "righttrigger": self.rt, "leftshoulder": self.lb,
                  "rightshoulder": self.rb, "back": self.back, "start": self.start,
                  "guide": self.guide, "misc1": self.misc1,
                  "leftstick": self.lstick, "rightstick": self.rstick})
        c.update(self.paddles)
        if self.touchpad is not None:
            c["touchpad"] = self.touchpad.center()
        return c

    def anchors(self) -> dict[str, QPointF]:
        a = self.centers()
        a.update({
            "lefttrigger": QPointF(self.lt.x() - 30, self.lt.y()),
            "righttrigger": QPointF(self.rt.x() + 30, self.rt.y()),
            "leftshoulder": QPointF(self.lb.x() - 40, self.lb.y()),
            "rightshoulder": QPointF(self.rb.x() + 40, self.rb.y()),
            "lefty": QPointF(self.lstick.x(), self.lstick.y() - STICK_R),
            "leftx": QPointF(self.lstick.x() - STICK_R, self.lstick.y()),
            "leftstick": QPointF(self.lstick.x() - 20, self.lstick.y() + 34),
            "righty": QPointF(self.rstick.x(), self.rstick.y() - STICK_R),
            "rightx": QPointF(self.rstick.x() + STICK_R, self.rstick.y()),
            "rightstick": QPointF(self.rstick.x() + 20, self.rstick.y() + 34),
        })
        if self.touchpad is not None:
            t = self.touchpad
            a["touchx"] = QPointF(t.left() + 4, t.center().y() - 12)
            a["touchy"] = QPointF(t.left() + 4, t.center().y() + 14)
            a["touchpad"] = QPointF(t.right() - 4, t.center().y())
        if self.gyro is not None:
            a["gyroyaw"] = QPointF(self.gyro.x() + 10, self.gyro.y() - 4)
            a["gyropitch"] = QPointF(self.gyro.x() + 10, self.gyro.y() + 6)
        return a


def _xbox_body() -> QPainterPath:
    p = QPainterPath(QPointF(455, 245))
    p.cubicTo(520, 232, 680, 232, 745, 245)
    p.cubicTo(800, 252, 835, 280, 850, 330)
    p.cubicTo(875, 410, 905, 520, 905, 575)
    p.cubicTo(905, 628, 858, 642, 832, 614)
    p.cubicTo(802, 582, 780, 535, 738, 520)
    p.cubicTo(690, 505, 510, 505, 462, 520)
    p.cubicTo(420, 535, 398, 582, 368, 614)
    p.cubicTo(342, 642, 295, 628, 295, 575)
    p.cubicTo(295, 520, 325, 410, 350, 330)
    p.cubicTo(365, 280, 400, 252, 455, 245)
    p.closeSubpath()
    return p


def _ps_body() -> QPainterPath:
    p = QPainterPath(QPointF(430, 250))
    p.cubicTo(520, 238, 680, 238, 770, 250)
    p.cubicTo(850, 255, 880, 300, 895, 380)
    p.cubicTo(915, 480, 930, 570, 915, 610)
    p.cubicTo(900, 652, 845, 652, 820, 615)
    p.cubicTo(790, 575, 760, 522, 720, 508)
    p.cubicTo(680, 496, 520, 496, 480, 508)
    p.cubicTo(440, 522, 410, 575, 380, 615)
    p.cubicTo(355, 652, 300, 652, 285, 610)
    p.cubicTo(270, 570, 285, 480, 305, 380)
    p.cubicTo(320, 300, 350, 255, 430, 250)
    p.closeSubpath()
    return p


XBOX = Layout(
    family="xbox",
    lt=QPointF(445, 186), rt=QPointF(755, 186), lb=QPointF(445, 228), rb=QPointF(755, 228),
    lstick=QPointF(450, 322), rstick=QPointF(668, 418), dpad=QPointF(532, 418), face=QPointF(752, 322),
    back=QPointF(560, 320), start=QPointF(640, 320), guide=QPointF(600, 272), misc1=QPointF(600, 356),
    paddles={"paddle1": QPointF(822, 565), "paddle3": QPointF(792, 612),
             "paddle2": QPointF(378, 565), "paddle4": QPointF(408, 612)},
    left=["lefttrigger", "leftshoulder", "lefty", "leftx", "leftstick", "back", "misc1",
          "dpup", "dpleft", "dpright", "dpdown", "paddle2", "paddle4"],
    right=["righttrigger", "rightshoulder", "guide", "y", "start", "x", "b", "a",
           "righty", "rightx", "rightstick", "paddle1", "paddle3"],
    body=_xbox_body(),
)

PLAYSTATION = Layout(
    family="playstation",
    lt=QPointF(445, 186), rt=QPointF(755, 186), lb=QPointF(445, 228), rb=QPointF(755, 228),
    lstick=QPointF(522, 432), rstick=QPointF(678, 432), dpad=QPointF(420, 335), face=QPointF(780, 335),
    back=QPointF(498, 270), start=QPointF(702, 270), guide=QPointF(600, 446), misc1=QPointF(600, 480),
    paddles={"paddle1": QPointF(842, 575), "paddle2": QPointF(358, 575),
             "paddle3": QPointF(722, 488), "paddle4": QPointF(478, 488)},
    left=["lefttrigger", "leftshoulder", "back", "dpup", "dpleft", "dpright", "dpdown",
          "touchx", "touchy", "lefty", "leftx", "leftstick", "misc1", "paddle2", "paddle4"],
    right=["righttrigger", "rightshoulder", "start", "y", "x", "b", "a", "touchpad",
           "gyroyaw", "gyropitch", "guide", "righty", "rightx", "rightstick", "paddle1", "paddle3"],
    body=_ps_body(),
    touchpad=QRectF(522, 250, 156, 82),
    gyro=QPointF(600, 380),
)
LAYOUTS = {"xbox": XBOX, "playstation": PLAYSTATION}
# Inputs only drawn when the controller has them (or, for PlayStation, by default).
OPTIONAL = set(PADDLE_IDS) | {"touchpad"} | set(TOUCH_IDS) | set(GYRO_IDS)
PS_DEFAULT_EXTRAS = {"touchpad"} | set(TOUCH_IDS) | set(GYRO_IDS)


@dataclass
class LabelSlot:
    input_id: str
    rect: QRectF
    left: bool


class ControllerView(QWidget):
    inputClicked = Signal(str, QRectF)  # input_id, global rect of the label

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(760, 460)
        self.profile: Profile | None = None
        self.snap: dict = {}
        self.axis_values: dict[str, float] = {}
        self.axis_raw_out: dict[str, int] = {}
        self.hover: str | None = None
        self.selected: str | None = None
        self.learn = False
        self.layer = ""  # displayed layer (key)
        self.layer_live = False  # True: the layer comes from modifiers held on the controller
        self._sigs: dict = {}
        self.seen_ever: set[str] = set()
        self.layout = XBOX
        self.extras: set[str] = set()  # optional inputs the controller has
        self.slots: list[LabelSlot] = []
        self.H = BASE_H
        self.cy = 0.0  # vertical offset of the controller drawing inside the view
        self._f_title = QFont()
        self._f_title.setPixelSize(14)
        self._f_title.setBold(True)
        self._f_small = QFont()
        self._f_small.setPixelSize(12)
        self._f_part = QFont()
        self._f_part.setPixelSize(13)
        self._f_part.setBold(True)
        self._rebuild_slots()

    # ---------------------------------------------------------------- data
    def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        self.update()

    def set_family(self, family: str, extras: set[str] | None = None) -> None:
        """Switch the drawing to a controller family; `extras` = optional inputs it has."""
        layout = LAYOUTS.get(family, XBOX)
        if extras is None:
            extras = set(PS_DEFAULT_EXTRAS) if layout is PLAYSTATION else set()
        extras = set(extras) & OPTIONAL
        if layout is self.layout and extras == self.extras:
            return
        self.layout, self.extras = layout, extras
        self._rebuild_slots()
        self.update()

    def visible_inputs(self) -> list[str]:
        return [s.input_id for s in self.slots]

    def _rebuild_slots(self) -> None:
        keep = lambda ids: [i for i in ids if i not in OPTIONAL or i in self.extras]
        left, right = keep(self.layout.left), keep(self.layout.right)
        rows = max(len(left), len(right))
        step = 62.0 if rows <= 11 else 58.0
        label_h = step - 10.0
        self.H = max(BASE_H, TOP_Y * 2 + rows * step - 10.0)
        self.cy = (self.H - BASE_H) / 2
        self.slots = []
        for col, ids, x in ((True, left, LEFT_X), (False, right, RIGHT_X)):
            for i, iid in enumerate(ids):
                self.slots.append(LabelSlot(iid, QRectF(x, TOP_Y + i * step, LABEL_W, label_h), col))

    def set_state(self, snap: dict, axis_values: dict[str, float], axis_raw_out: dict[str, int]) -> None:
        self.snap = snap
        self.axis_values = axis_values
        self.axis_raw_out = axis_raw_out
        self.update()

    # ---------------------------------------------------------------- geometry
    def _transform(self):
        s = min(self.width() / W, self.height() / self.H)
        ox = (self.width() - W * s) / 2
        oy = (self.height() - self.H * s) / 2
        return s, ox, oy

    def _to_logical(self, pt) -> QPointF:
        s, ox, oy = self._transform()
        return QPointF((pt.x() - ox) / s, (pt.y() - oy) / s)

    def label_global_rect(self, input_id: str) -> QRectF:
        s, ox, oy = self._transform()
        for slot in self.slots:
            if slot.input_id == input_id:
                r = slot.rect
                tl = self.mapToGlobal(QPointF(ox + r.x() * s, oy + r.y() * s).toPoint())
                return QRectF(tl.x(), tl.y(), r.width() * s, r.height() * s)
        return QRectF()

    def _hit(self, pos) -> str | None:
        p = self._to_logical(pos)
        for slot in self.slots:
            if slot.rect.contains(p):
                return slot.input_id
        # click directly on a part of the diagram
        p = QPointF(p.x(), p.y() - self.cy)
        visible = set(self.visible_inputs())
        best, best_d = None, 22.0 ** 2
        for iid, a in self.layout.centers().items():
            d = (a.x() - p.x()) ** 2 + (a.y() - p.y()) ** 2
            if iid in visible and d < best_d:
                best, best_d = iid, d
        if best is None and self.layout.touchpad is not None and self.layout.touchpad.contains(p):
            best = "touchpad" if "touchpad" in visible else None
        return best

    # ---------------------------------------------------------------- mouse
    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self.hover:
            self.hover = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            h = self._hit(e.position())
            if h:
                self.inputClicked.emit(h, self.label_global_rect(h))

    # ---------------------------------------------------------------- painting
    def _active(self, iid: str) -> bool:
        if iid in self.snap.get("buttons", {}):
            return bool(self.snap["buttons"].get(iid))
        if iid in TOUCH_IDS:
            return bool(self.snap.get("touch_down"))
        v = self.snap.get("axes", {}).get(iid, 0.0)
        m = self.profile.axes.get(iid) if self.profile else None
        dz = m.deadzone if m else 0.08
        if iid in GYRO_IDS:
            return bool(self.snap.get("gyro")) and abs(v) > max(dz, 0.02)
        return abs(v) > dz

    def _bmap(self, iid: str) -> ButtonMapping | None:
        return self.profile.effective_map(self.layer, iid)

    def _is_modifier(self, iid: str) -> bool:
        if iid in self.profile.axes:
            return False
        if iid in layer_mods(self.layer):
            return True
        m = self._bmap(iid)
        return bool(m and m.enabled and m.action == "modifier")

    def _undetected(self, iid: str) -> bool:
        return iid in unreliable_inputs(self.layout.family) and iid not in self.seen_ever

    def paintEvent(self, _):
        if not self.profile:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG))
        s, ox, oy = self._transform()
        p.translate(ox, oy)
        p.scale(s, s)

        self._sigs = self.profile.used_signatures()
        self._draw_layer_banner(p)
        p.save()
        p.translate(0, self.cy)
        self._draw_body(p)
        self._draw_parts(p)
        p.restore()
        self._draw_leaders(p)
        for slot in self.slots:
            self._draw_label(p, slot)
        msg_rect = QRectF(300, self.cy + 660, 600, 40)
        if self.learn:
            p.setPen(QColor(theme.WARN))
            p.setFont(self._f_title)
            p.drawText(msg_rect, Qt.AlignCenter,
                       tr("Learn: press a button or move a stick/trigger on the controller…",
                          "MIDI Learn: натисни бутон или мръдни стик/тригер на контролера…"))
        elif not self.snap.get("connected"):
            p.setPen(QColor(theme.MUTED))
            p.setFont(self._f_title)
            p.drawText(msg_rect, Qt.AlignCenter,
                       tr("Controller not connected. Turn it on or plug it in via USB.",
                          "Контролерът не е свързан. Включи го или го свържи по USB."))
        p.end()

    def _draw_layer_banner(self, p: QPainter):
        if not self.layer and not self.profile.layers and not any(
                m.action == "modifier" for m in self.profile.buttons.values()):
            return  # no modifiers: nothing to show
        r = QRectF(330, 70, 540, 34)
        col = QColor(theme.MOD)
        p.setPen(QPen(col if self.layer else QColor(theme.BORDER), 1.4))
        bg = QColor(theme.MOD)
        bg.setAlpha(40 if self.layer else 0)
        p.setBrush(bg)
        p.drawRoundedRect(r, 17, 17)
        p.setFont(self._f_title)
        p.setPen(col if self.layer else QColor(theme.MUTED))
        txt = tr("Layer: {}", "Слой: {}").format(layer_label(self.layer))
        if self.layer_live:
            txt += tr("   ● held on the controller", "   ● задържан на контролера")
        p.drawText(r, Qt.AlignCenter, txt)

    def _draw_body(self, p: QPainter):
        L = self.layout
        visible = set(self.visible_inputs())
        # back paddles first: they sit under the grips
        for iid, c in L.paddles.items():
            if iid in visible:
                self._part(p, iid, QRectF(c.x() - 20, c.y() - 9, 40, 18), radius=9)
        # triggers and bumpers on top (under the body)
        for iid, c in (("lefttrigger", L.lt), ("righttrigger", L.rt)):
            r = QRectF(c.x() - 46, c.y() - 18, 92, 36)
            p.setPen(QPen(QColor(theme.PART_EDGE), 1.5))
            p.setBrush(QColor(theme.PART))
            p.drawRoundedRect(r, 12, 12)
            v = max(0.0, min(1.0, self.snap.get("axes", {}).get(iid, 0.0)))
            if v > 0:
                fill = QRectF(r.x() + 3, r.bottom() - 3 - (r.height() - 6) * v, r.width() - 6, (r.height() - 6) * v)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.ACCENT))
                p.drawRoundedRect(fill, 8, 8)
            self._part_text(p, r, INPUT_LABELS[iid], self._sel(iid))
        for iid, c in (("leftshoulder", L.lb), ("rightshoulder", L.rb)):
            r = QRectF(c.x() - 62, c.y() - 11, 124, 22)
            self._part(p, iid, r, radius=10)
            self._part_text(p, r, INPUT_LABELS[iid], self._sel(iid))
        p.setPen(QPen(QColor(theme.BODY_EDGE), 2))
        p.setBrush(QColor(theme.BODY_FILL))
        p.drawPath(L.body)

    def _sel(self, iid) -> bool:
        return iid in (self.hover, self.selected)

    def _part(self, p: QPainter, iid: str, rect: QRectF, radius=None, ellipse=False, color=None):
        active = self._active(iid)
        undetected = self._undetected(iid)
        edge = QColor(theme.ACCENT) if self._sel(iid) else QColor(
            theme.MOD if self._is_modifier(iid) else theme.PART_EDGE)
        p.setPen(QPen(edge, 2 if self._sel(iid) else 1.5,
                      Qt.DashLine if undetected else Qt.SolidLine))
        fill = QColor(theme.ACCENT) if active else QColor(theme.PART)
        if undetected and not active:
            fill = QColor(theme.BODY_FILL)
        p.setBrush(fill)
        if ellipse:
            p.drawEllipse(rect)
        else:
            p.drawRoundedRect(rect, radius or 6, radius or 6)

    def _part_text(self, p, rect, text, sel, color=None):
        p.setFont(self._f_part)
        p.setPen(QColor(color) if color else QColor(theme.TEXT if sel else theme.MUTED))
        p.drawText(rect, Qt.AlignCenter, text)

    def _draw_parts(self, p: QPainter):
        L = self.layout
        axes = self.snap.get("axes", {})
        if L.touchpad is not None:
            self._draw_touchpad(p, L.touchpad)
        # sticks
        for press_id, xid, yid, c in (("leftstick", "leftx", "lefty", L.lstick),
                                      ("rightstick", "rightx", "righty", L.rstick)):
            sel = any(self._sel(i) for i in (press_id, xid, yid))
            p.setPen(QPen(QColor(theme.ACCENT if sel else theme.PART_EDGE), 2 if sel else 1.5))
            p.setBrush(QColor("#1d2025"))
            p.drawEllipse(c, STICK_R, STICK_R)
            # deadzone circle
            m = self.profile.axes.get(xid)
            dz = (m.deadzone if m else 0.08) * STICK_TRAVEL
            p.setPen(QPen(QColor(theme.BORDER), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, dz + 2, dz + 2)
            pos = QPointF(c.x() + axes.get(xid, 0.0) * STICK_TRAVEL, c.y() - axes.get(yid, 0.0) * STICK_TRAVEL)
            pressed = self.snap.get("buttons", {}).get(press_id)
            p.setPen(QPen(QColor(theme.PART_EDGE), 1.5))
            p.setBrush(QColor(theme.ACCENT_DIM if pressed else theme.PART))
            p.drawEllipse(pos, THUMB_R, THUMB_R)
            moving = self._active(xid) or self._active(yid)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ACCENT if moving else theme.MUTED))
            p.drawEllipse(pos, 5, 5)
        # D-pad
        arm = 19.0
        d = L.dpad
        p.setPen(QPen(QColor(theme.PART_EDGE), 1.5))
        p.setBrush(QColor(theme.PART))
        cross = QPainterPath()
        cross.addRoundedRect(QRectF(d.x() - arm / 2, d.y() - 34, arm, 68), 4, 4)
        cross.addRoundedRect(QRectF(d.x() - 34, d.y() - arm / 2, 68, arm), 4, 4)
        p.drawPath(cross.simplified())
        for iid, c in L.dpad_pos.items():
            r = QRectF(c.x() - arm / 2 + 1, c.y() - arm / 2 + 1, arm - 2, arm - 2)
            if self._active(iid) or self._sel(iid) or self._is_modifier(iid):
                pen_col = theme.ACCENT if self._sel(iid) else theme.MOD
                p.setPen(QPen(QColor(pen_col), 2) if (self._sel(iid) or self._is_modifier(iid)) else Qt.NoPen)
                p.setBrush(QColor(theme.ACCENT) if self._active(iid) else Qt.NoBrush)
                p.drawRoundedRect(r, 3, 3)
        # face buttons
        for iid, c in L.face_pos.items():
            rect = QRectF(c.x() - FACE_R, c.y() - FACE_R, FACE_R * 2, FACE_R * 2)
            self._part(p, iid, rect, ellipse=True)
            if L is PLAYSTATION:
                self._ps_symbol(p, iid, c, theme.BG if self._active(iid) else PS_FACE_COLORS[iid])
            else:
                col = theme.BG if self._active(iid) else theme.FACE_COLORS[iid]
                self._part_text(p, rect, iid.upper(), True, color=col)
        # small buttons
        if L is PLAYSTATION:
            for iid, c in (("back", L.back), ("start", L.start)):
                self._part(p, iid, QRectF(c.x() - 7, c.y() - 12, 14, 24), radius=7)
            self._part(p, "guide", QRectF(L.guide.x() - 14, L.guide.y() - 14, 28, 28), ellipse=True)
            self._part(p, "misc1", QRectF(L.misc1.x() - 13, L.misc1.y() - 5, 26, 10), radius=5)
            if L.gyro is not None and set(GYRO_IDS) & set(self.visible_inputs()):
                self._draw_gyro_icon(p, L.gyro)
        else:
            for iid, c, r in (("back", L.back, 10), ("start", L.start, 10), ("misc1", L.misc1, 9)):
                self._part(p, iid, QRectF(c.x() - r, c.y() - r, r * 2, r * 2), ellipse=True)
            self._part(p, "guide", QRectF(L.guide.x() - 18, L.guide.y() - 18, 36, 36), ellipse=True)
            # small glyphs
            g = L.guide
            p.setPen(QPen(QColor(theme.MUTED), 1.3))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(L.back.x() - 5, L.back.y() - 4, 6, 5))
            p.drawRect(QRectF(L.back.x() - 1, L.back.y() - 1, 6, 5))
            for dy in (-3, 0, 3):
                p.drawLine(QPointF(L.start.x() - 4, L.start.y() + dy), QPointF(L.start.x() + 4, L.start.y() + dy))
            p.drawLine(QPointF(g.x() - 7, g.y() - 7), QPointF(g.x() + 7, g.y() + 7))
            p.drawLine(QPointF(g.x() + 7, g.y() - 7), QPointF(g.x() - 7, g.y() + 7))

    def _ps_symbol(self, p: QPainter, iid: str, c: QPointF, color: str):
        p.setPen(QPen(QColor(color), 2.2))
        p.setBrush(Qt.NoBrush)
        r = 7.0
        if iid == "a":  # cross
            p.drawLine(QPointF(c.x() - r, c.y() - r), QPointF(c.x() + r, c.y() + r))
            p.drawLine(QPointF(c.x() + r, c.y() - r), QPointF(c.x() - r, c.y() + r))
        elif iid == "b":  # circle
            p.drawEllipse(c, r, r)
        elif iid == "x":  # square
            p.drawRect(QRectF(c.x() - r + 1, c.y() - r + 1, 2 * r - 2, 2 * r - 2))
        else:  # triangle
            path = QPainterPath(QPointF(c.x(), c.y() - r))
            path.lineTo(c.x() + r, c.y() + r * 0.7)
            path.lineTo(c.x() - r, c.y() + r * 0.7)
            path.closeSubpath()
            p.drawPath(path)

    def _draw_touchpad(self, p: QPainter, t: QRectF):
        visible = set(self.visible_inputs())
        if not (visible & ({"touchpad"} | set(TOUCH_IDS))):
            return
        # light bar: the colour the controller shows for the current layer
        led = self.snap.get("led")
        if led:
            p.setPen(QPen(QColor(*led), 4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(t.left() - 6, t.top() + 10), QPointF(t.left() - 6, t.bottom() - 10))
            p.drawLine(QPointF(t.right() + 6, t.top() + 10), QPointF(t.right() + 6, t.bottom() - 10))
        pressed = self._active("touchpad")
        sel = self._sel("touchpad") or any(self._sel(i) for i in TOUCH_IDS)
        p.setPen(QPen(QColor(theme.ACCENT if sel else theme.PART_EDGE), 2 if sel else 1.5))
        p.setBrush(QColor(theme.ACCENT_DIM if pressed else theme.PART))
        p.drawRoundedRect(t, 10, 10)
        if self.snap.get("touch_down"):
            ax = self.snap.get("axes", {})
            pos = QPointF(t.left() + ax.get("touchx", 0.5) * t.width(),
                          t.bottom() - ax.get("touchy", 0.5) * t.height())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(pos, 7, 7)

    def _draw_gyro_icon(self, p: QPainter, c: QPointF):
        on = bool(self.snap.get("gyro"))
        sel = any(self._sel(i) for i in GYRO_IDS)
        col = QColor(theme.ACCENT if (on or sel) else theme.MUTED)
        p.setPen(QPen(col, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 13, 6)
        p.drawEllipse(c, 6, 13)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(c, 2.5, 2.5)

    def _draw_leaders(self, p: QPainter):
        anchors = self.layout.anchors()
        for slot in self.slots:
            iid = slot.input_id
            a = anchors.get(iid)
            if a is None:
                continue
            a = QPointF(a.x(), a.y() + self.cy)
            r = slot.rect
            start = QPointF(r.right(), r.center().y()) if slot.left else QPointF(r.left(), r.center().y())
            elbow = QPointF(start.x() + (18 if slot.left else -18), start.y())
            hot = self._sel(iid) or self._active(iid)
            col = QColor(theme.ACCENT if hot else theme.MUTED)
            if not hot:
                col.setAlpha(110)
            p.setPen(QPen(col, 1.8 if hot else 1.1))
            p.drawLine(start, elbow)
            p.drawLine(elbow, a)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(a, 3.2, 3.2)

    def _draw_label(self, p: QPainter, slot: LabelSlot):
        iid = slot.input_id
        r = slot.rect
        prof = self.profile
        is_axis = iid in prof.axes
        held_mod = not is_axis and iid in layer_mods(self.layer)
        if is_axis:
            m = prof.axes[iid]
        else:
            m = self._bmap(iid) or ButtonMapping(enabled=False)
        inherited = not is_axis and prof.inherited_modifier(self.layer, iid)
        is_mod = not is_axis and (held_mod or (m.enabled and m.action == "modifier"))
        sel = self._sel(iid)
        active = self._active(iid)
        undetected = self._undetected(iid)
        sig = None if is_axis else midi_signature(m)
        duplicate = bool(sig and self._sigs.get(sig, 0) > 1)

        edge = theme.ACCENT if sel else (theme.MOD if is_mod else (theme.ACCENT_DIM if active else theme.BORDER))
        p.setPen(QPen(QColor(edge), 1.6 if sel or is_mod else 1))
        p.setBrush(QColor(theme.PANEL_2 if sel else theme.PANEL))
        p.drawRoundedRect(r, 8, 8)

        dim = (not m.enabled and not held_mod) or undetected
        title = INPUT_LABELS[iid]
        if not is_axis and m.label:
            title += f" · {m.label}"
        p.setFont(self._f_title)
        p.setPen(QColor(theme.MUTED if dim else theme.TEXT))
        trect = QRectF(r.x() + 10, r.y() + 4, r.width() - 20, 20)
        p.drawText(trect, Qt.AlignLeft | Qt.AlignVCenter, title)

        # right side of the first row: value / status
        p.setFont(self._f_small)
        if undetected:
            p.setPen(QColor(theme.WARN))
            p.drawText(trect, Qt.AlignRight | Qt.AlignVCenter, tr("not detected", "не е открит"))
        elif held_mod:
            p.setPen(QColor(theme.MOD))
            p.drawText(trect, Qt.AlignRight | Qt.AlignVCenter, tr("held", "задържан"))
        elif duplicate:
            p.setPen(QColor(theme.WARN))
            p.drawText(trect, Qt.AlignRight | Qt.AlignVCenter, tr("duplicate", "дублиран"))
        elif is_axis and m.enabled:
            raw = self.axis_raw_out.get(iid)
            p.setPen(QColor(theme.ACCENT if active else theme.MUTED))
            p.drawText(trect, Qt.AlignRight | Qt.AlignVCenter, "—" if raw is None else str(raw))
        elif not is_axis and m.enabled:
            toggled = self.snap.get("toggles", {}).get(f"{self.layer}|{iid}")
            on = active or (toggled and m.mode == "toggle")
            p.setPen(Qt.NoPen)
            p.setBrush(QColor((theme.MOD if is_mod else theme.ACCENT) if on else theme.BORDER))
            p.drawEllipse(QPointF(r.right() - 16, r.y() + 14), 5, 5)

        if is_axis:
            desc = describe_axis(m) + (tr(" · shared", " · общ") if self.layer else "")
            if iid in GYRO_IDS and m.enabled:
                desc += tr(" · needs Gyro button", " · с бутон Жиро")
        elif held_mod:
            desc = tr("Modifier of this layer", "Модификатор на този слой")
        elif inherited:
            desc = tr("Modifier (inherited)", "Модификатор (наследен)")
        elif self.layer and not m.enabled:
            desc = tr("not set in this layer", "не е зададен в този слой")
        else:
            desc = describe_button(m)
        p.setPen(QColor(theme.MUTED if dim else "#b8c0ca"))
        p.drawText(QRectF(r.x() + 10, r.y() + 23, r.width() - 20, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(desc, Qt.ElideRight, int(r.width() - 20)))

        if is_axis and m.enabled:
            v = max(0.0, min(1.0, self.axis_values.get(iid, 0.0)))
            bar = QRectF(r.x() + 10, r.bottom() - 7, r.width() - 20, 3)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BORDER))
            p.drawRoundedRect(bar, 1.5, 1.5)
            p.setBrush(QColor(theme.ACCENT))
            if m.mode != "absolute" or iid not in TRIGGER_IDS:
                # position marker (center = 64)
                x = bar.x() + bar.width() * v
                p.drawRoundedRect(QRectF(x - 3, bar.y() - 2, 6, 7), 2, 2)
            else:
                p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * v, bar.height()), 1.5, 1.5)
