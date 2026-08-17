# -*- coding: utf-8 -*-
"""对话面板：透明圆角窗口，包含历史记录与输入框。

- 点击桌宠或右键菜单打开，Enter 发送；
- 大模型调用在后台线程执行，不阻塞 UI；
- 标题栏可拖动面板位置，✕ 仅隐藏面板（程序不退出）。
"""
import html

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QPainter,
                           QPainterPath)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import llm_client


class ChatWorker(QThread):
    """后台执行大模型请求，避免阻塞界面。"""
    done = Signal(dict)

    def __init__(self, fn, args, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = args

    def run(self):
        self.done.emit(self._fn(*self._args))


class ChatPanel(QWidget):
    def __init__(self, settings, title: str, on_send, topmost: bool = True):
        super().__init__()
        self._settings = settings
        self._on_send = on_send
        self._drag_pos = None

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(340, 430)

        # 整体布局
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        inner = QWidget(self)
        inner.setObjectName("panelBody")
        inner.setStyleSheet("""
            #panelBody { background: transparent; }
            QLabel { background: transparent; border: none; }
            QLineEdit {
                background: rgba(255,255,255,235); border: 1px solid #E8D5DB;
                border-radius: 10px; padding: 6px 10px;
                color: #4A4038; font-size: 13px;
            }
            QPushButton {
                background: #F5B8C8; border: none; border-radius: 10px;
                padding: 6px 14px; color: white; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background: #F29CB4; }
            QTextBrowser { background: transparent; border: none; }
        """)
        outer.addWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # 标题栏（可拖动）
        self._header = QWidget(inner)
        h = QHBoxLayout(self._header)
        h.setContentsMargins(4, 2, 4, 2)
        self._title = QLabel(f"💬 {title}")
        self._title.setStyleSheet("color: #6B4F5A; font-weight: bold; font-size: 13px;")
        self._mode = QLabel("离线")
        self._mode.setStyleSheet("color: #9AA0A6; font-size: 11px; padding: 2px 6px;"
                                 "background: rgba(0,0,0,0.05); border-radius: 8px;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet("QPushButton { background: rgba(0,0,0,0.06); color: #7A6A70;"
                                "border-radius: 11px; font-size: 12px; padding: 0; }")
        close_btn.clicked.connect(self.hide)
        h.addWidget(self._title)
        h.addStretch(1)
        h.addWidget(self._mode)
        h.addWidget(close_btn)
        v.addWidget(self._header)

        # 历史记录
        self._history = QTextBrowser(inner)
        self._history.setOpenExternalLinks(False)
        self._history.document().setDefaultStyleSheet(
            "body { margin: 4px; font-family: 'Microsoft YaHei'; }")
        v.addWidget(self._history, 1)

        # 输入行
        row = QHBoxLayout()
        row.setSpacing(8)
        self._input = QLineEdit(inner)
        self._input.setPlaceholderText(f"和{title}说点什么…（Enter 发送）")
        self._input.returnPressed.connect(self.send)
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self.send)
        row.addWidget(self._input, 1)
        row.addWidget(send_btn)
        v.addLayout(row)

        # 拖动：仅标题栏可拖
        self._header.mousePressEvent = self._header_press
        self._header.mouseMoveEvent = self._header_move
        self._header.mouseReleaseEvent = self._header_release

        self._thinking = False

    # ---------- 面板绘制 ----------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(1, 1, self.width() - 2, self.height() - 2, 18, 18)
        p.fillPath(path, QColor(255, 250, 252, 246))
        p.setPen(QColor("#E8D5DB"))
        p.drawPath(path)

    # ---------- 拖动 ----------
    def _header_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _header_move(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _header_release(self, event):
        self._drag_pos = None

    # ---------- 消息 ----------
    def send(self):
        text = self._input.text().strip()
        if not text or self._thinking:
            return
        self._input.clear()
        self.append_user(text)
        self._on_send(text)

    def _append(self, who: str, text: str):
        esc = html.escape(text).replace("\n", "<br>")
        if who == "user":
            bubble = (f'<table width="100%"><tr><td align="right">'
                      f'<span style="display:inline-block;max-width:75%;word-wrap:break-word;'
                      f'background:#F5B8C8;color:#fff;padding:7px 11px;border-radius:12px;'
                      f'font-size:13px;">{esc}</span></td></tr></table>')
        else:
            bubble = (f'<table width="100%"><tr><td align="left">'
                      f'<span style="display:inline-block;max-width:75%;word-wrap:break-word;'
                      f'background:#FFFFFF;color:#4A4038;padding:7px 11px;border-radius:12px;'
                      f'border:1px solid #F0DFE4;font-size:13px;">{esc}</span></td></tr></table>')
        self._history.append(bubble)
        sb = self._history.verticalScrollBar()
        sb.setValue(sb.maximum())

    def append_user(self, text):
        self._append("user", text)

    def append_pet(self, text):
        self._append("pet", text)

    def append_note(self, text):
        """灰色系统提示（如 API 配置错误说明）。"""
        esc = html.escape(text)
        self._history.append(
            f'<div style="color:#9AA0A6;font-size:11px;text-align:center;'
            f'margin:4px;">{esc}</div>')
        sb = self._history.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_thinking(self, on: bool):
        self._thinking = on
        self._input.setEnabled(not on)
        if on:
            self.append_note("……")
        sb = self._history.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_mode(self, text: str, color: str):
        self._mode.setText(text)
        self._mode.setStyleSheet(
            f"color: {color}; font-size: 11px; padding: 2px 6px;"
            f"background: rgba(0,0,0,0.05); border-radius: 8px;")

    # ---------- 显示位置 ----------
    def open_near(self, pet_rect):
        """在宠物附近打开面板：优先右侧，其次左侧，最后屏幕内任意。"""
        screen = QGuiApplication.screenAt(pet_rect.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = pet_rect.right() + 16
        if x + self.width() > area.right():
            x = pet_rect.left() - self.width() - 16
            if x < area.left():
                x = max(area.left() + 8, area.right() - self.width() - 8)
        y = min(max(area.top() + 8, pet_rect.center().y() - self.height() // 2),
                area.bottom() - self.height() - 8)
        self.move(round(x), round(y))
        self.show()
        self.raise_()
        self._input.setFocus()

    def closeEvent(self, event):
        """关闭仅隐藏面板，宠物继续运行。"""
        event.ignore()
        self.hide()
