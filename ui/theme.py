"""Dark theme (for dimly lit venues)."""
from PySide6.QtGui import QColor, QPalette

BG = "#111316"
PANEL = "#1a1d22"
PANEL_2 = "#22262c"
BORDER = "#2f343c"
TEXT = "#e4e7eb"
MUTED = "#8a929d"
ACCENT = "#4fc3f7"
ACCENT_DIM = "#2a6f8f"
WARN = "#f0a840"
OK = "#6cc070"
ERR = "#e0605e"
MOD = "#b48ef0"  # modifiers / layers

BODY_FILL = "#262a31"
BODY_EDGE = "#3c424c"
PART = "#363b44"
PART_EDGE = "#4a515c"

FACE_COLORS = {"a": "#6cc070", "b": "#e0605e", "x": "#5aa0e6", "y": "#e6c35a"}

STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QMainWindow::separator {{ background: {BORDER}; width: 1px; }}
QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER}; spacing: 6px; padding: 6px; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar QLabel {{ background: transparent; padding: 0 10px; }}
QPushButton, QToolButton {{
  background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 10px; color: {TEXT};
}}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT_DIM}; }}
QPushButton:checked, QToolButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
  background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px; min-height: 18px;
  selection-background-color: {ACCENT_DIM};
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {PANEL_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {PANEL}; top: -1px; }}
QTabBar::tab {{ background: {BG}; color: {MUTED}; padding: 7px 12px; border: 1px solid transparent; border-bottom: none; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {TEXT}; border-color: {BORDER}; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QPlainTextEdit {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 6px;
  font-family: Menlo, monospace; font-size: 12px; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL_2}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT_DIM}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER}; border-radius: 4px; background: {PANEL_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#h {{ font-weight: 600; font-size: 14px; }}
QFrame#popover {{ background: {PANEL}; border: 1px solid {ACCENT_DIM}; border-radius: 10px; }}
QFrame#popover QWidget {{ background: {PANEL}; }}
QFrame#popover QComboBox, QFrame#popover QSpinBox, QFrame#popover QDoubleSpinBox, QFrame#popover QLineEdit {{ background: {PANEL_2}; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""


def apply(app) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, PANEL_2),
        (QPalette.AlternateBase, PANEL), (QPalette.Text, TEXT), (QPalette.Button, PANEL_2),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT_DIM), (QPalette.HighlightedText, TEXT),
        (QPalette.ToolTipBase, PANEL_2), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, MUTED),
    ]:
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
