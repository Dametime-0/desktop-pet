# -*- coding: utf-8 -*-
"""文字气泡窗口：悬浮于宠物四周（不遮挡主体），逐字显示、自动消失。

- 独立顶层窗口 + WA_TransparentForMouseEvents，气泡完全不拦截鼠标；
- 样式（颜色/字体/圆角/透明度/速度等）全部由 config/settings.json 的 bubble 节控制；
- 定位策略 _choose_rect：按 上 → 下 → 右 → 左 顺序找第一个「在屏幕内且不与
  避让窗口（对话面板）重叠」的位置，尾巴始终指向宠物（上下左右四向）；
- 每次显示文本后由控制器调用 follow() 重新定位，宠物移动/缩放时同步跟随。
"""
from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPainterPath)
from PySide6.QtWidgets import QLabel, QWidget

TAIL = 10          # 气泡尾巴长度
MARGIN = 10        # 与宠物窗口/避让窗口的间距

#: 尾巴方向（尾巴指向宠物一侧）
SIDE_DOWN = "down"      # 气泡在宠物上方
SIDE_UP = "up"          # 气泡在宠物下方
SIDE_LEFT = "left"      # 气泡在宠物右侧（尾巴朝左）
SIDE_RIGHT = "right"    # 气泡在宠物左侧（尾巴朝右）


