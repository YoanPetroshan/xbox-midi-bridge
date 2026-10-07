"""Generates assets/icon.icns (original artwork): python assets/make_icon.py"""
import os
import subprocess
import sys
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

HERE = os.path.dirname(os.path.abspath(__file__))


def draw(size=1024) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 1024, size / 1024)
    # background: macOS-style rounded square
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(100, 100, 824, 824), 185, 185)
    p.fillPath(bg, QColor("#14171c"))
    p.setPen(QPen(QColor("#2a6f8f"), 6))
    p.drawPath(bg)
    # gamepad
    body = QPainterPath(QPointF(330, 380))
    body.cubicTo(420, 350, 604, 350, 694, 380)
    body.cubicTo(760, 400, 790, 470, 805, 560)
    body.cubicTo(820, 650, 800, 700, 760, 700)
    body.cubicTo(715, 700, 690, 640, 640, 615)
    body.cubicTo(560, 590, 464, 590, 384, 615)
    body.cubicTo(334, 640, 309, 700, 264, 700)
    body.cubicTo(224, 700, 204, 650, 219, 560)
    body.cubicTo(234, 470, 264, 400, 330, 380)
    body.closeSubpath()
    p.setPen(QPen(QColor("#4a515c"), 8))
    p.setBrush(QColor("#2b3038"))
    p.drawPath(body)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#4fc3f7"))
    p.drawEllipse(QPointF(360, 470), 52, 52)
    p.drawEllipse(QPointF(590, 560), 44, 44)
    p.setBrush(QColor("#3b414b"))
    p.drawRoundedRect(QRectF(420, 528, 30, 84), 6, 6)
    p.drawRoundedRect(QRectF(393, 555, 84, 30), 6, 6)
    for (x, y), c in zip(((680, 430), (640, 470), (720, 470), (680, 510)),
                         ("#e6c35a", "#5aa0e6", "#e0605e", "#6cc070")):
        p.setBrush(QColor(c))
        p.drawEllipse(QPointF(x, y), 22, 22)
    # MIDI wave on top
    p.setPen(QPen(QColor("#4fc3f7"), 22, Qt.SolidLine, Qt.RoundCap))
    p.setBrush(Qt.NoBrush)
    wave = QPainterPath(QPointF(330, 255))
    wave.cubicTo(400, 185, 450, 325, 512, 255)
    wave.cubicTo(574, 185, 624, 325, 694, 255)
    p.drawPath(wave)
    p.end()
    return img


def main():
    QGuiApplication(sys.argv)
    base = draw()
    with tempfile.TemporaryDirectory() as d:
        iconset = os.path.join(d, "icon.iconset")
        os.mkdir(iconset)
        for s in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = s * scale
                name = f"icon_{s}x{s}{'@2x' if scale == 2 else ''}.png"
                base.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(os.path.join(iconset, name))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(HERE, "icon.icns")], check=True)
    base.save(os.path.join(HERE, "icon.png"))
    print("assets/icon.icns written")


if __name__ == "__main__":
    main()
