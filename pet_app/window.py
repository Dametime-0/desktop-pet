# -*- coding: utf-8 -*-
"""宠物主窗口：透明无边框、始终置顶、左键拖动、滚轮缩放、右键菜单信号。

图形结构（QGraphicsScene）：
    scene
      └── root_group   QGraphicsItemGroup —— 负责呼吸/摇摆（常驻动画层）
            └── pet_item  QGraphicsPixmapItem —— 负责动作变换（摸头/蹦跳等）

两层分离保证常驻动画与动作动画互不干扰、叠加流畅。
变换锚点均为"底部中心"，缩放时宠物像站在地上一样。
"""
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsItemGroup,
                               QGraphicsPixmapItem, QGraphicsScene, QGraphicsView)

IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

SCALE_MIN, SCALE_MAX = 0.25, 3.0


class PetWindow(QGraphicsView):
    """透明无边框桌宠窗口。"""

    clicked = Signal(float, float)          # 单击（非拖动），参数为窗口内相对坐标 0~1
    dropped_image = Signal(str)             # 拖入图片文件
    context_menu_requested = Signal(QPoint)  # 右键菜单（全局坐标）
    geometry_changed = Signal()             # 移动/缩放，用于气泡跟随与状态保存
    interaction_started = Signal()          # 用户按住宠物（拖动/点击），用于中断走路

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._press_global = None
        self._dragging = False

        # 透明无边框 + 置顶 + 不占任务栏（Tool）
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if settings.get("window.always_on_top", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: transparent;")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAcceptDrops(True)

        # 场景与图形项
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.root_group = QGraphicsItemGroup()
        self.pet_item = QGraphicsPixmapItem()
        self.root_group.addToGroup(self.pet_item)
        self._scene.addItem(self.root_group)
        self.pet_item.setZValue(1)

        # 缩放状态：fit = 图片缩放到基准高度；user = 滚轮倍率
        self._pixmap = None
        self._fit_scale = 1.0
        self._user_scale = settings.get("window.scale", 1.0)
        self._headroom = 0            # 顶部临时留白（像素），供跳跃等动作使用
        self._content_h = 1           # 形象本体显示高度（像素）
        self._content_w = 1           # 形象本体显示宽度（像素）
        self._margin = 6              # 窗口四周边距，吸收动作的小幅溢出

    # ---------- 形象加载与缩放 ----------
    def load_image(self, path: str):
        """加载宠物图片并按基准高度适配显示。"""
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return False
        self._pixmap = pixmap
        base_h = self._settings.get("window.base_height", 240)
        self._fit_scale = base_h / pixmap.height()
        self.pet_item.setPixmap(pixmap)
        self.pet_item.setTransformOriginPoint(pixmap.width() / 2, pixmap.height())
        self._apply_scale()
        return True

    def _apply_scale(self):
        """按 fit × user 缩放图像并同步窗口大小。

        坐标约定（简单直接，无变换锚点）：
        - 图形项以左上角为原点、平铺缩放，放在 (margin, margin)；
        - 窗口 = 形象本体 + 四周 margin，动作的小幅溢出由 margin 吸收；
        - 跳跃留白：场景顶部为负 y 方向（set_jump_headroom）。
        """
        if self._pixmap is None:
            return
        self.set_jump_headroom(0)          # 缩放时取消临时跳跃留白，重新计算
        zoom = self._fit_scale * self._user_scale
        w, h = self._pixmap.width(), self._pixmap.height()
        content_h = round(h * zoom)
        content_w = round(w * zoom)
        self._margin = max(6, round(content_h * 0.03))
        m = self._margin
        self.pet_item.setPos(m, m)
        self.pet_item.setTransform(QTransform.fromScale(zoom, zoom))
        self.root_group.setTransform(QTransform())
        self._scene.setSceneRect(0.0, 0.0, content_w + 2 * m, content_h + 2 * m)
        self.setFixedSize(content_w + 2 * m, content_h + 2 * m)
        self._headroom = 0
        self._content_h = content_h
        self._content_w = content_w
        self._anchor_view()
        self.geometry_changed.emit()

    def set_jump_headroom(self, px: float):
        """跳跃时临时加高窗口顶部空间（向上扩展），保证头部不被裁切。

        px 为目标留白高度（像素），窗口向上增长并同步场景范围；
        恢复为 0 时窗口缩回原尺寸并下移还原。跳跃结束后必须调用 0。
        """
        if self._pixmap is None:
            return
        zoom = self._fit_scale * self._user_scale
        w, h = self._pixmap.width(), self._pixmap.height()
        content_h = round(h * zoom)
        content_w = round(w * zoom)
        m = self._margin
        px = max(0, int(round(px)))
        if px == self._headroom:
            return
        d = px - self._headroom
        # 先移动窗口（向上扩展时同步上移，保持形象贴地）
        screen = QApplication.screenAt(self.frameGeometry().center()) \
            or QApplication.primaryScreen()
        new_y = self.y() - d
        if new_y >= screen.availableGeometry().top():   # 屏幕顶部空间不足则不移动
            self.move(self.x(), new_y)
        # 可见区 = [0, content] 本体 + [-px, 0) 顶部留白，四周 margin
        self._scene.setSceneRect(0.0, -px, content_w + 2 * m,
                                 content_h + 2 * m + px)
        self.setFixedSize(content_w + 2 * m, content_h + 2 * m + px)
        self._headroom = px
        self._content_h = content_h
        self._content_w = content_w
        self._anchor_view()
        self.geometry_changed.emit()

    def _anchor_view(self):
        """窗口尺寸变化后强制视图锚定到场景左上角。

        防止 QGraphicsView 在缩放/加高后滚动偏移，出现只显示人物
        局部（如左上四分之一）的渲染异常。
        """
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self.viewport().update()

    def content_geometry(self):
        """形象本体的屏幕矩形（不含边距与顶部跳跃留白），供气泡跟随与面板定位。"""
        geo = self.frameGeometry()
        return geo.adjusted(self._margin, self._margin + self._headroom,
                            -self._margin, -self._margin)

    def set_user_scale(self, scale: float):
        self._user_scale = max(SCALE_MIN, min(SCALE_MAX, scale))
        self._settings.set("window.scale", self._user_scale)
        self._apply_scale()

    def user_scale(self) -> float:
        return self._user_scale

    def pet_height(self) -> float:
        """当前显示高度（像素），动画跳跃幅度按此计算。"""
        return self.height()

    # ---------- 交互：拖动 / 单击 / 滚轮 ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._dragging = False
            self.interaction_started.emit()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._press_global:
            cur = event.globalPosition().toPoint()
            if (cur - self._press_global).manhattanLength() > QApplication.startDragDistance():
                self._dragging = True
            if self._dragging:
                self.move(self.pos() + cur - self._press_global)
                self._press_global = cur
                self.geometry_changed.emit()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._dragging and self._press_global is not None:
                p = event.position().toPoint()
                # 点击坐标换算到形象本体（去掉边距与顶部留白）
                fx = (p.x() - self._margin) / max(1, self._content_w)
                fy = (p.y() - self._margin - self._headroom) / max(1, self._content_h)
                fx = max(0.0, min(1.0, fx))
                fy = max(0.0, min(1.0, fy))
                self.clicked.emit(fx, fy)
            self._press_global = None
            self._dragging = False
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        """滚轮缩放：向上放大、向下缩小。"""
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 1 / 1.1
        self.set_user_scale(self._user_scale * factor)
        event.accept()

    def contextMenuEvent(self, event):      # noqa: N802
        self.context_menu_requested.emit(event.globalPos())
        event.accept()

    # ---------- 拖放导入图片 ----------
    def dragEnterEvent(self, event):
        if any(u.toLocalFile().lower().endswith(IMG_EXTS)
               for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for u in event.mimeData().urls():
            path = u.toLocalFile()
            if path.lower().endswith(IMG_EXTS):
                self.dropped_image.emit(path)
                break
        event.acceptProposedAction()

    # ---------- 窗口状态 ----------
    def set_topmost(self, on: bool):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def restore_position(self):
        """恢复上次位置（未记录或窗口中心已不在任何屏幕内则放到主屏右下角）。"""
        x, y = self._settings.get("window.x"), self._settings.get("window.y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x >= 0 and y >= 0:
            center = QPoint(round(x) + self.width() // 2,
                            round(y) + self.height() // 2)
            if QApplication.screenAt(center) is not None:   # 窗口中心必须在屏幕内
                self.move(round(x), round(y))
                return
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - 40,
                  geo.bottom() - self.height() - 60)

    def save_position(self):
        self._settings.set("window.x", self.x())
        self._settings.set("window.y", self.y())
        self._settings.set("window.scale", round(self._user_scale, 3))
        self._settings.set("window.always_on_top",
                           bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
