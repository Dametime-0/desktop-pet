# -*- coding: utf-8 -*-
"""总控：组装窗口/动画/气泡/聊天，负责所有交互接线。

交互规则：
- 左键点击宠物上半身 → 摸头动画 + 泡泡，下半身 → 蹦跳动画 + 泡泡；
  同时打开对话面板并聚焦输入框；
- 左键拖动移动位置，滚轮缩放，右键弹出菜单；
- 空闲时随机自言自语/小动作（behavior.auto_idle）。
"""
import os
import random

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QMenu

from . import llm_client, matting
from .animations import AnimController
from .bubble import BubbleWindow
from .chat_panel import ChatPanel, ChatWorker
from .default_image import ensure_default_image, ensure_icon
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

        # 窗口与场景
        from .window import PetWindow
        self.window = PetWindow(self.settings)
        self.anims = AnimController(self.window.root_group,
                                    self.window.pet_item, self.window.scene())
        topmost = self.settings.get("window.always_on_top", True)
        self.bubble = BubbleWindow(self.settings.get("bubble") or {}, topmost)
        self.chat = ChatPanel(self.settings, self.persona.current.name,
                              self.handle_chat, topmost)

        # 形象加载（缺失时生成默认形象）
        pet_path = ensure_default_image()
        if not self.window.load_image(pet_path):
            log.error("形象加载失败: %s", pet_path)
        self.anims.set_zoom(self.window._fit_scale * self.window.user_scale())

        # 信号接线
        self.window.clicked.connect(self._on_click)
        self.window.context_menu_requested.connect(self._show_menu)
        self.window.dropped_image.connect(self._import_image)
        self.window.geometry_changed.connect(self._on_geometry_changed)

        # 启动
        self.anims.start_idle()
        self.window.restore_position()
        self.window.show()
        self._schedule_idle()

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
        if fy < HEAD_ZONE:
            self.anims.play("pat")
            line = self.dialogue.personality.offline_reply("pat")
        else:
            self.anims.play("bounce")
            line = self.dialogue.personality.offline_reply("jump")
        self.bubble.show_text(line)
        self.chat.open_near(self.window.frameGeometry())

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
                self.anims.play(action)
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
            self.bubble.show_text("新形象加载好啦～团子觉得超好看！")
        except Exception as e:                    # noqa: BLE001 导入失败不能崩溃
            log.error("形象导入失败: %s", e)
            self.bubble.show_text("图片处理失败……换一张试试？")

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
            self.bubble.show_text(f"人格「{result['name']}」加载好啦！")
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
                self.bubble.show_text("人格包导出成功～可以分享给朋友啦！")
            except (PersonalityError, OSError) as e:
                self.bubble.show_text(f"导出失败：{e}")

    def _open_personality_dir(self):
        target = self.persona.current_dir or personality_dir
        try:
            os.startfile(target)
        except OSError:
            self.bubble.show_text("打不开文件夹……")

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
            if r < 0.4:
                self.bubble.show_text(self.dialogue.idle_line())
            elif r > 0.55:
                self.anims.play(random.choice(("jump", "squish", "shake")))
        self._schedule_idle()

    # ---------- 状态保存 / 退出 ----------
    def _on_geometry_changed(self):
        self.bubble.follow(self.window.frameGeometry())
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
