# -*- coding: utf-8 -*-
"""动画控制器：双风格（卡通弹性 / 人物动作）+ 动作串行队列。

- 常驻动画（呼吸 + 摇摆）作用在 root_group 上，始终运行，幅度按风格取参；
- 动作动画作用在 pet_item 上，串行排队执行，避免抢占同一变换属性导致画面跳变；
- 锚点为底部中心（脚底），缩放/旋转都像站在地上一样。

风格说明：
- cartoon（卡通弹性）：适合团子类圆润形象，Q 弹压扁、大幅弹性回弹；
- humanoid（人物动作）：适合 Q 版立绘人物，幅度收敛，含走路/挥手/鞠躬。

动作名（人格配置 keyword_rules / easter_eggs 的 action 字段）：
    pat 摸头 | bounce 蹦跳 | jump 小跳 | spin 转圈 | squish 压扁/蹲下
    dance 跳舞 | shake 摇头 | happy 开心 | walk 走路 | wave 挥手 | bow 鞠躬
"""
import math
import random
from collections import deque

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QObject,
                            QVariantAnimation)
from PySide6.QtGui import QFont, QTransform
from PySide6.QtWidgets import QGraphicsTextItem

HEART_EMOJIS = ("❤", "💕", "✨", "🌸")
ACTIONS = ("pat", "bounce", "jump", "spin", "squish", "dance", "shake",
           "happy", "walk", "wave", "bow")
STYLES = ("cartoon", "humanoid")

#: 双风格动作参数表：时长单位 ms，幅度为比例/度，缺键时回退 cartoon
STYLE_PARAMS = {
    "cartoon": {
        "idle": {"breath_amp_y": 0.04, "breath_amp_x": 0.015, "breath_ms": 2800,
                 "sway_deg": 2.5, "sway_ms": 6200},
        "pat": {"squash_sx": 0.12, "squash_sy": 0.14, "squash_ms": 130,
                "recover_ms": 520, "wiggle_deg": 6.0, "wiggle_ms": 340,
                "hearts": 3},
        "bounce": {"crouch_sx": 0.08, "crouch_sy": 0.06, "crouch_ms": 110,
                   "jump_ratio": 0.22, "up_ms": 230, "down_ms": 200,
                   "land_sx": 0.16, "land_sy": 0.18, "land_ms": 110,
                   "recover_ms": 480},
        "jump": {"ratio": 0.14, "up_ms": 200, "down_ms": 190},
        "spin": {"ms": 650, "reset_ms": 200},
        "squish": {"sx": 0.22, "sy": 0.26, "down_ms": 200, "recover_ms": 600},
        "dance": {"hops": 3, "lean_deg": 10, "hop_ratio": 0.12,
                  "hop_up_ms": 180, "hop_down_ms": 170, "lean_ms": 260},
        "shake": {"deg": 9, "ms": 90, "rounds": 3},
        "happy": {"hearts": 5, "jump_ratio": 0.14},
        "walk": {"steps": 3, "step_ms": 290, "step_ratio": 0.35,
                 "bob_ratio": 0.03, "waddle_deg": 4.0},
        "wave": {"deg": 30, "ms": 400, "rounds": 2},
        "bow": {"down_ratio": 0.18, "tilt_deg": 25, "down_ms": 220,
                "hold_ms": 400, "back_ms": 240},
    },
    "humanoid": {
        "idle": {"breath_amp_y": 0.015, "breath_amp_x": 0.004, "breath_ms": 3200,
                 "sway_deg": 1.5, "sway_ms": 7200},
        "pat": {"tilt_deg": 5.0, "tilt_ms": 280, "jump_ratio": 0.08, "hearts": 3},
        "bounce": {"crouch_sx": 0.02, "crouch_sy": 0.03, "crouch_ms": 120,
                   "jump_ratio": 0.18, "up_ms": 240, "down_ms": 210,
                   "land_sx": 0.04, "land_sy": 0.06, "land_ms": 120,
                   "recover_ms": 380},
        "jump": {"ratio": 0.12, "up_ms": 210, "down_ms": 200},
        "spin": {"ms": 700, "reset_ms": 220},
        "squish": {"sx": 0.04, "sy": 0.05, "down_ms": 240, "recover_ms": 480},
        "dance": {"hops": 4, "lean_deg": 12, "hop_ratio": 0.10,
                  "hop_up_ms": 160, "hop_down_ms": 150, "lean_ms": 240},
        "shake": {"deg": 6, "ms": 110, "rounds": 3},
        "happy": {"hearts": 5, "jump_ratio": 0.08},
        "walk": {"steps": 3, "step_ms": 280, "step_ratio": 0.40,
                 "bob_ratio": 0.02, "waddle_deg": 3.0},
        "wave": {"deg": 25, "ms": 500, "rounds": 2},
        "bow": {"down_ratio": 0.15, "tilt_deg": 22, "down_ms": 240,
                "hold_ms": 450, "back_ms": 260},
    },
}


