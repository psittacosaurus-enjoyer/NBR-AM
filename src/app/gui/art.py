"""Original, resolution-independent pixel artwork for the manager's Qt interface."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget


NAVY = "#081d65"
CREAM = "#f5f0d7"


def _polygon(painter: QPainter, points, fill: str, outline: str = NAVY):
    painter.setPen(QPen(QColor(outline), 1))
    painter.setBrush(QColor(fill))
    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))


def _rect(painter: QPainter, x, y, width, height, color: str):
    painter.fillRect(QRectF(x, y, width, height), QColor(color))


def _globe(painter: QPainter, bounds: QRectF):
    """Paint a shaded globe on a deliberately coarse 24 by 24 pixel grid."""
    painter.save()
    painter.translate(bounds.x(), bounds.y())
    painter.scale(bounds.width() / 24, bounds.height() / 24)
    land = QPainterPath()
    for contour in (
        [(4, 5), (7, 3), (12, 4), (11, 7), (8, 8), (9, 11), (7, 12), (4, 9)],
        [(8, 12), (11, 11), (13, 14), (11, 18), (9, 21), (8, 17)],
        [(14, 4), (20, 5), (22, 9), (19, 10), (18, 13), (15, 11), (12, 9)],
        [(14, 11), (18, 12), (18, 16), (15, 19), (13, 15)],
        [(19, 17), (22, 18), (21, 20), (18, 20)],
    ):
        land.addPolygon(QPolygonF([QPointF(x, y) for x, y in contour]))
    for y in range(24):
        for x in range(24):
            radius = math.hypot(x - 11.5, y - 11.5)
            if radius > 11.4:
                continue
            if radius > 10.3:
                color = QColor(NAVY)
            else:
                light = max(0, min(1, (23 - x - y) / 28 + .43))
                if land.contains(QPointF(x + .5, y + .5)):
                    color = QColor(int(65 + light * 112), int(120 + light * 74), int(65 + light * 21))
                else:
                    color = QColor(int(17 + light * 40), int(64 + light * 92), int(126 + light * 77))
                if y == 2 and 8 <= x <= 13:
                    color = QColor("#d0e7d0")
                elif x == 3 and 7 <= y <= 12:
                    color = color.lighter(120)
            painter.fillRect(QRectF(x, y, 1, 1), color)
    painter.restore()


def _paint_icon(painter: QPainter, name: str):
    """All coordinates are on the same small, sharp 16-pixel canvas."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(QColor(NAVY), 1))
    if name == "globe":
        _globe(painter, QRectF(0, 0, 16, 16))
    elif name == "dashboard":
        _polygon(painter, [(1, 7), (8, 1), (15, 7), (13, 9), (8, 5), (3, 9)], "#f8edb5")
        _polygon(painter, [(3, 7), (8, 3), (13, 7), (13, 15), (3, 15)], "#faf8e9")
        _rect(painter, 7, 10, 3, 5, "#2271bf")
        _rect(painter, 4, 8, 2, 3, "#9cd8f1")
        _rect(painter, 11, 8, 1, 3, "#9cd8f1")
    elif name == "account":
        painter.setBrush(QColor("#268dd3"))
        painter.drawEllipse(QRectF(5, 1, 6, 6))
        _polygon(painter, [(5, 8), (11, 8), (14, 12), (14, 15), (2, 15), (2, 12)], "#1680c5")
        _rect(painter, 6, 2, 3, 2, "#a3e4f7")
        _rect(painter, 4, 10, 2, 3, "#72c6ec")
    elif name == "farming":
        _polygon(painter, [(1, 4), (2, 2), (7, 2), (9, 4), (15, 4), (15, 14), (1, 14)], "#ecc34d")
        _rect(painter, 2, 5, 12, 8, "#f7dc71")
        _rect(painter, 2, 5, 12, 1, "#fff5c8")
        _rect(painter, 2, 6, 1, 7, "#fff1a9")
    elif name == "log":
        _polygon(painter, [(3, 1), (10, 1), (13, 4), (13, 15), (3, 15)], "#fffdee")
        _polygon(painter, [(10, 1), (10, 4), (13, 4)], "#b6cae6")
        for y, width in ((4, 4), (7, 7), (10, 7), (13, 6)):
            _rect(painter, 5, y, width, 1, "#245bbb")
    elif name == "settings":
        _polygon(painter, [(6, 1), (9, 1), (10, 4), (12, 3), (14, 5), (12, 7),
                           (15, 8), (15, 11), (12, 11), (12, 14), (9, 15), (8, 12),
                           (6, 14), (3, 13), (4, 10), (1, 9), (1, 6), (4, 6), (4, 3), (6, 4)], "#82a6bd")
        painter.setBrush(QColor("#f3eedb"))
        painter.drawEllipse(QRectF(5, 5, 6, 6))
        painter.setBrush(QColor("#3a74a0"))
        painter.drawEllipse(QRectF(7, 7, 2, 2))
    elif name == "clock":
        painter.setBrush(QColor("#91b9d5"))
        painter.drawEllipse(QRectF(1, 1, 14, 14))
        painter.setBrush(QColor("#fff9df"))
        painter.drawEllipse(QRectF(3, 3, 10, 10))
        painter.drawLine(8, 4, 8, 8)
        painter.drawLine(8, 8, 11, 9)
        _rect(painter, 7, 2, 2, 1, NAVY)
    elif name == "hourglass":
        _polygon(painter, [(4, 2), (12, 2), (11, 6), (9, 8), (11, 10), (12, 14), (4, 14), (5, 10), (7, 8), (5, 6)], "#d8ecf3")
        _polygon(painter, [(5, 4), (11, 4), (8, 7)], "#e3b846")
        _polygon(painter, [(5, 13), (8, 9), (11, 13)], "#edc34f")
        _rect(painter, 3, 1, 10, 2, "#245fc0")
        _rect(painter, 3, 14, 10, 2, "#245fc0")
    elif name == "loot":
        _polygon(painter, [(2, 6), (14, 6), (14, 14), (2, 14)], "#98551c")
        _polygon(painter, [(2, 6), (4, 2), (12, 2), (14, 6), (14, 8), (2, 8)], "#b97720")
        _rect(painter, 4, 3, 2, 11, "#f2c34f")
        _rect(painter, 11, 3, 2, 11, "#e9b342")
        _rect(painter, 2, 7, 12, 1, NAVY)
        _rect(painter, 7, 7, 3, 4, "#ffd954")
        _rect(painter, 8, 8, 1, 2, NAVY)
    elif name == "start":
        _polygon(painter, [(4, 1), (14, 8), (4, 15)], "#24a52d", "#0b3c17")
        _rect(painter, 5, 3, 1, 10, "#7cda4c")
    elif name == "stop":
        _polygon(painter, [(2, 2), (14, 2), (14, 14), (2, 14)], "#d42a21", "#581b1b")
        _rect(painter, 3, 3, 10, 1, "#f87758")
        _rect(painter, 3, 4, 1, 9, "#f24932")
    elif name == "restart":
        painter.setPen(QPen(QColor("#624512"), 3))
        painter.drawArc(QRectF(2, 2, 12, 12), 30 * 16, 295 * 16)
        painter.setPen(QPen(QColor("#efbf42"), 1))
        painter.drawArc(QRectF(2, 2, 12, 12), 30 * 16, 295 * 16)
        _polygon(painter, [(1, 3), (2, 9), (7, 6)], "#f2c548", "#624512")
    else:
        raise ValueError(f"Unknown manager icon: {name}")


