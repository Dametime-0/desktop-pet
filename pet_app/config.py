# -*- coding: utf-8 -*-
"""全局配置管理。

- 默认配置随包分发于 config/settings.json
- 用户可创建 config/settings.local.json 覆盖主配置（建议把 API Key 放在这里，
  该文件已被 .gitignore 忽略，不会被提交到 GitHub）
"""
import copy
import json
import os

from .utils import config_dir, log

DEFAULT_SETTINGS = {
    "window": {
        "base_height": 240,      # 宠物基准显示高度（像素）
        "scale": 1.0,            # 用户缩放倍率（滚轮调节）
        "x": -1, "y": -1,        # 上次窗口位置，-1 表示未记录
        "always_on_top": True,
    },
    "bubble": {
        "bg_color": "#FFFFFF",
        "border_color": "#F5B8C8",
        "text_color": "#4A4038",
        "font_family": "Microsoft YaHei",
        "font_size": 14,
        "corner_radius": 16,
        "opacity": 0.94,
        "chars_per_second": 20,          # 逐字显示速度
        "min_duration_ms": 2600,         # 最短停留时间
        "duration_per_char_ms": 95,      # 按字数追加停留时间
        "max_width": 280,
        "show_tail": True,
    },
    "llm": {
        "enabled": True,
        "base_url": "https://api.openai.com/v1",   # OpenAI 兼容接口
        "api_key": "",                             # 留空则始终离线
        "model": "gpt-4o-mini",
        "timeout": 15,
        "max_tokens": 200,
        "temperature": 0.9,
    },
    "chat": {
        "history_max_turns": 12,   # 发送给大模型的最近对话轮数
        "user_name": "",           # 用户昵称（告诉它"我叫XX"可自动记忆）
    },
    "behavior": {
        "auto_idle": True,         # 宠物空闲时自言自语/小动作
        "idle_min_s": 18,
        "idle_max_s": 45,
    },
    "matting": {
        "method": "auto",          # auto=优先rembg，不可用则内置flood-fill；floodfill=强制内置算法
        "tolerance": 32,           # 背景容差，抠图不干净可调大/调小
    },
    "active_personality": "default",
}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个字典，override 覆盖 base（不新增顶层键以外的结构）。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Settings:
    """带点路径访问的配置对象，修改后调用 save() 持久化。"""

    def __init__(self):
        self.data = {}
        self.load()

    @property
    def main_path(self) -> str:
        return os.path.join(config_dir, "settings.json")

    @property
    def local_path(self) -> str:
        return os.path.join(config_dir, "settings.local.json")

    def load(self):
        data = copy.deepcopy(DEFAULT_SETTINGS)
        for path in (self.main_path, self.local_path):
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = _deep_merge(data, json.load(f))
                except (OSError, json.JSONDecodeError) as e:
                    log.warning("配置读取失败 %s: %s", path, e)
        self.data = data
        # 主配置文件被删除时自动重建，保证程序可运行
        if not os.path.isfile(self.main_path):
            self.save()

    def save(self):
        try:
            os.makedirs(config_dir, exist_ok=True)
            with open(self.main_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.warning("配置保存失败: %s", e)

    def get(self, dotted: str, default=None):
        """按点路径取值，如 get("bubble.font_size")。"""
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value):
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
