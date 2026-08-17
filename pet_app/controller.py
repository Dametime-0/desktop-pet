# -*- coding: utf-8 -*-
"""总控：组装窗口/动画/气泡/聊天，负责所有交互接线。

交互规则：
- 左键点击宠物上半身 → 摸头动画 + 泡泡，下半身 → 蹦跳动画 + 泡泡；
  同时打开对话面板并聚焦输入框；
- 左键拖动移动位置，滚轮缩放，右键弹出菜单；
- 空闲时随机自言自语/小动作（behavior.auto_idle）。
"""
import math
import os
import random
import time

from PySide6.QtCore import QEasingCurve, QObject, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QMenu

from . import llm_client, matting
from .animations import AnimController
from .assets import ensure_icon, ensure_pet_image
from .bubble import BubbleWindow
from .chat_panel import ChatPanel, ChatWorker
from .dialogue import DialogueEngine
from .personality import PersonalityError, PersonalityManager
from .utils import personality_dir, log

HEAD_ZONE = 0.45            # 上半部分视为头部（摸头）
SCALE_PRESETS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


class PetController(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = self._load_settings()
        self.persona = PersonalityManager(self.settings)
        self.dialogue = DialogueEngine(self.persona.current, self.settings)
        self.history = []                     # [{"role","content"}] 大模型上下文
        self._workers = []                    # 防止线程被回收
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self._save_state)
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle)
        # 走路状态（窗口平移动画 + 颠簸定时器）
        self._walk_anim = None
        self._walk_bob_timer = QTimer(self)
        self._walk_bob_timer.setInterval(30)
        self._walk_bob_timer.timeout.connect(self._on_walk_bob)
        self._bob_t = 0.0
        # 定时提醒（喝水/活动/休息），每分钟检查一次
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(60_000)
        self._reminder_timer.timeout.connect(self._check_reminders)
        self._reminder_due = {}
        self._reset_reminder_due()

        # 窗口与场景
        from .window import PetWindow
        self.window = PetWindow(self.settings)
        self.anims = AnimController(self.window.root_group,
                                    self.window.pet_item, self.window.scene())
        topmost = self.settings.get("window.always_on_top", True)
        self.bubble = BubbleWindow(self.settings.get("bubble") or {}, topmost)
        self.chat = ChatPanel(self.settings, self.persona.current.name,
                              self.handle_chat, topmost)

        # 形象加载（缺失时从随包资源复制）
        pet_path = ensure_pet_image()
        if not self.window.load_image(pet_path):
            log.error("形象加载失败: %s", pet_path)
        self.anims.set_zoom(self.window._fit_scale * self.window.user_scale())

        # 信号接线
        self.window.clicked.connect(self._on_click)
        self.window.context_menu_requested.connect(self._show_menu)
        self.window.dropped_image.connect(self._import_image)
        self.window.geometry_changed.connect(self._on_geometry_changed)
        self.window.interaction_started.connect(self._on_interaction)

        # 启动
        self.anims.start_idle()
        self.window.restore_position()
        self.window.show()
        self._schedule_idle()
        self._reminder_timer.start()

    # ---------- 初始化辅助 ----------
    @staticmethod
    def _load_settings():
        from .config import Settings
        return Settings()

    def system_prompt(self) -> str:
        return llm_client.build_system_prompt(
            self.persona.current, self.settings.get("chat.user_name", ""))

    # ---------- 宠物点击 ----------
    def _on_click(self, fx: float, fy: float):
        """点击：上半身摸头 / 下半身蹦跳，并打开对话面板。"""
        self._stop_walk()          # 点击打断走路
        if fy < HEAD_ZONE:
            self.anims.play("pat")
            line = self.dialogue.personality.offline_reply("pat")
        else:
            self.anims.play("bounce")
            line = self.dialogue.personality.offline_reply("jump")
        self.chat.open_near(self.window.frameGeometry())
        # 气泡避让聊天面板，避免出现"气泡跑到面板另一侧"的错位
        self.bubble.show_text(line)
        self.bubble.follow(self.window.frameGeometry(),
                           self._chat_rect_if_visible())

    # ---------- 聊天 ----------
    def handle_chat(self, text: str):
        """聊天入口：关键词彩蛋优先，其次大模型，最后离线库。"""
        self._append_history("user", text)

        local = self.dialogue.handle_local(text)
        if local:                                  # 关键词/记忆/昵称 → 立即回复
            reply, action = local
            self.chat.append_pet(reply)
            self._append_history("assistant", reply)
            if action:
                self.play_action(action)
            return

        cfg = self.settings.get("llm") or {}
        if cfg.get("enabled") and cfg.get("api_key"):
            self.chat.set_thinking(True)
            messages = [{"role": "system", "content": self.system_prompt()}]
            turns = int(self.settings.get("chat.history_max_turns", 12))
            messages += self.history[-2 * turns:]
            worker = ChatWorker(llm_client.chat_completion, (
                cfg.get("base_url"), cfg.get("api_key"), cfg.get("model"),
                messages, int(cfg.get("timeout", 15)),
                int(cfg.get("max_tokens", 200)), float(cfg.get("temperature", 0.9))))
            worker.done.connect(self._on_llm_done)
            worker.start()
            self._workers.append(worker)
        else:                                      # 未配置 API → 离线回复
            reply = self.dialogue.classify_reply(text)
            self.chat.append_pet(reply)
            self._append_history("assistant", reply)
            self.chat.set_mode("离线", "#9AA0A6")

    def play_action(self, action: str):
        """执行动作：walk 为小段散步，其余交给动画队列。"""
        if action == "walk":
            self._start_walk(short=True)
        elif action:
            self.anims.play(action)

    def _on_llm_done(self, result: dict):
        self.chat.set_thinking(False)
        if result.get("ok"):
            reply = result["text"]
            self.chat.append_pet(reply)
            self._append_history("assistant", reply)
            self.chat.set_mode("在线", "#4CAF50")
            if random.random() < 0.3:              # 偶尔配个小动作
                self.anims.play(random.choice(("jump", "happy")))
        else:
            if not result.get("offline"):          # 配置类错误（如 Key 无效）
                self.chat.set_mode("配置错误", "#E57373")
                self.chat.append_note(f"⚠ 大模型调用失败：{result.get('error', '')}，"
                                      f"请检查 config/settings.json 的 llm 配置")
            else:
                self.chat.set_mode("离线", "#9AA0A6")
            user_text = self.history[-1]["content"] if self.history else ""
            reply = self.dialogue.classify_reply(user_text)
            self.chat.append_pet(reply)
            self._append_history("assistant", reply)

    def _append_history(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        turns = int(self.settings.get("chat.history_max_turns", 12))
        if len(self.history) > 2 * turns + 4:
            self.history = self.history[-2 * turns:]

    # ---------- 右键菜单 ----------
    def _show_menu(self, global_pos):
        menu = QMenu()
        menu.setStyleSheet("QMenu { background: #FFF8FA; border: 1px solid #F0D9E0;"
                           "padding: 4px; } QMenu::item { padding: 6px 24px; }"
                           "QMenu::item:selected { background: #FCE4EC; }")

        size_menu = menu.addMenu("大小调整")
        cur = self.window.user_scale()
        for pct in SCALE_PRESETS:
            act = size_menu.addAction(f"{int(pct * 100)}%"
                                      + ("（当前）" if abs(pct - cur) < 0.01 else ""))
            act.triggered.connect(lambda checked=False, p=pct: self._set_scale(p))
        size_menu.addSeparator()
        act = size_menu.addAction("微调放大 10%")
        act.triggered.connect(lambda: self._set_scale(cur * 1.1))
        act = size_menu.addAction("微调缩小 10%")
        act.triggered.connect(lambda: self._set_scale(cur / 1.1))

        menu.addSeparator()
        top_act = menu.addAction("置顶显示")
        top_act.setCheckable(True)
        top_act.setChecked(self.settings.get("window.always_on_top", True))
        top_act.triggered.connect(self._toggle_topmost)
        menu.addAction("打开对话面板", lambda: self.chat.open_near(
            self.window.frameGeometry()))
        menu.addAction("散步", self._start_walk)
        # 提醒子菜单
        rem_menu = menu.addMenu("提醒")
        rem = self.settings.get("reminders") or {}
        master = rem_menu.addAction("启用提醒")
        master.setCheckable(True)
        master.setChecked(rem.get("enabled", True))
        master.triggered.connect(self._toggle_reminders)
        rem_menu.addSeparator()
        for key, label in (("drink", "喝水提醒"), ("move", "活动提醒"),
                           ("rest", "休息提醒")):
            act = rem_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked((rem.get(key) or {}).get("enabled", True))
            act.triggered.connect(lambda checked, k=key: self._toggle_reminder(k, checked))
        menu.addSeparator()
        menu.addAction("更换形象…", self._choose_image)
        menu.addAction("导入人格包…", self._choose_import_pack)
        menu.addAction("导出人格包…", self._choose_export_pack)
        menu.addAction("打开人格文件夹", self._open_personality_dir)
        menu.addSeparator()
        menu.addAction("退出程序", self.quit)
        menu.exec(global_pos)

    def _set_scale(self, scale: float):
        self.window.set_user_scale(scale)
        self.anims.set_zoom(self.window._fit_scale * self.window.user_scale())
        self._schedule_save()

    def _toggle_topmost(self, on: bool):
        self.settings.set("window.always_on_top", on)
        self.window.set_topmost(on)
        self.bubble.set_topmost(on)
        self.chat.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if self.chat.isVisible():
            self.chat.show()
        self._schedule_save()

    # ---------- 形象 ----------
    def _choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "选择形象图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*.*)")
        if path:
            self._import_image(path)

    def _import_image(self, path: str):
        try:
            cfg = self.settings.get("matting") or {}
            dst = matting.process_image(path, cfg.get("method", "auto"),
                                        int(cfg.get("tolerance", 32)))
            self.window.load_image(dst)
            self.anims.set_zoom(self.window._fit_scale * self.window.user_scale())
            self.bubble.show_text("新形象已经加载好了，我很喜欢")
        except Exception as e:                    # noqa: BLE001 导入失败不能崩溃
            log.error("形象导入失败: %s", e)
            self.bubble.show_text("图片处理失败，请换一张图片试试")

    # ---------- 人格包 ----------
    def _choose_import_pack(self):
        path, _ = QFileDialog.getOpenFileName(None, "导入人格包", "",
                                              "人格包 (*.zip);;所有文件 (*.*)")
        if path:
            self._import_pack(path)

    def _import_pack(self, path: str):
        try:
            result = self.persona.import_pack(path)
            self._reload_persona(result["name"])
            if result.get("image"):
                self.window.load_image(result["image"])
                self.anims.set_zoom(self.window._fit_scale * self.window.user_scale())
            self.bubble.show_text(f"人格「{result['name']}」已加载")
            self.chat._title.setText(f"💬 {result['name']}")
        except (PersonalityError, OSError, ValueError) as e:
            log.warning("人格包导入失败: %s", e)
            self.bubble.show_text(f"人格包导入失败：{e}")

    def _choose_export_pack(self):
        name = self.persona.current.name if self.persona.current else "personality"
        default = os.path.join(os.path.expanduser("~"), "Desktop",
                               f"{name}_人格包.zip")
        path, _ = QFileDialog.getSaveFileName(None, "导出人格包", default,
                                              "人格包 (*.zip)")
        if path:
            try:
                self.persona.export_pack(path)
                self.bubble.show_text("人格包导出成功，可以和朋友分享了")
            except (PersonalityError, OSError) as e:
                self.bubble.show_text(f"导出失败：{e}")

    def _open_personality_dir(self):
        target = self.persona.current_dir or personality_dir
        try:
            os.startfile(target)
        except OSError:
            self.bubble.show_text("无法打开文件夹")

    def _reload_persona(self, name: str):
        self.persona.load(name)
        self.dialogue.set_personality(self.persona.current)
        self.history = []                          # 换人格后清空对话上下文

    # ---------- 空闲行为 ----------
    def _schedule_idle(self):
        lo = max(5, int(self.settings.get("behavior.idle_min_s", 18)))
        hi = max(lo + 1, int(self.settings.get("behavior.idle_max_s", 45)))
        self._idle_timer.start(random.randint(lo, hi) * 1000)

    def _on_idle(self):
        if self.settings.get("behavior.auto_idle", True):
            r = random.random()
            if r < 0.3:
                self.bubble.show_text(self.dialogue.idle_line())
            elif (r < 0.5
                  and self.settings.get("behavior.walk_enabled", True)
                  and not self.anims.is_busy()):
                self._start_walk()                 # 空闲时溜达一小段
            elif r < 0.65:
                self.anims.play(random.choice(("jump", "squish", "shake")))
        self._schedule_idle()

    # ---------- 走路 ----------
    def _start_walk(self, short: bool = False) -> bool:
        """小范围左右散步：窗口平滑平移 + 轻微颠簸，拖动/点击即中断。

        返回是否成功启动（已在走/无空间/动作播放中则返回 False）。
        """
        if self._walk_anim is not None or self.anims.is_busy():
            return False
        screen = QGuiApplication.screenAt(self.window.frameGeometry().center()) \
            or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x, y = self.window.x(), self.window.y()
        rng = int(self.settings.get("behavior.walk_range_px", 180))
        if short:
            rng = min(rng, 90)
        # 随机方向，两侧都无空间则放弃
        left_ok = x - 60 >= area.left() + 8
        right_ok = x + self.window.width() + 60 <= area.right() - 8
        directions = [d for d in (-1, 1)
                      if (d < 0 and left_ok) or (d > 0 and right_ok)]
        if not directions:
            return False
        direction = random.choice(directions)
        limit = (x - area.left() - 8) if direction < 0 \
            else (area.right() - 8 - (x + self.window.width()))
        dist = max(40, min(rng, limit))
        if dist < 40:
            return False
        speed = max(30, float(self.settings.get("behavior.walk_speed_px_s", 70)))
        anim = QVariantAnimation(self)
        anim.setDuration(int(dist / speed * 1000))
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        start_x = x
        anim.valueChanged.connect(
            lambda v: self.window.move(round(start_x + dist * direction * v), y))
        anim.finished.connect(self._on_walk_done)
        self._walk_anim = anim
        self._bob_t = 0.0
        self._walk_bob_timer.start()
        anim.start()
        return True

    def _on_walk_bob(self):
        """走路时身体轻微上下起伏并小幅度摇摆。"""
        self._bob_t += 0.09
        phase = math.sin(self._bob_t * 2.2)
        self.window.pet_item.setY(-abs(phase) * self.window.height() * 0.03)
        self.window.pet_item.setRotation(phase * 2.5)

    def _on_walk_done(self):
        self._stop_walk()

    def _stop_walk(self):
        if self._walk_anim is not None:
            self._walk_anim.stop()
            self._walk_anim = None
        if self._walk_bob_timer.isActive():
            self._walk_bob_timer.stop()
        self.window.pet_item.setY(0)
        self.window.pet_item.setRotation(0)

    def _on_interaction(self):
        """用户按住宠物时中断走路。"""
        self._stop_walk()

    # ---------- 定时提醒（喝水/活动/休息） ----------
    def _reset_reminder_due(self):
        now = time.time()
        self._reminder_due = {key: now + self._reminder_interval(key)
                              for key in ("drink", "move", "rest")}

    def _reminder_interval(self, key: str) -> float:
        cfg = (self.settings.get("reminders") or {}).get(key) or {}
        return max(5, int(cfg.get("interval_min", 60))) * 60

    def _check_reminders(self):
        """每分钟检查：到期提醒 → 气泡 + 动作，并顺延下次时间（±10% 抖动）。"""
        rem = self.settings.get("reminders") or {}
        if not rem.get("enabled", True):
            return
        now = time.time()
        for key, label in (("drink", "喝水"), ("move", "活动"), ("rest", "休息")):
            cfg = rem.get(key) or {}
            if not cfg.get("enabled", True):
                continue
            if now >= self._reminder_due.get(key, 0):
                lines = cfg.get("lines") or [f"{label}时间到啦，休息一下吧"]
                self.bubble.show_text(random.choice(lines))
                self.bubble.follow(self.window.frameGeometry(),
                                   self._chat_rect_if_visible())
                action = cfg.get("action", "")
                if action:
                    self.anims.play(action)
                interval = self._reminder_interval(key)
                self._reminder_due[key] = now + interval * random.uniform(0.9, 1.1)

    def _toggle_reminders(self, on: bool):
        rem = self.settings.get("reminders") or {}
        rem["enabled"] = on
        self.settings.set("reminders", rem)
        self._schedule_save()

    def _toggle_reminder(self, key: str, on: bool):
        rem = self.settings.get("reminders") or {}
        rem.setdefault(key, {})["enabled"] = on
        self.settings.set("reminders", rem)
        self._schedule_save()

    # ---------- 状态保存 / 退出 ----------
    def _chat_rect_if_visible(self):
        return self.chat.frameGeometry() if self.chat.isVisible() else None

    def _on_geometry_changed(self):
        self.bubble.follow(self.window.frameGeometry(), self._chat_rect_if_visible())
        self._schedule_save()

    def _schedule_save(self):
        self._save_timer.start()

    def _save_state(self):
        self.window.save_position()
        self.settings.save()

    def quit(self):
        self._save_state()
        self.bubble.hide_now()
        self.chat.hide()
        self.window.hide()
        self.app.quit()
