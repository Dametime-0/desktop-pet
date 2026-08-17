# -*- coding: utf-8 -*-
"""对话引擎（离线部分）：

处理优先级：
1. 昵称记忆：识别「我叫/叫我/我是 XX」→ 保存到配置并确认；
2. 关键词规则（keyword_rules + easter_eggs + 记忆库自动规则）：
   命中权重最高者 → 返回彩蛋台词 + 指定动作；
3. 离线分类回复：按句子类别（问候/感谢/夸奖/疑问/安慰/告别…）从本地库随机取；
4. 以上均未命中 → default 兜底回复。

无网络时也能正常触发以上全部交互。
"""
import random
import re

NAME_PATTERNS = [
    re.compile(r"我(?:的?名字)?(?:叫|是)\s*([一-龥A-Za-z0-9_]{1,12})"),
    re.compile(r"叫我\s*([一-龥A-Za-z0-9_]{1,12})"),
]
NAME_FILLERS = {"你", "我", "他", "她", "它", "谁", "啥", "什么", "是", "叫", "magic"}

# 离线分类词表（顺序即优先级）
CLASSIFIERS = [
    ("greeting", ("你好", "您好", "嗨", "哈喽", "哈啰", "hello", "hi", "在吗",
                  "早上好", "早安", "下午好", "中午好", "晚上好", "来了", "hello~")),
    ("bye", ("再见", "拜拜", "晚安", "去睡", "走了", "下线", "睡了", "bye")),
    ("thanks", ("谢谢", "感谢", "多谢", "thanks", "thx", "辛苦")),
    ("praise", ("可爱", "真棒", "厉害", "好棒", "乖", "聪明", "喜欢你", "爱你",
                "卡哇伊", "萌", "好看", "漂亮")),
    ("comfort", ("难过", "伤心", "不开心", "累", "烦", "哭", "emo", "郁闷", "焦虑")),
    ("question", ("？", "?", "什么", "为什么", "怎么", "吗", "呢", "哪里", "多少",
                  "几点", "谁", "能不能", "可以")),
]


class DialogueEngine:
    def __init__(self, personality, settings):
        self.personality = personality
        self._settings = settings
        self._last_pick = {}          # 类别 → 上次抽中的台词下标，避免连续重复

    def set_personality(self, personality):
        self.personality = personality
        self._last_pick = {}

    # ---------- 关键词 / 记忆匹配 ----------
    def match_rule(self, text: str):
        """返回命中的最高权重规则，未命中返回 None。"""
        text_l = text.lower()
        best, best_score = None, -1
        for rule in self.personality.rules:
            if any(kw in text_l for kw in rule["keywords"]):
                if rule["weight"] > best_score:
                    best, best_score = rule, rule["weight"]
        return best

    def _pick(self, lines):
        """随机抽取一条，避免与上次重复。"""
        if not lines:
            return None
        if len(lines) == 1:
            return lines[0]
        i = random.randrange(len(lines))
        if i == self._last_pick.get(id(lines)):
            i = (i + 1) % len(lines)
        self._last_pick[id(lines)] = i
        return lines[i]

    # ---------- 本地对话入口 ----------
    def handle_local(self, text: str):
        """本地处理输入，返回 (reply, action) 或 None（表示应交给大模型）。"""
        # 1) 昵称记忆
        for pat in NAME_PATTERNS:
            m = pat.search(text)
            if m:
                name = m.group(1).strip()
                if name and name not in NAME_FILLERS:
                    self._settings.set("chat.user_name", name)
                    self._settings.save()
                    return f"记住啦！以后我就叫你「{name}」～", "happy"
        # 2) 关键词彩蛋（含记忆库）
        rule = self.match_rule(text)
        if rule:
            return self._pick(rule["replies"]), rule["action"] or None
        # 3) 离线分类库
        return self.classify_reply(text), None

    def classify(self, text: str) -> str:
        """按词表判断句子类别，返回类别名。"""
        text_l = text.lower()
        for category, words in CLASSIFIERS:
            if any(w in text_l for w in words):
                return category
        return "default"

    def classify_reply(self, text: str) -> str:
        """按类别从离线库取回复（优先人格配置，缺类则 default）。"""
        category = self.classify(text)
        if self.personality.has_offline_category(category):
            return self._pick(self.personality.offline.get(category))
        return self._pick(self.personality.offline.get("default") or ["……"])

    def idle_line(self):
        """空闲自言自语。"""
        if self.personality.has_offline_category("idle"):
            return self._pick(self.personality.offline.get("idle"))
        return self._pick(self.personality.offline.get("default") or ["……"])
