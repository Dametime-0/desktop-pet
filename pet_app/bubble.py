# -*- coding: utf-8 -*-
"""文字气泡窗口：悬浮于宠物头顶（不遮挡主体），逐字显示、自动消失。

- 独立顶层窗口 + WA_TransparentForMouseEvents，气泡完全不拦截鼠标；
- 样式（颜色/字体/圆角/透明度/速度等）全部由 config/settings.json 的 bubble 节控制；
- 宠物移动/缩放时通过 follow() 跟随。
"""
from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, Qt, QTimer)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPainterPath)
from PySide6.QtWidgets import QLabel, QWidget

TAIL = 10          # 气泡尾巴高度
MARGIN = 10        # 与宠物窗口的间距


class BubbleWindow(QWidget):
    def __init__(self, style: dict, topmost: bool = True):
        super().__init__()
        self._style = style
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 气泡不接收任何鼠标事件，点击会穿透到桌面/宠物
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._label.setStyleSheet("background: transparent; border: none;")

        self._full_text = ""
        self._shown = 0
        self._flipped = False        # True=气泡显示在宠物下方（贴近屏幕顶部时翻转）
        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._tick)
        self._fade = None

        self.set_topmost(topmost)
        self._apply_style()

    # ---------- 样式 ----------
    def _apply_style(self):
        s = self._style
        font = QFont(s.get("font_family", "Microsoft YaHei"), int(s.get("font_size", 14)))
        self._label.setFont(font)
        c = QColor(s.get("text_color", "#4A4038"))
        self._label.setStyleSheet(f"color: {c.name()}; background: transparent; border: none;")

    @property
    def _pad(self):
        return max(10, int(self._style.get("font_size", 14)) * 0.9)

    def set_topmost(self, on: bool):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if self.isVisible():
            self.show()

    # ---------- 显示控制 ----------
    def show_text(self, text: str):
        """显示新文本（打断当前气泡），逐字打出。"""
        self._full_text = text or ""
        self._shown = 0
        self._typing_timer.stop()
        if self._fade is not None:
            self._fade.stop()
            self._fade = None
        self.setWindowOpacity(1.0)
        cps = max(4, float(self._style.get("chars_per_second", 20)))
        interval = 60
        self._chars_per_tick = max(1, round(cps * interval / 1000))
        if self._full_text:
            self._label.setText("")
            self._adjust_size()
            self.show()
            self.raise_()
            self._typing_timer.start(interval)

    def _tick(self):
        """逐字显示，完成后安排自动消失。"""
        self._shown = min(len(self._full_text), self._shown + self._chars_per_tick)
        self._label.setText(self._full_text[:self._shown])
        self._adjust_size()
        if self._shown >= len(self._full_text):
            self._typing_timer.stop()
            per_char = float(self._style.get("duration_per_char_ms", 95))
            dur = max(int(self._style.get("min_duration_ms", 2600)),
                      int(len(self._full_text) * per_char))
            QTimer.singleShot(dur, self._fade_out)

    def _fade_out(self):
        """淡出后隐藏。"""
        if not self.isVisible():
            return
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(260)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InQuad)
        self._fade.finished.connect(self._do_hide)
        self._fade.start()

    def _do_hide(self):
        self.hide()
        self.setWindowOpacity(1.0)
        self._fade = None

    def hide_now(self):
        self._typing_timer.stop()
        if self._fade is not None:
            self._fade.stop()
            self._fade = None
        self.hide()
        self.setWindowOpacity(1.0)

    # ---------- 布局与绘制 ----------
    def _adjust_size(self):
        """按当前已显示文本计算气泡尺寸。"""
        fm = QFontMetrics(self._label.font())
        max_w = int(self._style.get("max_width", 280))
        rect = fm.boundingRect(0, 0, max_w, 10000,
                               Qt.TextFlag.TextWordWrap,
                               self._label.text() or " ")
        pad = self._pad
        w = max(40, min(max_w, rect.width() + 4) + pad * 2)
        h = rect.height() + pad * 2
        top = TAIL if self._flipped else 0          # 翻转时尾巴在上方
        self.setFixedSize(w, h + TAIL)
        self._label.setGeometry(pad, top + pad - 2, w - pad * 2, h)

    def paintEvent(self, event):
        """圆角矩形气泡 + 指向宠物的小尾巴（翻转时尾巴朝上）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._style
        radius = float(s.get("corner_radius", 16))
        opacity = max(0.1, min(1.0, float(s.get("opacity", 0.94))))

        bg = QColor(s.get("bg_color", "#FFFFFF"))
        bg.setAlphaF(opacity)
        border = QColor(s.get("border_color", "#F5B8C8"))
        border.setAlphaF(opacity)

        w, h = self.width(), self.height()
        top = TAIL if self._flipped else 0
        path = QPainterPath()
        path.addRoundedRect(0, top, w, h - TAIL, radius, radius)
        if s.get("show_tail", True):
            cx = w / 2
            if self._flipped:
                path.moveTo(cx - TAIL / 2, top)
                path.lineTo(cx + TAIL / 2, top)
                path.lineTo(cx, top - TAIL + 1)
            else:
                body_h = h - TAIL
                path.moveTo(cx - TAIL / 2, body_h)
                path.lineTo(cx + TAIL / 2, body_h)
                path.lineTo(cx, body_h + TAIL - 1)
            path.closeSubpath()
        p.fillPath(path, bg)
        p.setPen(border)
        p.drawPath(path)

    # ---------- 跟随宠物 ----------
    def follow(self, pet_rect):
        """将气泡定位在宠物窗口上方；贴屏幕顶部时翻转到下方。"""
        if not self.isVisible():
            return
        screen = QGuiApplication.screenAt(pet_rect.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()

        x = pet_rect.x() + pet_rect.width() // 2 - self.width() // 2
        y = pet_rect.y() - self.height() - MARGIN
        flipped = False
        if y < area.top():                      # 顶部空间不足 → 放宠物下方
            y = pet_rect.bottom() + MARGIN
            flipped = True
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        y = max(area.top() + 4, min(y, area.bottom() - self.height() - 4))
        self._flipped = flipped
        self._adjust_size()               # 尾巴方向变化时重排内部布局
        self.move(round(x), round(y))
        self.update()