def pixel_icon(name: str, size: int = 20) -> QIcon:
    """Return local artwork for a named navigation, section, or control icon."""
    size = max(8, int(size))
    icon = QIcon()
    for resolution in (size, size * 2):
        pixmap = QPixmap(resolution, resolution)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.scale(resolution / 16, resolution / 16)
        try:
            _paint_icon(painter, name)
        finally:
            painter.end()
        icon.addPixmap(pixmap)
    return icon


# A compact original alphabet gives the title its pixel geometry on every OS.
_GLYPHS = {
    "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
    "B": ("11110", "11001", "11001", "11110", "11001", "11001", "11110"),
    "R": ("11110", "11001", "11001", "11110", "11100", "11010", "11001"),
    "A": ("01110", "11011", "11011", "11111", "11011", "11011", "11011"),
    "M": ("10001", "11011", "11111", "11111", "11011", "11011", "11011"),
    "S": ("01111", "11000", "11000", "01110", "00011", "00011", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "P": ("11110", "11001", "11001", "11110", "11000", "11000", "11000"),
    "J": ("00111", "00011", "00011", "00011", "11011", "11011", "01110"),
    "a": ("00000", "00000", "01110", "00011", "01111", "11011", "01111"),
    "u": ("00000", "00000", "11011", "11011", "11011", "11011", "01111"),
    "t": ("00100", "01100", "11111", "01100", "01100", "01101", "00110"),
    "o": ("00000", "00000", "01110", "11011", "11011", "11011", "01110"),
    "m": ("00000", "00000", "11010", "11111", "11011", "11011", "11011"),
    "i": ("01100", "00000", "11100", "01100", "01100", "01100", "11110"),
    "n": ("00000", "00000", "11110", "11011", "11011", "11011", "11011"),
    "g": ("00000", "01111", "11011", "11011", "01111", "00011", "11110"),
    "e": ("00000", "00000", "01110", "11011", "11110", "11000", "01111"),
    "r": ("00000", "00000", "11011", "11100", "11000", "11000", "11000"),
    "p": ("00000", "11110", "11011", "11011", "11110", "11000", "11000"),
    "l": ("11100", "01100", "01100", "01100", "01100", "01100", "11110"),
    "s": ("00000", "00000", "01111", "11000", "01110", "00011", "11110"),
    "d": ("00011", "00011", "01111", "11011", "11011", "11011", "01111"),
    "y": ("00000", "11011", "11011", "11011", "01111", "00011", "11110"),
    "h": ("11000", "11000", "11110", "11011", "11011", "11011", "11011"),
}


def _text_width(text: str) -> int:
    return sum(4 if character == " " else 6 for character in text) - 1


def _pixel_text(painter: QPainter, text: str, x: float, y: float, scale: float):
    for character in text:
        if character != " ":
            for row, bits in enumerate(_GLYPHS[character]):
                for column, bit in enumerate(bits):
                    if bit == "1":
                        _rect(painter, x + column * scale, y + row * scale, scale, scale, NAVY)
        x += (4 if character == " " else 6) * scale


class RetroBanner(QWidget):
    """Cream globe masthead whose title always fits its available width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("retroBanner")
        self.setAccessibleName("NBR Automation Manager")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(74)

    def sizeHint(self):
        return QSize(880, 82)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(CREAM))
        width, height = self.width(), self.height()
        # Fine, regular stippling suggests old paper without a texture asset.
        for y in range(5, height - 3, 6):
            for x in range(5 + (y % 2) * 2, width - 3, 9):
                _rect(painter, x, y, 1, 1, "#e5e0c9")
        painter.setPen(QPen(QColor("#72889b"), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setPen(QPen(QColor("#fffdf0"), 1))
        painter.drawRect(self.rect().adjusted(2, 2, -3, -3))
        globe_size = min(64, height - 14, max(28, width * .10))
        _globe(painter, QRectF(12, (height - globe_size) / 2, globe_size, globe_size))
        title = "NBR Automation Manager"
        title_x = 24 + globe_size
        right_space = 68 if width >= 600 else 18
        checker_size = 7 if width >= 600 else 3
        checker_x = width - (checker_size * 6 + 12)
        checker_y = (height - 42) / 2 if width >= 600 else 6
        for row in range(6):
            for column in range(6):
                if (row + column) % 2 == 0:
                    _rect(painter, checker_x + column * checker_size, checker_y + row * checker_size,
                          checker_size, checker_size, "#cccbb8")
        available = max(1, width - title_x - right_space)
        scale = min(5.0, (height - 24) / 7, available / _text_width(title))
        _pixel_text(painter, title, title_x, (height - 7 * scale) / 2, scale)
        painter.end()