def choose_rect(pet_rect: QRect, size, area: QRect, avoid_rect=None):
    """挑选气泡位置：返回 (气泡矩形, 尾巴方向)。

    依次尝试 上/下/右/左 四个候选位，要求完整落在屏幕可用区域内、
    且不与避让窗口（如对话面板）重叠；全部冲突时退回上方并横向平移避开面板。
    纯函数，便于单元测试。
    """
    bw, bh = size.width(), size.height()
    pc = pet_rect.center()

    def clamp(r: QRect) -> QRect:
        r = QRect(r)
        r.setWidth(min(bw, area.width()))
        r.setHeight(min(bh, area.height()))
        if r.right() > area.right():
            r.moveRight(area.right())
        if r.left() < area.left():
            r.moveLeft(area.left())
        if r.bottom() > area.bottom():
            r.moveBottom(area.bottom())
        if r.top() < area.top():
            r.moveTop(area.top())
        return r

    candidates = [
        (QRect(pc.x() - bw // 2, pet_rect.top() - bh - MARGIN, bw, bh), SIDE_DOWN),
        (QRect(pc.x() - bw // 2, pet_rect.bottom() + MARGIN, bw, bh), SIDE_UP),
        (QRect(pet_rect.right() + MARGIN, pet_rect.top() + pet_rect.height() // 4 - bh // 2,
               bw, bh), SIDE_LEFT),
        (QRect(pet_rect.left() - bw - MARGIN, pet_rect.top() + pet_rect.height() // 4 - bh // 2,
               bw, bh), SIDE_RIGHT),
    ]
    for rect, side in candidates:
        rect = clamp(rect)
        if rect.intersects(pet_rect):              # 不得遮挡宠物本体
            continue
        if avoid_rect is not None and rect.intersects(avoid_rect):
            continue
        return rect, side

    # 全部冲突（宠物与面板之间空间狭小）：退回上方，向远离面板的一侧平移
    rect = clamp(QRect(candidates[0][0]))
    if rect.intersects(pet_rect):
        rect.moveBottom(pet_rect.top() - MARGIN)   # 被钳进宠物时抬回宠物上方
        rect = clamp(rect)
    if avoid_rect is not None and rect.intersects(avoid_rect):
        if pc.x() < avoid_rect.center().x():
            rect.moveRight(avoid_rect.left() - MARGIN)
        else:
            rect.moveLeft(avoid_rect.right() + MARGIN)
    rect = clamp(rect)
    if rect.intersects(pet_rect):                  # 空间实在不够时，退到屏幕同侧边缘
        rect.moveTopRight(area.topRight().translated(-MARGIN, MARGIN))
    return clamp(rect), SIDE_DOWN


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
        self._sticky = False       # True=不自动消失（AI 进度气泡等），由调用方 hide_now()
        self._tail_side = SIDE_DOWN
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
    def show_text(self, text: str, sticky: bool = False):
        """显示新文本（打断当前气泡），逐字打出。位置由调用方随后 follow() 设置。

        sticky=True 时打完不自动消失（用于 AI 进度提示），由调用方 hide_now() 结束。
        """
        self._full_text = text or ""
        self._sticky = sticky
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
            if self._sticky:      # 进度模式：不自动消失
                return
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
    def _body_rect(self) -> QRect:
        """气泡主体（不含尾巴）在窗口内的矩形。"""
        w, h = self.width(), self.height()
        x = TAIL if self._tail_side == SIDE_LEFT else 0
        y = TAIL if self._tail_side == SIDE_UP else 0
        bw = w - (TAIL if self._tail_side in (SIDE_LEFT, SIDE_RIGHT) else 0)
        bh = h - (TAIL if self._tail_side in (SIDE_UP, SIDE_DOWN) else 0)
        return QRect(x, y, bw, bh)

    def _adjust_size(self):
        """按当前已显示文本与尾巴方向计算气泡尺寸。"""
        fm = QFontMetrics(self._label.font())
        max_w = int(self._style.get("max_width", 280))
        rect = fm.boundingRect(0, 0, max_w, 10000,
                               Qt.TextFlag.TextWordWrap,
                               self._label.text() or " ")
        pad = self._pad
        w = max(40, min(max_w, rect.width() + 4) + pad * 2)
        h = rect.height() + pad * 2
        body = QRect(TAIL if self._tail_side == SIDE_LEFT else 0,
                     TAIL if self._tail_side == SIDE_UP else 0, w, h)
        self.setFixedSize(body.width() + (TAIL if self._tail_side in (SIDE_LEFT, SIDE_RIGHT) else 0),
                          body.height() + (TAIL if self._tail_side in (SIDE_UP, SIDE_DOWN) else 0))
        self._label.setGeometry(body.x() + pad, body.y() + pad - 2,
                                w - pad * 2, h)

    def paintEvent(self, event):
        """圆角矩形气泡 + 指向宠物的小尾巴（四向）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._style
        radius = float(s.get("corner_radius", 16))
        opacity = max(0.1, min(1.0, float(s.get("opacity", 0.94))))

        bg = QColor(s.get("bg_color", "#FFFFFF"))
        bg.setAlphaF(opacity)
        border = QColor(s.get("border_color", "#F5B8C8"))
        border.setAlphaF(opacity)

        body = self._body_rect()
        path = QPainterPath()
        path.addRoundedRect(body.x(), body.y(), body.width(), body.height(),
                            radius, radius)
        if s.get("show_tail", True):
            side, t = self._tail_side, TAIL
            cx, cy = body.center().x(), body.center().y()
            if side == SIDE_DOWN:        # 尾巴在底部，朝下指
                path.moveTo(cx - t / 2, body.bottom() + 1)
                path.lineTo(cx + t / 2, body.bottom() + 1)
                path.lineTo(cx, body.bottom() + t - 1)
            elif side == SIDE_UP:        # 尾巴在顶部，朝上指
                path.moveTo(cx - t / 2, body.top() - 1)
                path.lineTo(cx + t / 2, body.top() - 1)
                path.lineTo(cx, body.top() - t + 1)
            elif side == SIDE_LEFT:      # 尾巴在左侧，朝左指
                path.moveTo(body.left() - 1, cy - t / 2)
                path.lineTo(body.left() - 1, cy + t / 2)
                path.lineTo(body.left() - t + 1, cy)
            else:                        # 尾巴在右侧，朝右指
                path.moveTo(body.right() + 1, cy - t / 2)
                path.lineTo(body.right() + 1, cy + t / 2)
                path.lineTo(body.right() + t - 1, cy)
            path.closeSubpath()
        p.fillPath(path, bg)
        p.setPen(border)
        p.drawPath(path)

    # ---------- 跟随宠物 ----------
    def follow(self, pet_rect: QRect, avoid_rect: QRect = None):
        """将气泡定位到宠物四周最合适的位置（见 choose_rect），需气泡可见。"""
        if not self.isVisible():
            return
        screen = QGuiApplication.screenAt(pet_rect.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry().adjusted(4, 4, -4, -4)
        rect, side = choose_rect(pet_rect, self.size(), area, avoid_rect)
        if side != self._tail_side:
            self._tail_side = side
            self._adjust_size()          # 尾巴方向变化时重排内部布局
        self.move(rect.topLeft())
        self.update()