class AnimController(QObject):
    def __init__(self, root_group, pet_item, scene):
        super().__init__()
        self.root = root_group
        self.pet = pet_item
        self.scene = scene
        self._zoom = 1.0
        self._style = "cartoon"
        self._queue = deque()      # 待播放动作队列
        self._busy = False
        self._last_action = None
        self._current_group = None
        self._walk_cb = None       # 走路平移回调（由控制器注入 window.walk）
        self._breath = None
        self._sway = None

    # ---------- 基础工具 ----------
    def set_zoom(self, zoom: float):
        """窗口缩放倍率变化时同步（粒子、跳跃幅度需要）。"""
        self._zoom = zoom

    def set_style(self, style: str):
        """切换动画风格（cartoon 卡通弹性 / humanoid 人物动作）。"""
        if style not in STYLES:
            style = "cartoon"
        if style == self._style:
            return
        self._style = style
        if self._breath is not None:      # 常驻动画幅度随风格更新
            self.stop_idle()
            self.start_idle()

    def style(self) -> str:
        return self._style

    def set_walk_cb(self, cb):
        """注入走路平移回调：签名 cb(dx_px) -> 实际移动像素。"""
        self._walk_cb = cb

    def _params(self, action: str) -> dict:
        """取当前风格的参数表，缺键回退 cartoon。"""
        return STYLE_PARAMS.get(self._style, STYLE_PARAMS["cartoon"]).get(
            action) or STYLE_PARAMS["cartoon"].get(action) or {}

    def _make(self, duration, curve, on_update):
        """构造 0→1 缓动动画，on_update 收到缓动后的值 v（可为 None，稍后手动连接）。"""
        anim = QVariantAnimation(self)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(curve)
        if on_update is not None:
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

    def _jump_height(self, ratio: float) -> float:
        return self.pet.boundingRect().height() * self._zoom * ratio

    # ---------- 常驻待机动画 ----------
    def start_idle(self):
        """呼吸 + 轻微摇摆循环，幅度按风格取参。"""
        if self._breath is not None:
            return
        p = self._params("idle")
        # 呼吸：0→1→0 关键帧循环，scaleY/scaleX 反向，无跳变
        # 注意 QGraphicsItemGroup 只支持均匀 setScale，需用 setTransform 做非均匀缩放
        breath = QVariantAnimation(self)
        breath.setDuration(p["breath_ms"])
        breath.setStartValue(0.0)
        breath.setEndValue(0.0)
        breath.setKeyValueAt(0.5, 1.0)
        breath.setLoopCount(-1)
        breath.setEasingCurve(QEasingCurve.Type.InOutSine)
        breath.valueChanged.connect(
            lambda v: self.root.setTransform(QTransform.fromScale(
                1.0 - p["breath_amp_x"] * v, 1.0 + p["breath_amp_y"] * v)))
        self._breath = breath
        breath.start()
        # 摇摆：-deg ~ +deg
        sway = QVariantAnimation(self)
        sway.setDuration(p["sway_ms"])
        sway.setStartValue(-p["sway_deg"])
        sway.setEndValue(-p["sway_deg"])
        sway.setKeyValueAt(0.5, p["sway_deg"])
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
        self.root.setTransform(QTransform())
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

    def stop_all(self):
        """停掉全部非 idle 动画并复位（如用户开始拖动窗口时调用）。"""
        self._queue.clear()
        self._busy = False
        self._last_action = None
        if self._current_group is not None:
            self._current_group.stop()
            self._current_group = None
        self._set_scale(1, 1)
        self._set_rot(0)
        self._set_y(0)

    def _run(self, name: str):
        self._busy = True
        self._last_action = name
        builder = getattr(self, f"_build_{name}", self._build_jump)
        group = builder()
        self._current_group = group
        group.finished.connect(self._on_finished)
        group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_finished(self):
        self._busy = False
        self._current_group = None
        if self._queue:
            self._run(self._queue.popleft())

    # ---------- 动作构建 ----------
    def _build_pat(self):
        """摸头：cartoon=压扁回弹+摇摆；humanoid=点头倾斜+小跳。均冒爱心。"""
        from PySide6.QtCore import (QParallelAnimationGroup,
                                    QSequentialAnimationGroup)
        p = self._params("pat")
        if self._style == "humanoid":
            # 点头倾斜两轮 + 小跳
            tilts = QSequentialAnimationGroup(self)
            tilts.addAnimation(self._make(p["tilt_ms"], QEasingCurve.Type.InOutSine,
                                          self._lerp_fn(0, -p["tilt_deg"], self._set_rot)))
            tilts.addAnimation(self._make(p["tilt_ms"], QEasingCurve.Type.InOutSine,
                                          self._lerp_fn(-p["tilt_deg"], p["tilt_deg"], self._set_rot)))
            tilts.addAnimation(self._make(p["tilt_ms"], QEasingCurve.Type.InOutSine,
                                          self._lerp_fn(p["tilt_deg"], 0, self._set_rot)))
            group = QParallelAnimationGroup(self)
            group.addAnimation(tilts)
            group.addAnimation(self._build_jump())
            self.spawn_hearts(p.get("hearts", 3))
            return group

        squish = QSequentialAnimationGroup(self)
        squish.addAnimation(self._make(
            p["squash_ms"], QEasingCurve.Type.InOutQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(
                1 + p["squash_sx"] * v, 1 - p["squash_sy"] * v))))
        squish.addAnimation(self._make(
            p["recover_ms"], QEasingCurve.Type.OutElastic,
            self._lerp_fn(0, 1, lambda v: self._set_scale(
                1 + p["squash_sx"] * (1 - v), 1 - p["squash_sy"] * (1 - v)))))

        wiggle_group = QSequentialAnimationGroup(self)
        wiggle_group.addAnimation(self._make(
            p["wiggle_ms"], QEasingCurve.Type.InOutSine,
            self._lerp_fn(-p["wiggle_deg"], p["wiggle_deg"], self._set_rot)))
        wiggle_group.addAnimation(self._make(
            p["wiggle_ms"], QEasingCurve.Type.InOutSine,
            self._lerp_fn(p["wiggle_deg"], 0.0, self._set_rot)))

        group = QParallelAnimationGroup(self)
        group.addAnimation(squish)
        group.addAnimation(wiggle_group)
        self.spawn_hearts(p.get("hearts", 3))
        return group

    def _build_bounce(self):
        """蹦跳：蓄力下蹲 → 跳起 → 落地 → 落地缓冲回弹（humanoid 幅度收敛）。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        p = self._params("bounce")
        jump_h = self._jump_height(p["jump_ratio"])
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(
            p["crouch_ms"], QEasingCurve.Type.InQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(
                1 + p["crouch_sx"] * v, 1 - p["crouch_sy"] * v))))
        seq.addAnimation(self._make(p["up_ms"], QEasingCurve.Type.OutQuad,
                                    self._lerp_fn(0, -jump_h, self._set_y)))
        seq.addAnimation(self._make(p["down_ms"], QEasingCurve.Type.InQuad,
                                    self._lerp_fn(-jump_h, 0, self._set_y)))
        land = QSequentialAnimationGroup(self)
        land.addAnimation(self._make(
            p["land_ms"], QEasingCurve.Type.InQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(
                1 + p["land_sx"] * v, 1 - p["land_sy"] * v))))
        land.addAnimation(self._make(
            p["recover_ms"], QEasingCurve.Type.OutElastic,
            self._lerp_fn(0, 1, lambda v: self._set_scale(
                1 + p["land_sx"] * (1 - v), 1 - p["land_sy"] * (1 - v)))))
        seq.addAnimation(land)
        return seq

    def _build_jump(self):
        """小跳（彩蛋动作的通用跳跃）。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        p = self._params("jump")
        jump_h = self._jump_height(p["ratio"])
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(p["up_ms"], QEasingCurve.Type.OutQuad,
                                    self._lerp_fn(0, -jump_h, self._set_y)))
        seq.addAnimation(self._make(p["down_ms"], QEasingCurve.Type.InQuad,
                                    self._lerp_fn(-jump_h, 0, self._set_y)))
        return seq

    def _build_spin(self):
        """转圈：绕底部中心旋转 360°。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        p = self._params("spin")
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(p["ms"], QEasingCurve.Type.OutCubic,
                                    self._lerp_fn(0, 360, self._set_rot)))
        seq.addAnimation(self._make(p["reset_ms"], QEasingCurve.Type.InOutSine,
                                    self._lerp_fn(360, 0, self._set_rot)))
        return seq

    def _build_squish(self):
        """压扁（cartoon）/ 蹲下（humanoid）：缓缓压缩再弹回。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        p = self._params("squish")
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(self._make(
            p["down_ms"], QEasingCurve.Type.InOutQuad,
            self._lerp_fn(0, 1, lambda v: self._set_scale(
                1 + p["sx"] * v, 1 - p["sy"] * v))))
        seq.addAnimation(self._make(
            p["recover_ms"], QEasingCurve.Type.OutElastic,
            self._lerp_fn(0, 1, lambda v: self._set_scale(
                1 + p["sx"] * (1 - v), 1 - p["sy"] * (1 - v)))))
        return seq

    def _build_dance(self):
        """跳舞：连续小跳并左右摇摆。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        p = self._params("dance")
        jump_h = self._jump_height(p["hop_ratio"])
        seq = QSequentialAnimationGroup(self)
        for i in range(p["hops"]):
            lean = self._make(p["lean_ms"], QEasingCurve.Type.InOutSine,
                              self._lerp_fn(-p["lean_deg"] if i % 2 == 0
                                            else p["lean_deg"], 0, self._set_rot))
            hop = QSequentialAnimationGroup(self)
            hop.addAnimation(self._make(p["hop_up_ms"], QEasingCurve.Type.OutQuad,
                                        self._lerp_fn(0, -jump_h, self._set_y)))
            hop.addAnimation(self._make(p["hop_down_ms"], QEasingCurve.Type.InQuad,
                                        self._lerp_fn(-jump_h, 0, self._set_y)))
            seq.addAnimation(hop)
            seq.addAnimation(lean)
        self.spawn_hearts(2)
        return seq

    def _build_shake(self):
        """摇头：快速左右摆。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        p = self._params("shake")
        seq = QSequentialAnimationGroup(self)
        for _ in range(p["rounds"]):
            seq.addAnimation(self._make(p["ms"], QEasingCurve.Type.InOutQuad,
                                        self._lerp_fn(0, p["deg"], self._set_rot)))
            seq.addAnimation(self._make(p["ms"], QEasingCurve.Type.InOutQuad,
                                        self._lerp_fn(p["deg"], -p["deg"], self._set_rot)))
        seq.addAnimation(self._make(p["ms"], QEasingCurve.Type.InOutQuad,
                                    self._lerp_fn(-p["deg"], 0, self._set_rot)))
        return seq

    def _build_happy(self):
        """开心：小跳 + 爱心。"""
        p = self._params("happy")
        self.spawn_hearts(p.get("hearts", 5))
        return self._build_jump()

    def _build_walk(self):
        """走路：宠物真实水平移动（walk_cb），伴迈步起伏与左右摇摆。

        每步按缓动增量回调平移；顶到屏幕边界（回调返回 0）时提前结束。
        """
        from PySide6.QtCore import QSequentialAnimationGroup
        p = self._params("walk")
        h = self.pet.boundingRect().height() * self._zoom
        step_px = h * p["step_ratio"]
        direction = random.choice((-1, 1))
        blocked = [False]
        seq = QSequentialAnimationGroup(self)
        for _ in range(p["steps"]):
            anim = self._make(p["step_ms"], QEasingCurve.Type.InOutSine, None)
            prev = [0.0]

            def on_step(v, _anim=anim, _prev=prev):
                if blocked[0]:
                    _anim.stop()
                    return
                delta = v - _prev[0]
                _prev[0] = v
                self._set_y(-p["bob_ratio"] * h * math.sin(math.pi * v))
                self._set_rot(p["waddle_deg"] * math.sin(2 * math.pi * v))
                if self._walk_cb is not None:
                    if self._walk_cb(direction * step_px * delta) == 0 and v > 0.05:
                        blocked[0] = True       # 顶到屏幕边界，后续步直接结束

            anim.valueChanged.connect(on_step)
            anim.finished.connect(lambda: (self._set_y(0), self._set_rot(0)))
            seq.addAnimation(anim)
        return seq

    def _build_wave(self):
        """挥手：钟摆式摇晃身体。"""
        from PySide6.QtCore import QSequentialAnimationGroup
        p = self._params("wave")
        seq = QSequentialAnimationGroup(self)
        last = 0.0
        for i in range(p["rounds"]):
            start = -p["deg"] if i % 2 == 0 else p["deg"]
            seq.addAnimation(self._make(p["ms"], QEasingCurve.Type.InOutSine,
                                        self._lerp_fn(start, -start, self._set_rot)))
            last = -start
        seq.addAnimation(self._make(p["ms"], QEasingCurve.Type.InOutSine,
                                    self._lerp_fn(last, 0.0, self._set_rot)))
        return seq

    def _build_bow(self):
        """鞠躬：绕脚踝前倾 + 整体下移，保持后回程。"""
        from PySide6.QtCore import (QParallelAnimationGroup,
                                    QSequentialAnimationGroup)
        p = self._params("bow")
        down = self._jump_height(p["down_ratio"])
        seq = QSequentialAnimationGroup(self)
        down_group = QParallelAnimationGroup(self)
        down_group.addAnimation(self._make(
            p["down_ms"], QEasingCurve.Type.InOutQuad,
            self._lerp_fn(0, p["tilt_deg"], self._set_rot)))
        down_group.addAnimation(self._make(
            p["down_ms"], QEasingCurve.Type.InOutQuad,
            self._lerp_fn(0, down, self._set_y)))
        seq.addAnimation(down_group)
        seq.addAnimation(self._make(
            p["hold_ms"], QEasingCurve.Type.Linear, lambda v: None))
        back_group = QParallelAnimationGroup(self)
        back_group.addAnimation(self._make(
            p["back_ms"], QEasingCurve.Type.OutCubic,
            self._lerp_fn(p["tilt_deg"], 0, self._set_rot)))
        back_group.addAnimation(self._make(
            p["back_ms"], QEasingCurve.Type.OutCubic,
            self._lerp_fn(down, 0, self._set_y)))
        seq.addAnimation(back_group)
        return seq

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
