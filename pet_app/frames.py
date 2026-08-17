# -*- coding: utf-8 -*-
"""帧动画素材管理。

素材目录约定（assets/animations/<动作>/frame_0.png, frame_1.png ...）：
    idle   待机（循环播放）
    walk   走路（循环播放，程序按行走方向水平翻转）
    jump   跳跃（单次）
    pat    摸头（单次）
    happy  开心（单次）
    spin / dance / shake 等其他动作同样支持

有帧素材的动作优先播放帧动画；没有帧素材的动作回退到程序化变换动画。
素材可通过 scripts/generate_frames.py（AI 补帧）或
scripts/video_to_frames.py（本地视频转帧）生成。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap, QTransform

from .utils import assets_dir, log

#: 帧动画支持的动作名
FRAME_ACTIONS = ("idle", "walk", "jump", "pat", "happy", "spin", "dance",
                 "shake", "squish", "bounce")

#: 循环衔接的交叉淡化过渡帧数（消除"动作没播完就跳回开头"的断裂感）
TRANSITION_FRAMES = 3


class FrameSet:
    """某个动作的帧序列（懒加载，QPixmap 按需生成）。"""

    def __init__(self, action: str, paths):
        self.action = action
        self.paths = paths
        self._pixmaps = None
        self._mirrored = None
        self._base_size = None

    def __len__(self):
        return len(self.paths)

    def pixmaps(self, base_size=None, mirrored: bool = False):
        """返回 QPixmap 列表；统一缩放到 base_size，mirrored 时水平翻转。

        翻转用于走路方向（素材通常只有朝一个方向的步态）。
        """
        if (self._pixmaps is None or self._base_size != base_size
                or self._mirrored != mirrored):
            pmaps = []
            for p in self.paths:
                pm = QPixmap(p)
                if pm.isNull():
                    continue
                if base_size and pm.size() != base_size:
                    pm = pm.scaled(base_size.width(), base_size.height(),
                                   aspectMode=Qt.AspectRatioMode.IgnoreAspectRatio,
                                   mode=Qt.TransformationMode.SmoothTransformation)
                if mirrored:
                    pm = pm.transformed(QTransform().scale(-1, 1))
                pmaps.append(pm)
            self._pixmaps = pmaps
            self._base_size = base_size
            self._mirrored = mirrored
        return self._pixmaps

    def transitions(self, base_size=None, mirrored: bool = False):
        """循环衔接过渡帧：末帧与首帧的交叉淡化序列。

        AI 生成的循环视频首尾并不完全衔接，直接跳回开头会有断裂感；
        用 3 帧淡入淡出过渡把末帧平滑引回首帧。
        """
        pmaps = self.pixmaps(base_size, mirrored)
        if len(pmaps) < 2:
            return []
        last, first = pmaps[-1], pmaps[0]
        result = []
        for k in range(1, TRANSITION_FRAMES + 1):
            t = k / (TRANSITION_FRAMES + 1)
            out = QPixmap(last.size())
            out.fill(Qt.GlobalColor.transparent)
            p = QPainter(out)
            p.setOpacity(1.0 - t)
            p.drawPixmap(0, 0, last)
            p.setOpacity(t)
            p.drawPixmap(0, 0, first)
            p.end()
            result.append(out)
        return result


class FrameLibrary:
    """全部动作帧素材的集合与查询。"""

    def __init__(self, root: str = None):
        #: 帧素材根目录（测试可传入临时目录，绝不触碰真实素材）
        self.root = root or os.path.join(assets_dir, "animations")
        self._sets = {}
        self.scan()

    def scan(self):
        """扫描帧素材根目录，重建帧索引。

        仅识别动作目录（如 idle/walk/jump）；下划线开头的目录
        （如 _raw 原片存档）自动跳过。
        """
        self._sets = {}
        root = self.root
        if not os.path.isdir(root):
            return
        for action in os.listdir(root):
            if action.startswith("_"):          # _raw 等内部目录不作为动作
                continue
            d = os.path.join(root, action)
            if not os.path.isdir(d):
                continue
            paths = sorted(
                os.path.join(d, f) for f in os.listdir(d)
                if f.lower().endswith((".png", ".jpg", ".jpeg")))
            if paths:
                self._sets[action] = FrameSet(action, paths)
        if self._sets:
            log.info("帧动画素材: %s", ", ".join(
                f"{k}({len(v)})" for k, v in sorted(self._sets.items())))

    def get(self, action: str):
        """返回动作的 FrameSet，无素材返回 None。"""
        return self._sets.get(action)

    def has(self, action: str) -> bool:
        return action in self._sets
