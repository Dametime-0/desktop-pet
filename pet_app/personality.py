# -*- coding: utf-8 -*-
"""人格包管理：加载、导入（zip）、导出（zip）。

人格包目录结构（personalities/<人格名>/personality.json）：
    {
      "name": "小晴", "version": "1.0.0", "author": "...",
      "personality": { "tone": ..., "speech_style": ..., "catchphrases": [...], "background": ... },
      "memory": { "关键词": {"fact": "记忆内容", "trigger": true/false}, ... },
      "keyword_rules": [ {"keywords": [...], "replies": [...], "action": "...", "weight": N} ],
      "easter_eggs":  [ 同 keyword_rules 结构，彩蛋台词  ],
      "offline_replies": { "greeting": [...], "pat": [...], "default": [...], ... }
    }

用户只需修改 JSON 即可定制人格，无需改代码。
导出的人格包为 zip，除 personality.json 外可携带当前形象图片（形象/pet.png）。
"""
import json
import os
import re
import shutil
import zipfile

from .utils import BUNDLED_PERSONALITY_DIR, assets_dir, log, personality_dir

#: 记忆库中这些常见词默认不作为触发关键词（避免几乎每句话都命中）
MEMORY_STOPWORDS = {"我", "你", "他", "她", "它", "的", "了", "呢", "吗", "我们", "你们"}

#: 与人物相关的称呼（用于提示词，不用于触发）
ACTION_NAMES = ("pat", "bounce", "jump", "spin", "squish", "dance", "shake", "happy")


class PersonalityError(Exception):
    """人格配置不合法。"""


class Personality:
    """已加载的人格对象，提供便捷属性。"""

    def __init__(self, data: dict, dir_path: str):
        self.data = data
        self.dir_path = dir_path
        self.name = data.get("name", "桌宠")
        self.version = data.get("version", "1.0.0")
        self.author = data.get("author", "")
        p = data.get("personality", {})
        self.tone = p.get("tone", "")
        self.speech_style = p.get("speech_style", "")
        self.catchphrases = list(p.get("catchphrases", []))
        self.background = p.get("background", "")
        self.memory = data.get("memory", {}) or {}
        # 关键词规则 = keyword_rules + easter_eggs + 记忆库自动规则
        self.rules = []
        for rule in data.get("keyword_rules", []) + data.get("easter_eggs", []):
            self.rules.append({
                "keywords": [str(k).lower() for k in rule.get("keywords", [])],
                "replies": list(rule.get("replies", [])) or ["……"],
                "action": rule.get("action", "") if rule.get("action") in ACTION_NAMES else "",
                "weight": int(rule.get("weight", 5)),
            })
        for key, value in self.memory.items():
            if str(key).startswith("_"):        # 跳过 "_comment" 等说明字段
                continue
            entry = value if isinstance(value, dict) else {"fact": str(value)}
            if entry.get("trigger", len(key) >= 2 and key not in MEMORY_STOPWORDS):
                fact = entry.get("fact", "")
                reply = entry.get("reply") or f"你问起「{key}」啦？我当然记得——{fact}～"
                self.rules.append({
                    "keywords": [key.lower()],
                    "replies": [reply],
                    "action": entry.get("action", "happy")
                              if entry.get("action") in ACTION_NAMES else "happy",
                    "weight": int(entry.get("weight", 8)),
                })
        self.offline = {k: v for k, v in (data.get("offline_replies", {}) or {}).items()
                        if not str(k).startswith("_")}

    # ---------- 离线回复 ----------
    def offline_reply(self, category: str) -> str:
        lines = self.offline.get(category) or self.offline.get("default") or ["……"]
        import random
        return random.choice(lines)

    def has_offline_category(self, category: str) -> bool:
        return bool(self.offline.get(category))


def _valid_dir_name(name: str) -> str:
    """人格名转为安全的目录名。"""
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name)).strip() or "personality"
    return name[:40]


