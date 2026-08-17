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
import random
from collections import deque

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QObject, Qt,
                            QVariantAnimation)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QGraphicsTextItem

HEART_EMOJIS = ("❤", "💕", "✨", "🌸")
ACTIONS = ("pat", "bounce", "jump", "spin", "squish", "dance", "shake", "happy")


class AnimController(QObject):
    def __init__(self, root_group, pet_item, scene):
        super().__init__()
        self.root = root_group
        self.pet = pet_item
        self.scene = scene
        self._zoom = 1.0
        self._queue = deque()      # 待播放动作队列
        self._busy = False
        self._last_action = None
        self._breath = None
        self._sway = None

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
        z = self._zoom
        self.pet.setScale(z * sx, z * sy)

    def _set_rot(self, deg):
        self.pet.setRotation(deg)

    def _set_y(self, y):
        self.pet.setY(y)

    # ---------- 常驻待机动画 ----------
    def start_idle(self):
        """呼吸（2.8s 循环）+ 轻微摇摆（6.2s 循环），永远运行。"""
        if self._breath is not None:
            return
        # 呼吸：0→1→0 关键帧循环，scaleY 1.0→1.04→1.0，scaleX 反向，无跳变
        breath = QVariantAnimation(self)
        breath.setDuration(2800)
        breath.setStartValue(0.0)
        breath.setEndValue(0.0)
        breath.setKeyValueAt(0.5, 1.0)
        breath.setLoopCount(-1)
        breath.setEasingCurve(QEasingCurve.Type.InOutSine)
        breath.valueChanged.connect(
            lambda v: self.root.setScale(1.0 - 0.015 * v, 1.0 + 0.04 * v))
        self._breath = breath
        breath.start()
        # 摇摆：-2.5° ~ +2.5°
        sway = QVariantAnimation(self)
        sway.setDuration(6200)
        sway.setStartValue(-2.5)
        sway.setEndValue(-2.5)
        sway.setKeyValueAt(0.5, 2.5)
        sway.setLoopCount(-1)
        sway.setEasingCurve(QEasingCurve.Type.InOutSine)
        sway.valueChanged.connect(self.root.setRotation)
        self._sway = sway
        sway.start()

    def stop_idle(self):
        for anim in (self._breath, self._sway):
            if anim is not None:
                anim.stop()
        self._breath = self._sway = None
        self.root.setScale(1.0, 1.0)
        self.root.setRotation(0.0)

    # ---------- 动作播放（串行队列） ----------
    def play(self, name: str):
        """请求播放动作。若正在播放则入队（最多保留 2 个），过度请求会被丢弃。"""
        if name not in ACTIONS:
            name = "jump"
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
        from PySide6.QtCore import QSequentialAnimationGroup
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(200, QEasingCurve.Type.OutQuad,
                                    self._lerp_fn(0, -jump_h, self._set_y)))
        seq.addAnimation(self._make(190, QEasingCurve.Type.InQuad,
                                    self._lerp_fn(-jump_h, 0, self._set_y)))
        return seq

    def _build_spin(self):
        """转圈：绕底部中心旋转 360°。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(650, QEasingCurve.Type.OutCubic,
                                    self._lerp_fn(0, 360, self._set_rot)))
        seq.addAnimation(self._make(200, QEasingCurve.Type.InOutSine,
                                    self._lerp_fn(360, 0, self._set_rot)))
        return seq

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
            y = r.height() * self._zoom * 0.08
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
