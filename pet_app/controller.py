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
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox

from . import character_ai, llm_client, matting
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
        self._importing = False               # AI 形象生成进行中（防重入）
        self._ai_src_path = None              # AI 导入的原始图片路径（降级抠图用）
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
        self._apply_style_for_image()

        # 信号接线
        self.window.clicked.connect(self._on_click)
        self.window.context_menu_requested.connect(self._show_menu)
        self.window.dropped_image.connect(self._import_image)
        self.window.geometry_changed.connect(self._on_geometry_changed)
        self.window.drag_started.connect(self.anims.stop_all)   # 拖动中断走路等动作
        self.anims.set_walk_cb(self.window.walk)                # 走路=真实平移窗口

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

    # ---------- 气泡辅助 ----------
    def _say(self, text: str):
        """显示气泡并立即定位到宠物旁（每次显示都重新定位，避免位置陈旧）。"""
        self.bubble.show_text(text)
        self._reposition_bubble()

    def _reposition_bubble(self):
        """气泡定位：避让对话面板（可见时），并置于面板之上。"""
        avoid = self.chat.frameGeometry() if self.chat.isVisible() else None
        self.bubble.follow(self.window.frameGeometry(), avoid)
        self.bubble.raise_()

    # ---------- 宠物点击 ----------
    def _on_click(self, fx: float, fy: float):
        """点击：上半身摸头 / 下半身蹦跳，并打开对话面板。"""
        if fy < HEAD_ZONE:
            self.anims.play("pat")
            line = self.dialogue.personality.offline_reply("pat")
        else:
            self.anims.play("bounce")
            line = self.dialogue.personality.offline_reply("jump")
        self._say(line)
        self.chat.open_near(self.window.frameGeometry())
        self._reposition_bubble()      # 面板刚出现，需按避让规则再次定位

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
        img_act = menu.addAction("更换形象…", self._choose_image)
        img_act.setEnabled(not self._importing)
        style_menu = menu.addMenu("动画风格")
        style_group = QActionGroup(menu)
        style_group.setExclusive(True)
        cur_style = self.settings.get("animation.style", "auto")
        for key, label in (("auto", "自动（按图片比例）"),
                           ("cartoon", "卡通弹性（团子风）"),
                           ("humanoid", "人物动作（Q 版立绘）")):
            act = style_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(cur_style == key)
            act.triggered.connect(lambda checked=False, k=key: self._set_style(k))
            style_group.addAction(act)
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

    # ---------- 动画风格 ----------
    def _resolved_style(self) -> str:
        """解析当前生效风格：auto 按当前形象宽高比判定。"""
        style = self.settings.get("animation.style", "auto")
        if style == "auto":
            pm = self.window._pixmap
            return character_ai.guess_style_by_size(
                pm.width() if pm else 0, pm.height() if pm else 0)
        return style

    def _apply_style_for_image(self):
        """按配置应用动画风格（换形象后调用）。"""
        self.anims.set_style(self._resolved_style())

    def _set_style(self, style: str):
        """右键菜单切换动画风格并持久化。"""
        self.settings.set("animation.style", style)
        self.anims.set_style(self._resolved_style())
        self._schedule_save()
        labels = {"auto": "自动（按图片比例）", "cartoon": "卡通弹性",
                  "humanoid": "人物动作"}
        self._say(f"动画风格已切换为：{labels.get(style, style)}～")

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

    def _ai_available(self) -> bool:
        """视觉分析与文生图两节均已启用且配置了 api_key。"""
        vision = self.settings.get("vision") or {}
        gen = self.settings.get("image_gen") or {}
        return bool(vision.get("enabled") and vision.get("api_key")
                    and gen.get("enabled") and gen.get("api_key"))

    def _import_image(self, path: str):
        """导入图片入口：确认对话框选择 AI 生成 / 仅抠图 / 取消。"""
        if self._importing:                       # 防重入
            return
        box = QMessageBox(self.window)
        box.setWindowTitle("导入形象")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("要如何处理这张图片？\n\n"
                    "· AI 生成 Q 版形象：调用视觉模型分析角色特征，再生成完整全身"
                    "二头身 Q 版立绘（约 1 分钟，消耗 API 额度）；\n"
                    "· 仅抠图直接使用：去除背景后直接作为桌宠形象（不完整的"
                    "人物会保持不完整）。")
        ai_btn = box.addButton("AI 生成 Q 版形象", QMessageBox.ButtonRole.AcceptRole)
        direct_btn = box.addButton("仅抠图直接使用", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(ai_btn)
        if not self._ai_available():
            ai_btn.setEnabled(False)
            box.setInformativeText("未检测到 AI 配置：请在 config/settings.json 的 "
                                   "image_gen 与 vision 节填写 api_key（建议放 "
                                   "settings.local.json），模型与接口地址也可按需修改。")
        box.exec()
        if box.clickedButton() is ai_btn:
            self._start_ai_import(path)
        elif box.clickedButton() is direct_btn:
            self._matting_only_import(path)

    def _matting_only_import(self, path: str):
        """仅抠图直接使用（也是 AI 失败的降级路径）。"""
        try:
            cfg = self.settings.get("matting") or {}
            dst = matting.process_image(path, cfg.get("method", "auto"),
                                        int(cfg.get("tolerance", 32)))
            self.window.load_image(dst)
            self.anims.set_zoom(self.window._fit_scale * self.window.user_scale())
            self._apply_style_for_image()
            self._say("新形象加载好啦～团子觉得超好看！")
        except Exception as e:                    # noqa: BLE001 导入失败不能崩溃
            log.error("形象导入失败: %s", e)
            self._say("图片处理失败……换一张试试？")

    def _start_ai_import(self, path: str):
        """启动 AI 形象生成后台流水线。"""
        self._importing = True
        self._ai_src_path = path
        worker = character_ai.ImageImportWorker(self.settings, path)
        worker.progress.connect(self._on_ai_progress)
        worker.done.connect(self._on_ai_import_done)
        worker.start()
        self._workers.append(worker)

    def _on_ai_progress(self, text: str):
        """进度气泡：sticky 模式不自动消失，跟随宠物。"""
        self.bubble.show_text(text, sticky=True)
        self._reposition_bubble()

    def _on_ai_import_done(self, result: dict):
        """AI 流水线收尾：成功则换装并切人物风格；失败弹窗后降级直接抠图。"""
        self.bubble.hide_now()
        self._importing = False
        if result.get("ok"):
            self.window.load_image(result["path"])
            self.anims.set_zoom(self.window._fit_scale * self.window.user_scale())
            self.anims.set_style("humanoid")          # AI 生成 → 人物动作风格
            self.settings.set("animation.style", "humanoid")
            self._schedule_save()
            self._say("新形象加载好啦！已自动切换人物动作风格～")
        else:
            error = result.get("error", "未知错误")
            log.warning("AI 形象生成失败(%s): %s", result.get("stage"), error)
            QMessageBox.warning(self.window, "AI 生成失败",
                                f"AI 形象生成失败：{error}\n\n已改为直接抠图使用原图。")
            self._matting_only_import(self._ai_src_path or "")
        self._ai_src_path = None

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
                self._apply_style_for_image()
            self._say(f"人格「{result['name']}」加载好啦！")
            self.chat._title.setText(f"💬 {result['name']}")
        except (PersonalityError, OSError, ValueError) as e:
            log.warning("人格包导入失败: %s", e)
            self._say(f"人格包导入失败：{e}")

    def _choose_export_pack(self):
        name = self.persona.current.name if self.persona.current else "personality"
        default = os.path.join(os.path.expanduser("~"), "Desktop",
                               f"{name}_人格包.zip")
        path, _ = QFileDialog.getSaveFileName(None, "导出人格包", default,
                                              "人格包 (*.zip)")
        if path:
            try:
                self.persona.export_pack(path)
                self._say("人格包导出成功～可以分享给朋友啦！")
            except (PersonalityError, OSError) as e:
                self._say(f"导出失败：{e}")

    def _open_personality_dir(self):
        target = self.persona.current_dir or personality_dir
        try:
            os.startfile(target)
        except OSError:
            self._say("打不开文件夹……")

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
                self._say(self.dialogue.idle_line())
            elif r > 0.55:
                pool = ("jump", "squish", "shake")
                if self.anims.style() == "humanoid":
                    pool += ("wave",)        # 人物形象多点挥手小动作
                self.anims.play(random.choice(pool))
        self._schedule_idle()

    # ---------- 状态保存 / 退出 ----------
    def _on_geometry_changed(self):
        self._reposition_bubble()
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
