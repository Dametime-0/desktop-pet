# -*- coding: utf-8 -*-
"""自检模式：`python main.py --selftest`

按时间轴截图验证界面渲染（窗口/气泡/动画/聊天面板），
并断言对话引擎与抠图等核心逻辑，输出结果后自动退出。
截图与产物写入项目根目录 _selftest/ 文件夹。
"""
import io
import os
import sys

from PIL import Image, ImageDraw
from PySide6.QtCore import QBuffer, QIODevice, QTimer

from . import matting
from .controller import PetController

# Windows 控制台默认 GBK，打印 emoji 会抛异常导致自检中断，这里统一转 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "_selftest")


def _save_grab(widget, name: str, bg=(232, 240, 248)):
    """窗口截图（透明部分叠底色便于查看）。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    pm = widget.grab()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    img = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
    base = Image.new("RGBA", img.size, bg + (255,))
    base.alpha_composite(img)
    base.convert("RGB").save(os.path.join(OUT_DIR, name))


def _check(checks, name, condition, extra=""):
    checks.append((name, condition, extra))


def _test_dialogue(ctrl, checks):
    """断言对话引擎：关键词/记忆/分类/昵称记忆/离线兜底。"""
    d = ctrl.dialogue
    # 关键词规则命中 → 彩蛋台词 + 指定动作
    rule = d.match_rule("今天是我的生日哦")
    _check(checks, "关键词规则(生日→dance)",
           rule is not None and rule["action"] == "dance", f"got={rule}")
    # 记忆库关键词命中
    rule = d.match_rule("我想喝咖啡")
    _check(checks, "记忆库关键词(咖啡)",
           rule is not None and "咖啡" in rule["keywords"], f"got={rule}")
    # 离线分类
    _check(checks, "离线分类(greeting)", d.classify("你好呀") == "greeting",
           d.classify("你好呀"))
    _check(checks, "离线分类(question)", d.classify("今天天气怎么样") == "question",
           d.classify("今天天气怎么样"))
    # 离线兜底回复非空
    reply = d.classify_reply("今天天气怎么样")
    _check(checks, "离线兜底回复", isinstance(reply, str) and len(reply) > 0, reply)
    # 昵称记忆
    old_name = ctrl.settings.get("chat.user_name", "")
    res, action = d.handle_local("我叫小明") or (None, None)
    _check(checks, "昵称记忆", res is not None and "小明" in res, res)
    _check(checks, "昵称已保存", ctrl.settings.get("chat.user_name") == "小明",
           ctrl.settings.get("chat.user_name"))
    ctrl.settings.set("chat.user_name", old_name)   # 还原，不污染用户配置
    ctrl.settings.save()
    # 离线本地交互（模拟点击身体）
    line = d.personality.offline_reply("jump")
    _check(checks, "离线交互台词(jump)", isinstance(line, str) and len(line) > 0, line)


def _test_matting(checks):
    """合成一张白底图片 → 抠图 → 验证边角透明、主体不透明。

    抠图会写入正式形象 assets/pet.png，测试前后备份/还原，避免污染。
    """
    from .utils import assets_dir
    pet_path = os.path.join(assets_dir, "pet.png")
    backup = None
    if os.path.isfile(pet_path):
        with open(pet_path, "rb") as f:
            backup = f.read()
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([80, 80, 220, 220], fill=(230, 80, 80))      # 红色主体
    d.rectangle([30, 230, 90, 290], fill=(60, 120, 220))   # 贴边蓝色块（不应被误删）
    src = os.path.join(OUT_DIR, "matting_src.jpg")
    img.save(src, quality=95)
    try:
        dst = matting.process_image(src, "floodfill", 30)
        res = Image.open(dst).convert("RGBA")
        corner_alpha = res.getpixel((3, 3))[3]             # 裁剪后边角为留白
        center_alpha = res.getpixel((res.width // 2, res.height // 2))[3]
        _check(checks, "抠图-背景透明", corner_alpha < 30, f"corner={corner_alpha}")
        _check(checks, "抠图-主体保留", center_alpha > 200, f"center={center_alpha}")
        res.convert("RGB").save(os.path.join(OUT_DIR, "matting_result.png"))
    except Exception as e:                                # noqa: BLE001
        _check(checks, "抠图流程", False, repr(e))
    finally:
        # 还原正式形象文件；原本不存在（备份为空）则删除测试产物
        if backup is not None:
            with open(pet_path, "wb") as f:
                f.write(backup)
        elif os.path.isfile(pet_path):
            os.remove(pet_path)


def _test_settings(ctrl, checks):
    ctrl.settings.set("_selftest_marker", 123)
    ok = ctrl.settings.get("_selftest_marker") == 123
    ctrl.settings.set("_selftest_marker", None)
    _check(checks, "配置读写", ok)


def _test_bubble_placement(ctrl, checks):
    """气泡定位回归：宠物在聊天面板右侧时，气泡应贴在宠物旁、不压面板、不出屏。"""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    area = QGuiApplication.primaryScreen().availableGeometry()
    # 用户报告的场景：宠物在面板右侧，中间留有空隙
    pet = QRect(area.right() - 520, area.top() + 200, 180, 240)
    panel = QRect(pet.left() - 360, pet.top() - 30, 340, 430)
    ctrl.bubble.show_text("你好呀，这是一条测试气泡消息")
    ctrl.bubble.follow(pet, panel)
    b = ctrl.bubble.frameGeometry()
    _check(checks, "气泡-屏幕内", area.contains(b),
           f"{b.x()},{b.y()},{b.width()}x{b.height()}")
    _check(checks, "气泡-不压面板", not b.intersects(panel), f"{b.x()},{b.y()}")
    petc, bc = pet.center(), b.center()
    dist = ((petc.x() - bc.x()) ** 2 + (petc.y() - bc.y()) ** 2) ** 0.5
    _check(checks, "气泡-贴近宠物", dist < 400, f"dist={dist:.0f}")
    # 文字变长（气泡尺寸变化）后应重新定位回宠物旁，不能漂走
    x1 = b.center().x()
    ctrl.bubble.show_text("这是一条很长很长很长的测试消息，长度变化后气泡必须重新定位到宠物旁边，不能漂走哦")
    b2 = ctrl.bubble.frameGeometry()
    _check(checks, "气泡-长文本重新定位", abs(b2.center().x() - x1) < 260,
           f"dx={b2.center().x() - x1}")
    ctrl.bubble.hide_now()


def run_selftest(app) -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    ctrl = PetController(app)
    checks = []

    def step(delay, fn):
        QTimer.singleShot(delay, fn)

    # ---- 截图时间轴 ----
    step(400, lambda: _save_grab(ctrl.window, "01_pet_window.png"))
    step(500, lambda: ctrl.bubble.show_text("你好呀～这是气泡测试，逐字显示哦！"))
    step(900, lambda: _save_grab(ctrl.bubble, "02_bubble.png"))
    step(1000, lambda: ctrl.anims.play("pat"))
    step(1600, lambda: _save_grab(ctrl.window, "03_pat.png"))
    step(1700, lambda: ctrl.anims.play("bounce"))
    step(2300, lambda: _save_grab(ctrl.window, "04_bounce.png"))
    step(2400, lambda: ctrl.chat.open_near(ctrl.window.frameGeometry()))
    step(2500, lambda: ctrl.chat.append_user("magic 你好呀"))
    step(2600, lambda: ctrl.chat.append_pet("你好，我在呢～"))
    step(3000, lambda: _save_grab(ctrl.chat, "05_chat_panel.png"))

    # ---- 逻辑断言 ----
    step(3200, lambda: _test_dialogue(ctrl, checks))
    step(3300, lambda: _test_matting(checks))
    step(3400, lambda: _test_settings(ctrl, checks))
    step(3450, lambda: _test_bubble_placement(ctrl, checks))

    def finish():
        # 报告打印失败（如编码问题）也不能影响退出
        try:
            passed = [c for c in checks if c[1]]
            failed = [c for c in checks if not c[1]]
            print(f"\n===== 自检完成：{len(passed)}/{len(checks)} 通过 =====")
            for name, ok, extra in checks:
                print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
                      + (f"  ({extra})" if extra else ""))
            print(f"截图与产物目录: {OUT_DIR}")
        except Exception as e:                    # noqa: BLE001
            print("SELFTEST REPORT ERROR:", repr(e))
        finally:
            ctrl.bubble.hide_now()
            ctrl.chat.hide()
            ctrl.window.hide()
            app.exit(0 if not failed else 1)

    step(3600, finish)
    return app.exec()
