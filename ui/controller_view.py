"""Controller diagram with leader lines to labels showing the current mapping.

Everything is painted with QPainter in 1200×720 logical coordinates and scaled.
The drawing is original (simple shapes), not a copy of protected artwork.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from mapping import (INPUT_LABELS, TRIGGER_IDS, UNRELIABLE_INPUTS, ButtonMapping, Profile,
                     describe_axis, describe_button, layer_label, layer_mods, midi_signature)
from i18n import tr
from ui import theme

W, H = 1200.0, 720.0
LABEL_W, LABEL_H = 262.0, 52.0
LEFT_X, RIGHT_X = 16.0, W - 16.0 - LABEL_W
TOP_Y, STEP_Y = 22.0, 62.0

# Part geometry
LT_C, RT_C = QPointF(445, 186), QPointF(755, 186)
LB_C, RB_C = QPointF(445, 228), QPointF(755, 228)
LSTICK, RSTICK = QPointF(450, 322), QPointF(668, 418)
STICK_R, THUMB_R, STICK_TRAVEL = 40.0, 22.0, 18.0
DPAD = QPointF(532, 418)
FACE = QPointF(752, 322)
FACE_OFF, FACE_R = 38.0, 17.0
VIEW_C, MENU_C = QPointF(560, 320), QPointF(640, 320)
GUIDE_C, SHARE_C = QPointF(600, 272), QPointF(600, 356)

FACE_POS = {
    "y": QPointF(FACE.x(), FACE.y() - FACE_OFF),
    "a": QPointF(FACE.x(), FACE.y() + FACE_OFF),
    "x": QPointF(FACE.x() - FACE_OFF, FACE.y()),
    "b": QPointF(FACE.x() + FACE_OFF, FACE.y()),
}
DPAD_POS = {
    "dpup": QPointF(DPAD.x(), DPAD.y() - 22),
    "dpdown": QPointF(DPAD.x(), DPAD.y() + 22),
    "dpleft": QPointF(DPAD.x() - 22, DPAD.y()),
    "dpright": QPointF(DPAD.x() + 22, DPAD.y()),
}

# Leader-line anchors and label order (top to bottom).
ANCHORS = {
    "lefttrigger": QPointF(LT_C.x() - 30, LT_C.y()),
    "leftshoulder": QPointF(LB_C.x() - 40, LB_C.y()),
    "lefty": QPointF(LSTICK.x(), LSTICK.y() - STICK_R),
    "leftx": QPointF(LSTICK.x() - STICK_R, LSTICK.y()),
    "leftstick": QPointF(LSTICK.x() - 20, LSTICK.y() + 34),
    "back": VIEW_C,
    "misc1": SHARE_C,
    "dpup": DPAD_POS["dpup"],
    "dpleft": DPAD_POS["dpleft"],
    "dpright": DPAD_POS["dpright"],
    "dpdown": DPAD_POS["dpdown"],
    "righttrigger": QPointF(RT_C.x() + 30, RT_C.y()),
    "rightshoulder": QPointF(RB_C.x() + 40, RB_C.y()),
    "guide": GUIDE_C,
    "y": FACE_POS["y"],
    "start": MENU_C,
    "b": FACE_POS["b"],
    "x": FACE_POS["x"],
    "a": FACE_POS["a"],
    "righty": QPointF(RSTICK.x(), RSTICK.y() - STICK_R),
    "rightx": QPointF(RSTICK.x() + STICK_R, RSTICK.y()),
    "rightstick": QPointF(RSTICK.x() + 20, RSTICK.y() + 34),
}
LEFT_ORDER = ["lefttrigger", "leftshoulder", "lefty", "leftx", "leftstick", "back", "misc1",
              "dpup", "dpleft", "dpright", "dpdown"]
RIGHT_ORDER = ["righttrigger", "rightshoulder", "guide", "y", "start", "x", "b", "a",
               "righty", "rightx", "rightstick"]


@dataclass
class LabelSlot:
    input_id: str
    rect: QRectF
    left: bool


def _body_path() -> QPainterPath:
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
        self.slots: list[LabelSlot] = []
        for i, iid in enumerate(LEFT_ORDER):
            self.slots.append(LabelSlot(iid, QRectF(LEFT_X, TOP_Y + i * STEP_Y, LABEL_W, LABEL_H), True))
        for i, iid in enumerate(RIGHT_ORDER):
            self.slots.append(LabelSlot(iid, QRectF(RIGHT_X, TOP_Y + i * STEP_Y, LABEL_W, LABEL_H), False))
        self._body = _body_path()
        self._f_title = QFont()
        self._f_title.setPixelSize(14)
        self._f_title.setBold(True)
        self._f_small = QFont()
        self._f_small.setPixelSize(12)
        self._f_part = QFont()
        self._f_part.setPixelSize(13)
        self._f_part.setBold(True)

    # ---------------------------------------------------------------- data
    def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        self.update()

    def set_state(self, snap: dict, axis_values: dict[str, float], axis_raw_out: dict[str, int]) -> None:
        self.snap = snap
        self.axis_values = axis_values
        self.axis_raw_out = axis_raw_out
        self.update()

    # ---------------------------------------------------------------- geometry
    def _transform(self):
        s = min(self.width() / W, self.height() / H)
        ox = (self.width() - W * s) / 2
        oy = (self.height() - H * s) / 2
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
        best, best_d = None, 22.0 ** 2
        for iid, a in self._part_centers().items():
            d = (a.x() - p.x()) ** 2 + (a.y() - p.y()) ** 2
            if d < best_d:
                best, best_d = iid, d
        return best

    def _part_centers(self) -> dict[str, QPointF]:
        c = dict(FACE_POS)
        c.update(DPAD_POS)
        c.update({
            "lefttrigger": LT_C, "righttrigger": RT_C, "leftshoulder": LB_C, "rightshoulder": RB_C,
            "back": VIEW_C, "start": MENU_C, "guide": GUIDE_C, "misc1": SHARE_C,
            "leftstick": LSTICK, "rightstick": RSTICK,
        })
        return c

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
        v = self.snap.get("axes", {}).get(iid, 0.0)
        m = self.profile.axes.get(iid) if self.profile else None
        dz = m.deadzone if m else 0.08
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
        return iid in UNRELIABLE_INPUTS and iid not in self.seen_ever

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
        self._draw_body(p)
        self._draw_parts(p)
        self._draw_leaders(p)
        for slot in self.slots:
            self._draw_label(p, slot)
        if self.learn:
            p.setPen(QColor(theme.WARN))
            p.setFont(self._f_title)
            p.drawText(QRectF(300, 660, 600, 40), Qt.AlignCenter,
                       tr("Learn: press a button or move a stick/trigger on the controller…",
                          "MIDI Learn: натисни бутон или мръдни стик/тригер на контролера…"))
        elif not self.snap.get("connected"):
            p.setPen(QColor(theme.MUTED))
            p.setFont(self._f_title)
            p.drawText(QRectF(300, 660, 600, 40), Qt.AlignCenter,
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
        # triggers and bumpers on top (under the body)
        for iid, c in (("lefttrigger", LT_C), ("righttrigger", RT_C)):
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
            self._part_text(p, r, "LT" if iid == "lefttrigger" else "RT", self._sel(iid))
        for iid, c in (("leftshoulder", LB_C), ("rightshoulder", RB_C)):
            r = QRectF(c.x() - 62, c.y() - 11, 124, 22)
            self._part(p, iid, r, radius=10)
            self._part_text(p, r, "LB" if iid == "leftshoulder" else "RB", self._sel(iid))
        p.setPen(QPen(QColor(theme.BODY_EDGE), 2))
        p.setBrush(QColor(theme.BODY_FILL))
        p.drawPath(self._body)

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
        axes = self.snap.get("axes", {})
        # sticks
        for press_id, xid, yid, c in (("leftstick", "leftx", "lefty", LSTICK),
                                      ("rightstick", "rightx", "righty", RSTICK)):
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
        p.setPen(QPen(QColor(theme.PART_EDGE), 1.5))
        p.setBrush(QColor(theme.PART))
        cross = QPainterPath()
        cross.addRoundedRect(QRectF(DPAD.x() - arm / 2, DPAD.y() - 34, arm, 68), 4, 4)
        cross.addRoundedRect(QRectF(DPAD.x() - 34, DPAD.y() - arm / 2, 68, arm), 4, 4)
        p.drawPath(cross.simplified())
        for iid, c in DPAD_POS.items():
            r = QRectF(c.x() - arm / 2 + 1, c.y() - arm / 2 + 1, arm - 2, arm - 2)
            if self._active(iid) or self._sel(iid):
                p.setPen(Qt.NoPen if not self._sel(iid) else QPen(QColor(theme.ACCENT), 2))
                p.setBrush(QColor(theme.ACCENT) if self._active(iid) else Qt.NoBrush)
                p.drawRoundedRect(r, 3, 3)
        # ABXY
        for iid, c in FACE_POS.items():
            self._part(p, iid, QRectF(c.x() - FACE_R, c.y() - FACE_R, FACE_R * 2, FACE_R * 2), ellipse=True)
            col = theme.BG if self._active(iid) else theme.FACE_COLORS[iid]
            self._part_text(p, QRectF(c.x() - FACE_R, c.y() - FACE_R, FACE_R * 2, FACE_R * 2),
                            iid.upper(), True, color=col)
        # View / Menu / Share / Xbox
        for iid, c, r in (("back", VIEW_C, 10), ("start", MENU_C, 10), ("misc1", SHARE_C, 9)):
            self._part(p, iid, QRectF(c.x() - r, c.y() - r, r * 2, r * 2), ellipse=True)
        self._part(p, "guide", QRectF(GUIDE_C.x() - 18, GUIDE_C.y() - 18, 36, 36), ellipse=True)
        # small glyphs
        p.setPen(QPen(QColor(theme.MUTED), 1.3))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(VIEW_C.x() - 5, VIEW_C.y() - 4, 6, 5))
        p.drawRect(QRectF(VIEW_C.x() - 1, VIEW_C.y() - 1, 6, 5))
        for dy in (-3, 0, 3):
            p.drawLine(QPointF(MENU_C.x() - 4, MENU_C.y() + dy), QPointF(MENU_C.x() + 4, MENU_C.y() + dy))
        p.drawLine(QPointF(GUIDE_C.x() - 7, GUIDE_C.y() - 7), QPointF(GUIDE_C.x() + 7, GUIDE_C.y() + 7))
        p.drawLine(QPointF(GUIDE_C.x() + 7, GUIDE_C.y() - 7), QPointF(GUIDE_C.x() - 7, GUIDE_C.y() + 7))

    def _draw_leaders(self, p: QPainter):
        for slot in self.slots:
            iid = slot.input_id
            a = ANCHORS[iid]
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
        trect = QRectF(r.x() + 10, r.y() + 5, r.width() - 20, 20)
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
            p.drawEllipse(QPointF(r.right() - 16, r.y() + 15), 5, 5)

        if is_axis:
            desc = describe_axis(m) + (tr(" · shared", " · общ") if self.layer else "")
        elif held_mod:
            desc = tr("Modifier of this layer", "Модификатор на този слой")
        elif inherited:
            desc = tr("Modifier (inherited)", "Модификатор (наследен)")
        elif self.layer and not m.enabled:
            desc = tr("not set in this layer", "не е зададен в този слой")
        else:
            desc = describe_button(m)
        p.setPen(QColor(theme.MUTED if dim else "#b8c0ca"))
        p.drawText(QRectF(r.x() + 10, r.y() + 25, r.width() - 20, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(desc, Qt.ElideRight, int(r.width() - 20)))

        if is_axis and m.enabled:
            v = max(0.0, min(1.0, self.axis_values.get(iid, 0.0)))
            bar = QRectF(r.x() + 10, r.bottom() - 7, r.width() - 20, 3)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BORDER))
            p.drawRoundedRect(bar, 1.5, 1.5)
            p.setBrush(QColor(theme.ACCENT))
            if m.mode == "rate" or iid not in TRIGGER_IDS:
                # position marker (center = 64)
                x = bar.x() + bar.width() * v
                p.drawRoundedRect(QRectF(x - 3, bar.y() - 2, 6, 7), 2, 2)
            else:
                p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * v, bar.height()), 1.5, 1.5)
