# -*- coding: utf-8 -*-
"""动画控制器。

- 常驻动画（呼吸 + 摇摆）作用在 root_group 上，始终运行；
- 动作动画（摸头/蹦跳/转圈等）作用在 pet_item 上，串行排队执行，
  避免同时抢占同一变换属性导致画面跳变；
- 所有动画基于 QVariantAnimation 插值，锚点为底部中心，过渡平滑。

支持的动作名（人格配置中 keyword_rules / easter_eggs 的 action 字段）：
    pat 摸头 | bounce 蹦跳 | jump 小跳 | spin 转圈 | squish 压扁
    dance 跳舞 | shake 摇头 | happy 开心（爱心粒子）
"""
import math
import random
from collections import deque

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QObject, Qt,
                            QTimer, QVariantAnimation)
from PySide6.QtGui import QFont, QTransform
from PySide6.QtWidgets import QGraphicsTextItem

HEART_EMOJIS = ("❤", "💕", "✨", "🌸")
ACTIONS = ("pat", "bounce", "jump", "spin", "squish", "dance", "shake", "happy")


class AnimController(QObject):
    def __init__(self, root_group, pet_item, scene, headroom_cb=None):
        super().__init__()
        self.root = root_group
        self.pet = pet_item
        self.scene = scene
        self._headroom_cb = headroom_cb   # 跳跃时通知窗口临时加高顶部（防头部裁切）
        self._headroom_px = 0.0
        self._zoom = 1.0
        self._queue = deque()      # 待播放动作队列
        self._busy = False
        self._last_action = None
        self._breath = None
        self._sway = None
        # 帧动画状态
        self._frames = None           # FrameLibrary
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(100)     # 默认 10fps
        self._frame_timer.timeout.connect(self._on_frame_tick)
        self._frame_set = None
        self._frame_idx = 0
        self._frame_loop = False
        self._frame_mirror = False
        self._frame_action = None
        self._idle_frames = False     # 待机是否由帧动画接管

    def set_frame_library(self, lib):
        """接入帧素材库（有素材的动作优先播放帧动画）。"""
        self._frames = lib
        if lib and lib.has("idle") and len(lib.get("idle")) >= 2:
            self._idle_frames = True
        else:
            self._idle_frames = False

    # ---------- 基础工具 ----------
    def set_zoom(self, zoom: float):
        """窗口缩放倍率变化时同步（粒子、跳跃幅度需要）。"""
        self._zoom = zoom

    def _make(self, duration, curve, on_update):
        """构造 0→1 缓动动画，on_update 收到缓动后的值 v。"""
        anim = QVariantAnimation(self)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(curve)
        anim.valueChanged.connect(on_update)
        return anim

    def _lerp_fn(self, a, b, setter):
        """线性插值 setter：v 从 0→1 时数值从 a→b。"""
        return lambda v: setter(a + (b - a) * v)

    def _set_scale(self, sx, sy):
        # PySide6 中 setScale 仅支持均匀缩放，非均匀缩放用 setTransform
        z = self._zoom
        self.pet.setTransform(QTransform.fromScale(z * sx, z * sy))

    def _set_rot(self, deg):
        self.pet.setRotation(deg)

    def _set_y(self, y):
        self.pet.setY(y)

    # ---------- 常驻待机动画 ----------
    def start_idle(self):
        """待机：有帧素材播帧循环，否则左右轻摆 + 上下呼吸（纯平移）。"""
        if self._idle_frames:
            fs = self._frames.get("idle")
            self._frame_set = fs
            self._frame_idx = 0
            self._frame_loop = True
            self._frame_action = "idle"
            self._apply_frame()
            self._frame_timer.start()
            return
        if self._breath is not None:
            return
        self._idle_t = 0.0
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(33)
        self._idle_timer.timeout.connect(self._on_idle_tick)
        self._idle_timer.start()
        self._breath = self._idle_timer

    def _on_idle_tick(self):
        self._idle_t += 0.033
        x = 1.2 * math.sin(self._idle_t * 0.9)          # 左右轻摆 ±1.2px
        y = 1.8 * abs(math.sin(self._idle_t * 0.55))    # 上下呼吸 0~1.8px
        self.root.setTransform(QTransform.fromTranslate(x, y))

    def stop_idle(self):
        """停止待机动画（帧循环或呼吸轻摆）。"""
        if self._frame_action == "idle" and self._frame_set is not None:
            self._frame_timer.stop()
            self._frame_set = None
            self._frame_action = None
        if self._breath is not None:
            self._breath.stop()
            self._breath = None
        self.root.setTransform(QTransform())

    # ---------- 帧动画播放 ----------
    def _base_frame_size(self):
        pm = self.pet.pixmap()
        return pm.size() if pm is not None and not pm.isNull() else None

    def _frame_seq(self):
        """当前帧集合的播放序列（循环衔接由抽帧端的循环点对齐保证）。"""
        fs = self._frame_set
        if fs is None:
            return []
        return fs.pixmaps(self._base_frame_size(), self._frame_mirror)

    def _apply_frame(self):
        seq = self._frame_seq()
        if seq:
            self.pet.setPixmap(seq[self._frame_idx % len(seq)])

    def _play_frame_action(self, fs, action: str):
        """播放一次性帧动画动作（暂停待机帧循环，结束后恢复）。"""
        if self._frame_action == "idle" and self._frame_set is not None:
            self._frame_timer.stop()
            self._frame_set = None
            self._frame_action = None
        self._busy = True
        self._last_action = action
        self._frame_set = fs
        self._frame_idx = 0
        self._frame_loop = False
        self._frame_action = action
        self._frame_mirror = False
        self._apply_frame()
        self._frame_timer.start()

    def _on_frame_tick(self):
        seq = self._frame_seq()
        if not seq:
            self._frame_timer.stop()
            return
        if self._frame_idx + 1 >= len(seq):
            if self._frame_loop:
                self._frame_idx = 0        # 循环（含过渡帧，衔接平滑）
            else:
                self._frame_timer.stop()
                self._frame_set = None
                self._frame_action = None
                if self._idle_frames:
                    self.start_idle()      # 恢复待机帧循环
                self._on_finished()
                return
        else:
            self._frame_idx += 1
        self._apply_frame()

    def start_walk_frames(self, mirror: bool) -> bool:
        """走路帧循环（返回 False 表示无走路帧素材，调用方回退颠簸模式）。"""
        fs = self._frames.get("walk") if self._frames else None
        if fs is None or len(fs) < 2:
            return False
        if self._frame_action == "idle" and self._frame_set is not None:
            self._frame_timer.stop()
            self._frame_set = None
            self._frame_action = None
        self._frame_set = fs
        self._frame_idx = 0
        self._frame_loop = True
        self._frame_action = "walk"
        self._frame_mirror = mirror
        self._apply_frame()
        self._frame_timer.start()
        return True

    def stop_walk_frames(self):
        if self._frame_action == "walk" and self._frame_set is not None:
            self._frame_timer.stop()
            self._frame_set = None
            self._frame_action = None
            if self._idle_frames:
                self.start_idle()

    # ---------- 动作播放（串行队列） ----------
    def is_busy(self) -> bool:
        """是否有动作动画正在播放（走路等外部动画可据此避让）。"""
        return self._busy

    def _set_jump_headroom(self, px: float):
        """起跳前向窗口申请顶部留白，落地后（0）释放。"""
        if self._headroom_cb and px != self._headroom_px:
            self._headroom_px = px
            self._headroom_cb(px)

    def walk_bob(self, phase: float):
        """走路颠簸：底部锚定的呼吸式压伸 + 小幅度摇摆（不产生向上位移，
        因此不需要顶部留白，也不会裁到头部）。"""
        z = self._zoom
        sy = 1.0 + 0.035 * phase
        sx = 1.0 - 0.02 * abs(phase)
        self.pet.setTransform(QTransform.fromScale(z * sx, z * sy))
        self.pet.setRotation(phase * 2.5)

    def walk_bob_reset(self):
        z = self._zoom
        self.pet.setTransform(QTransform.fromScale(z, z))
        self.pet.setRotation(0.0)

    def play(self, name: str):
        """请求播放动作。有帧素材优先帧动画，否则变换动画（串行队列）。"""
        if name not in ACTIONS:
            name = "jump"
        fs = self._frames.get(name) if self._frames else None
        if fs is not None and len(fs) >= 2:
            self._play_frame_action(fs, name)     # 帧动画：立即播放（可打断）
            return
        if self._busy:
            if name != self._last_action and len(self._queue) < 2:
                self._queue.append(name)
            return
        self._run(name)

    def _run(self, name: str):
        self._busy = True
        self._last_action = name
        builder = getattr(self, f"_build_{name}", self._build_jump)
        group = builder()
        group.finished.connect(self._on_finished)
        group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_finished(self):
        self._set_jump_headroom(0)     # 动作结束，释放跳跃留白
        self._busy = False
        if self._queue:
            self._run(self._queue.popleft())

    # ---------- 动作构建 ----------
    def _build_pat(self):
        """摸头：先压扁（向下压缩），弹性回弹，同时左右小摇摆，头顶冒爱心。"""
        from PySide6.QtCore import QParallelAnimationGroup, QSequentialAnimationGroup

        squish = QSequentialAnimationGroup(self)
        squish.addAnimation(self._make(
            130, QEasingCurve.Type.InOutQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(1 + 0.12 * v, 1 - 0.14 * v))))
        squish.addAnimation(self._make(
            520, QEasingCurve.Type.OutElastic,
            self._lerp_fn(0, 1, lambda v: self._set_scale(1 + 0.12 * (1 - v),
                                                          1 - 0.14 * (1 - v)))))

        wiggle = self._make(
            340, QEasingCurve.Type.InOutSine,
            self._lerp_fn(-6.0, 6.0, self._set_rot))
        back = self._make(340, QEasingCurve.Type.InOutSine,
                          self._lerp_fn(6.0, 0.0, self._set_rot))
        wiggle_group = QSequentialAnimationGroup(self)
        wiggle_group.addAnimation(wiggle)
        wiggle_group.addAnimation(back)

        group = QParallelAnimationGroup(self)
        group.addAnimation(squish)
        group.addAnimation(wiggle_group)
        self.spawn_hearts(3)
        return group

    def _build_bounce(self):
        """蹦跳：蓄力下蹲 → 跳起 → 落地 → 落地压扁回弹。"""
        from PySide6.QtCore import QParallelAnimationGroup, QSequentialAnimationGroup

        jump_h = self.pet.boundingRect().height() * self._zoom * 0.22
        self._set_jump_headroom(jump_h)      # 起跳前给窗口留出头顶空间
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(
            110, QEasingCurve.Type.InQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(1 + 0.08 * v, 1 - 0.06 * v))))
        seq.addAnimation(self._make(
            230, QEasingCurve.Type.OutQuad,
            self._lerp_fn(0, -jump_h, self._set_y)))
        seq.addAnimation(self._make(
            200, QEasingCurve.Type.InQuad,
            self._lerp_fn(-jump_h, 0, self._set_y)))
        land = QSequentialAnimationGroup(self)
        land.addAnimation(self._make(
            110, QEasingCurve.Type.InQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(1 + 0.16 * v, 1 - 0.18 * v))))
        land.addAnimation(self._make(
            480, QEasingCurve.Type.OutElastic,
            self._lerp_fn(0, 1, lambda v: self._set_scale(1 + 0.16 * (1 - v),
                                                          1 - 0.18 * (1 - v)))))
        seq.addAnimation(land)
        return seq

    def _build_jump(self):
        """小跳（彩蛋动作的通用跳跃）。"""
        jump_h = self.pet.boundingRect().height() * self._zoom * 0.14
        self._set_jump_headroom(jump_h)
        from PySide6.QtCore import QSequentialAnimationGroup
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(200, QEasingCurve.Type.OutQuad,
                                    self._lerp_fn(0, -jump_h, self._set_y)))
        seq.addAnimation(self._make(190, QEasingCurve.Type.InQuad,
                                    self._lerp_fn(-jump_h, 0, self._set_y)))
        return seq

    def _build_spin(self):
        """转圈：缩小到 55% 后绕底部中心旋转 360°，避免甩出窗口裁切。"""
        from PySide6.QtCore import QParallelAnimationGroup, QSequentialAnimationGroup
        shrink = QSequentialAnimationGroup(self)
        shrink.addAnimation(self._make(
            250, QEasingCurve.Type.InOutQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(1 - 0.45 * v, 1 - 0.45 * v))))
        shrink.addAnimation(self._make(
            450, QEasingCurve.Type.OutBack,
            self._lerp_fn(0, 1, lambda v: self._set_scale(0.55 + 0.45 * v,
                                                          0.55 + 0.45 * v))))
        spin = QSequentialAnimationGroup(self)
        spin.addAnimation(self._make(650, QEasingCurve.Type.OutCubic,
                                     self._lerp_fn(0, 360, self._set_rot)))
        spin.addAnimation(self._make(200, QEasingCurve.Type.InOutSine,
                                     self._lerp_fn(360, 0, self._set_rot)))
        group = QParallelAnimationGroup(self)
        group.addAnimation(shrink)
        group.addAnimation(spin)
        return group

    def _build_squish(self):
        """压扁：缓缓摊成饼再弹回。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(
            200, QEasingCurve.Type.InOutQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(1 + 0.22 * v, 1 - 0.26 * v))))
        seq.addAnimation(self._make(
            600, QEasingCurve.Type.OutElastic,
            self._lerp_fn(0, 1, lambda v: self._set_scale(1 + 0.22 * (1 - v),
                                                          1 - 0.26 * (1 - v)))))
        return seq

    def _build_dance(self):
        """跳舞：连续小跳并左右摇摆。"""
        from PySide6.QtCore import QParallelAnimationGroup, QSequentialAnimationGroup
        jump_h = self.pet.boundingRect().height() * self._zoom * 0.12
        self._set_jump_headroom(jump_h)
        seq = QSequentialAnimationGroup(self)
        for i in range(3):
            lean = self._make(260, QEasingCurve.Type.InOutSine,
                              self._lerp_fn(-10 if i % 2 == 0 else 10, 0, self._set_rot))
            hop = QSequentialAnimationGroup(self)
            hop.addAnimation(self._make(180, QEasingCurve.Type.OutQuad,
                                        self._lerp_fn(0, -jump_h, self._set_y)))
            hop.addAnimation(self._make(170, QEasingCurve.Type.InQuad,
                                        self._lerp_fn(-jump_h, 0, self._set_y)))
            seq.addAnimation(hop)
            seq.addAnimation(lean)
        self.spawn_hearts(2)
        return seq

    def _build_shake(self):
        """摇头：快速左右摆。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        seq = QSequentialAnimationGroup(self)
        for _ in range(3):
            seq.addAnimation(self._make(90, QEasingCurve.Type.InOutQuad,
                                        self._lerp_fn(0, 9, self._set_rot)))
            seq.addAnimation(self._make(90, QEasingCurve.Type.InOutQuad,
                                        self._lerp_fn(9, -9, self._set_rot)))
        seq.addAnimation(self._make(90, QEasingCurve.Type.InOutQuad,
                                    self._lerp_fn(-9, 0, self._set_rot)))
        return seq

    def _build_happy(self):
        """开心：小跳 + 爱心。"""
        self.spawn_hearts(5)
        return self._build_jump()

    # ---------- 爱心粒子 ----------
    def spawn_hearts(self, n: int = 3):
        """从头顶飘出爱心粒子并淡出。"""
        r = self.pet.boundingRect()
        for i in range(n):
            item = QGraphicsTextItem(random.choice(HEART_EMOJIS))
            f = QFont("Segoe UI Emoji", 14)
            item.setFont(f)
            item.setZValue(10)
            x = r.width() * self._zoom * (0.25 + 0.5 * (i + random.random()) / max(1, n))
            y = r.height() * self._zoom * 0.06    # 头顶附近（形象顶部）
            item.setPos(x - item.boundingRect().width() / 2, y)
            self.scene.addItem(item)

            rise = self._make(1200, QEasingCurve.Type.OutCubic,
                              lambda v, it=item: it.setY(y - 80 * self._zoom * v))
            fade = self._make(1200, QEasingCurve.Type.InQuad,
                              lambda v, it=item: it.setOpacity(1.0 - v))
            from PySide6.QtCore import QParallelAnimationGroup
            group = QParallelAnimationGroup(self)
            group.addAnimation(rise)
            group.addAnimation(fade)
            group.finished.connect(
                lambda it=item: (self.scene.removeItem(it), it.setParent(None)))
            group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
