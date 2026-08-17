# -*- coding: utf-8 -*-
"""文字气泡窗口：悬浮于宠物周边（不遮挡主体），逐字显示、自动消失。

定位策略（修复贴边错位问题）：
- 候选方位依次尝试：上方 → 下方 → 右侧 → 左侧，优先选"完整落在屏幕内
  且不与聊天面板重叠"的位置；
- 气泡尺寸随文字变化，任何尺寸变化都会触发重新定位（此前长文本把气泡
  "顶"离宠物的 bug 由此修复）；
- 尾巴锚点始终指向宠物中心，即使气泡被屏幕边缘裁切也保持视觉连接；
- 样式（颜色/字体/圆角/透明度/速度等）全部由 config/settings.json 的
  bubble 节控制；气泡鼠标穿透，绝不拦截点击。
"""
from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QRect, Qt,
                            QTimer)
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
        self._flipped = False        # True=气泡显示在宠物下方（尾巴朝上）
        self._tail_x = None          # 尾巴在气泡上的 x 位置（锚定宠物中心）
        self._pet_rect = None        # 宠物窗口矩形（用于重新定位）
        self._avoid = None           # 需要避让的矩形（聊天面板）
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
            if self._pet_rect is not None:
                self.follow(self._pet_rect, self._avoid)   # 新文本尺寸变了，重新贴回宠物
            self._typing_timer.start(interval)

    def _tick(self):
        """逐字显示，完成后安排自动消失。"""
        self._shown = min(len(self._full_text), self._shown + self._chars_per_tick)
        self._label.setText(self._full_text[:self._shown])
        if self._adjust_size() and self._pet_rect is not None:
            self.follow(self._pet_rect, self._avoid)   # 尺寸随打字增长 → 贴回宠物
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
    def _adjust_size(self) -> bool:
        """按当前已显示文本计算气泡尺寸。

        返回 True 表示尺寸发生变化（调用方应随后调用 follow 重新贴回宠物）。
        注意：本方法不调用 follow，follow 也不调用本方法，避免互相递归。
        """
        fm = QFontMetrics(self._label.font())
        max_w = int(self._style.get("max_width", 280))
        rect = fm.boundingRect(0, 0, max_w, 10000,
                               Qt.TextFlag.TextWordWrap,
                               self._label.text() or " ")
        pad = self._pad
        w = max(40, min(max_w, rect.width() + 4) + pad * 2)
        h = rect.height() + pad * 2
        new_size = (w, h + TAIL)
        changed = new_size != (self.width(), self.height())
        self.setFixedSize(*new_size)
        self._relayout_label(w, h, pad)
        return changed

    def _relayout_label(self, w: int, h: int, pad: int):
        """仅重排标签位置（尾巴翻转时留出上方空间）。"""
        top = TAIL if self._flipped else 0
        self._label.setGeometry(pad, top + pad - 2, w - pad * 2, h)

    def paintEvent(self, event):
        """圆角矩形气泡 + 指向宠物的小尾巴（尾巴 x 由 _tail_x 锚定宠物）。"""
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
            cx = self._tail_x if self._tail_x is not None else w / 2
            cx = max(TAIL, min(cx, w - TAIL))
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

    # ---------- 跟随宠物（核心定位逻辑） ----------
    def follow(self, pet_rect, avoid_rect=None):
        """把气泡放到宠物旁边：上方 → 下方 → 右侧 → 左侧，要求完整在屏幕内
        且不与聊天面板（avoid_rect）重叠；都不满足则退化为贴边钳制。

        气泡隐藏时也记录 pet_rect/avoid_rect，便于下次显示直接定位。
        """
        self._pet_rect = QRect(pet_rect)
        self._avoid = QRect(avoid_rect) if avoid_rect is not None else None
        if not self.isVisible():
            return
        screen = QGuiApplication.screenAt(self._pet_rect.center()) \
            or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w, h = self.width(), self.height()
        pcx = self._pet_rect.center().x()
        pcy = self._pet_rect.center().y()

        # 候选方位：(说明, 左x, 上y, 是否翻转)
        candidates = [
            ("above", pcx - w / 2, self._pet_rect.top() - h - MARGIN, False),
            ("below", pcx - w / 2, self._pet_rect.bottom() + MARGIN, True),
            ("right", self._pet_rect.right() + MARGIN, pcy - h / 2, False),
            ("left", self._pet_rect.left() - w - MARGIN, pcy - h / 2, False),
        ]
        for _side, bx, by, flipped in candidates:
            rect = QRect(round(bx), round(by), w, h)
            if (rect.left() < area.left() or rect.right() > area.right()
                    or rect.top() < area.top() or rect.bottom() > area.bottom()):
                continue                               # 屏幕外 → 换方位
            if self._avoid is not None and rect.intersects(self._avoid.adjusted(2, 2, -2, -2)):
                continue                               # 挡住聊天面板 → 换方位
            self._place(rect, flipped, pcx)
            return

        # 兜底：贴边钳制，尽量对齐宠物中心
        x = pcx - w / 2
        y = self._pet_rect.top() - h - MARGIN
        flipped = False
        if y < area.top():
            y = self._pet_rect.bottom() + MARGIN
            flipped = True
        x = max(area.left() + 4, min(x, area.right() - w - 4))
        y = max(area.top() + 4, min(y, area.bottom() - h - 4))
        self._place(QRect(round(x), round(y), w, h), flipped, pcx)

    def _place(self, rect: QRect, flipped: bool, pet_center_x: float):
        """落地位置：设置翻转方向、尾巴锚点、重排标签并移动。

        仅重排标签（不重算尺寸），与 _adjust_size 解耦，避免递归。
        """
        self._flipped = flipped
        # 尾巴 x = 宠物中心在气泡上的投影（钳制在气泡范围内）
        self._tail_x = max(TAIL, min(pet_center_x - rect.left(), rect.width() - TAIL))
        self._relayout_label(self.width(), self.height() - TAIL, self._pad)
        self.move(rect.topLeft())
        self.update()