class PersonalityManager:
    def __init__(self, settings):
        self._settings = settings
        self.current = None
        self.current_dir = None
        self.load(settings.get("active_personality", "default"))

    # ---------- 加载 ----------
    @staticmethod
    def _scan_dir(root: str, name: str):
        """在某个根目录下按目录名或 personality.json 的 name 字段查找人格目录。"""
        direct = os.path.join(root, _valid_dir_name(name))
        if os.path.isdir(direct):
            return direct
        if os.path.isdir(root):
            for entry in os.listdir(root):
                d = os.path.join(root, entry)
                cfg = os.path.join(d, "personality.json")
                if os.path.isfile(cfg):
                    try:
                        with open(cfg, "r", encoding="utf-8") as f:
                            if json.load(f).get("name") == name:
                                return d
                    except (OSError, json.JSONDecodeError):
                        continue
        return None

    def _find_dir(self, name: str) -> str:
        """查找人格目录：用户目录优先，其次随包资源目录。"""
        for root in (personality_dir, BUNDLED_PERSONALITY_DIR):
            found = self._scan_dir(root, name)
            if found:
                return found
        raise PersonalityError(f"找不到人格「{name}」，请检查 personalities/ 目录")

    def load(self, name: str = None):
        """加载人格，失败时回退 default，default 也失败则用最小内置人格兜底。"""
        name = name or "default"
        try:
            d = self._find_dir(name)
            path = os.path.join(d, "personality.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.current = Personality(data, d)
            self.current_dir = d
            self._settings.set("active_personality", self.current.name)
            self._settings.save()
            log.info("已加载人格: %s (%s)", self.current.name, self.current.version)
        except (PersonalityError, OSError, json.JSONDecodeError) as e:
            log.warning("人格加载失败(%s)，使用内置兜底人格", e)
            self.current = Personality({"name": "小晴", "offline_replies": {
                "default": ["嗯？我在呢，怎么了？"]}}, "")
            self.current_dir = None
        return self.current

    def list_available(self):
        """列出全部可用人格名（用户目录 + 随包目录去重）。"""
        names, seen = [], set()
        for root in (personality_dir, BUNDLED_PERSONALITY_DIR):
            if not os.path.isdir(root):
                continue
            for entry in os.listdir(root):
                cfg = os.path.join(root, entry, "personality.json")
                if os.path.isfile(cfg):
                    try:
                        with open(cfg, "r", encoding="utf-8") as f:
                            n = json.load(f).get("name", entry)
                        if n not in seen:
                            seen.add(n)
                            names.append(n)
                    except (OSError, json.JSONDecodeError):
                        continue
        return names

    # ---------- 导出 / 导入 ----------
    def export_pack(self, zip_path: str, include_image: bool = True):
        """导出当前人格包（含说明文件，可选带当前形象）。"""
        if not self.current or not self.current_dir:
            raise PersonalityError("当前没有可用的人格可导出")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(os.path.join(self.current_dir, "personality.json"), "personality.json")
            zf.writestr("说明.txt", (
                f"人格包：{self.current.name} v{self.current.version}\n"
                f"作者：{self.current.author}\n\n"
                f"使用方法：在桌宠上右键 → 导入人格包 → 选择本压缩包。\n"
            ))
            if include_image:
                img = os.path.join(assets_dir, "pet.png")
                if os.path.isfile(img):
                    zf.write(img, "形象/pet.png")
        log.info("人格包已导出: %s", zip_path)

    def import_pack(self, zip_path: str) -> dict:
        """导入人格包，返回 {"name": 人格名, "image": 新形象路径或 None}。"""
        result = {"name": "", "image": None}
        if not zipfile.is_zipfile(zip_path):
            raise PersonalityError("不是有效的压缩包文件")
        with zipfile.ZipFile(zip_path, "r") as zf:
            # 防止 zip-slip 路径穿越
            bad = [n for n in zf.namelist()
                   if n.startswith("/") or ".." in n.split("/")]
            if bad:
                raise PersonalityError("压缩包内含有非法路径")
            # 找到 personality.json（根目录或一级子目录）
            target = next((n for n in zf.namelist()
                           if n.rstrip("/").endswith("personality.json")
                           and n.count("/") <= 1), None)
            if target is None:
                raise PersonalityError("压缩包内未找到 personality.json")
            data = json.loads(zf.read(target).decode("utf-8"))
            name = str(data.get("name") or "").strip()
            if not name:
                raise PersonalityError("personality.json 缺少 name 字段")
            result["name"] = name
            dst = os.path.join(personality_dir, _valid_dir_name(name))
            if os.path.isdir(dst):          # 同名覆盖
                shutil.rmtree(dst, ignore_errors=True)
            os.makedirs(dst, exist_ok=True)
            zf.extractall(dst)
            # 若包内附带形象，则解压后统一放回根目录
            for n in zf.namelist():
                if n.endswith("pet.png") and "形象" in n:
                    data_img = zf.read(n)
                    break
            else:
                data_img = None
        if data_img:
            img_path = os.path.join(assets_dir, "pet.png")
            os.makedirs(assets_dir, exist_ok=True)
            with open(img_path, "wb") as f:
                f.write(data_img)
            result["image"] = img_path
        log.info("人格包已导入: %s -> %s", zip_path, dst)
        return result
