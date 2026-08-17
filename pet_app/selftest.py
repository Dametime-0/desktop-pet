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
from PySide6.QtGui import QGuiApplication

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
    rule = d.match_rule("我想吃草莓大福")
    _check(checks, "记忆库关键词(草莓大福)",
           rule is not None and "草莓大福" in rule["keywords"], f"got={rule}")
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
    from .default_image import draw_default_pet
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
        # 还原正式形象文件（备份缺失时重新生成默认形象）
        if backup is not None:
            with open(pet_path, "wb") as f:
                f.write(backup)
        elif os.path.isfile(pet_path):
            draw_default_pet().save(pet_path)


def _test_settings(ctrl, checks):
    ctrl.settings.set("_selftest_marker", 123)
    ok = ctrl.settings.get("_selftest_marker") == 123
    ctrl.settings.set("_selftest_marker", None)
    _check(checks, "配置读写", ok)


def _test_bubble_position(checks):
    """气泡定位回归：宠物靠屏幕右边、面板在其左侧时，气泡必须避开面板且不越屏。"""
    from PySide6.QtCore import QRect, QSize
    from .bubble import choose_rect, SIDE_DOWN, SIDE_UP
    area = QRect(0, 0, 1920, 1040)
    size = QSize(200, 60)

    # 1) 无面板：默认在宠物上方
    pet = QRect(1700, 900, 120, 240)
    rect, side = choose_rect(pet, size, area)
    _check(checks, "气泡定位-默认上方",
           side == SIDE_DOWN and rect.bottom() <= pet.top() and area.contains(rect),
           f"rect={rect}")

    # 2) 面板在宠物左侧（用户报告的 bug 场景）：气泡不得与面板重叠
    panel = QRect(1400, 900, 340, 430)      # 宠物右侧空间不足，面板翻转到左侧
    rect, side = choose_rect(pet, size, area, panel)
    _check(checks, "气泡定位-避开左侧面板",
           not rect.intersects(panel) and area.contains(rect),
           f"rect={rect} side={side}")
    # 气泡需贴近宠物（与宠物包围盒或宠物+面板外接区域相邻）
    union = pet.united(panel)
    _check(checks, "气泡定位-贴近宠物",
           rect.adjusted(-30, -30, 30, 30).intersects(pet),
           f"rect={rect} pet={pet}")

    # 3) 宠物贴屏幕顶部：气泡翻转到下方
    pet_top = QRect(900, 0, 120, 240)
    rect, side = choose_rect(pet_top, size, area)
    _check(checks, "气泡定位-顶部翻转下方",
           side == SIDE_UP and rect.top() >= pet_top.bottom() and area.contains(rect),
           f"rect={rect} side={side}")

    # 4) 面板完全围住宠物（极小空间）：仍必须返回屏幕内位置
    panel_full = QRect(1600, 800, 400, 500)
    rect, side = choose_rect(pet, size, area, panel_full)
    _check(checks, "气泡定位-极端空间不越屏", area.contains(rect), f"rect={rect}")


def _test_style_heuristic(checks):
    """风格启发式：高/宽 >= 1.15 判人物（含边界值）。"""
    from . import character_ai
    cases = [((300, 300), "cartoon"), ((200, 420), "humanoid"),
             ((400, 220), "cartoon"), ((100, 114), "cartoon"),
             ((100, 115), "humanoid")]
    for i, (size, expect) in enumerate(cases):
        img = Image.new("RGBA", size, (255, 255, 255, 255))
        p = os.path.join(OUT_DIR, f"_style_{i}.png")
        img.save(p)
        got = character_ai.guess_style(p)
        os.remove(p)
        _check(checks, f"风格启发式{size}->{expect}", got == expect, got)


def _test_prompt_build(checks):
    """提示词构建：特征注入、硬性要求关键词、空字段跳过。"""
    from . import character_ai
    ch = {"hair_color": "银白色 #E8E8F0", "hair_style": "及腰银发+齐刘海",
          "eye_color": "蓝色", "outfit": "白色连衣裙",
          "color_palette": ["白", "蓝"], "accessories": "蝴蝶结",
          "posture": "站姿", "full_body": None, "is_humanoid": True,
          "extra_notes": ""}
    prompt = character_ai.build_generation_prompt(ch)
    _check(checks, "生成提示词-特征注入",
           "银白色" in prompt and "白色连衣裙" in prompt and "蝴蝶结" in prompt,
           prompt[:60])
    _check(checks, "生成提示词-硬性要求",
           all(k in prompt for k in ("二头身", "浅蓝", "全身", "纯色背景")))
    empty = {k: "" for k in ch}
    empty["color_palette"] = []
    prompt2 = character_ai.build_generation_prompt(empty)
    _check(checks, "生成提示词-空字段跳过",
           "None" not in prompt2 and "发色：" not in prompt2)
    vp = character_ai.build_vision_prompt()
    _check(checks, "视觉提示词-字段",
           "hair_color" in vp and "full_body" in vp and "is_humanoid" in vp)


def _test_parse_character_json(checks):
    """角色描述解析：纯 JSON / 围栏 / 尾文本 / 缺省补全 / 非法抛出。"""
    from . import character_ai
    data = character_ai.parse_character_json(
        '{"hair_color": "粉色", "color_palette": ["粉", "白"]}')
    _check(checks, "解析-纯JSON+缺省补全",
           data["hair_color"] == "粉色" and data["eye_color"] == ""
           and isinstance(data["color_palette"], list))
    data = character_ai.parse_character_json(
        '```json\n{"hair_color": "金发"}\n```\n（以上为分析结果）')
    _check(checks, "解析-围栏+尾文本", data["hair_color"] == "金发")
    data = character_ai.parse_character_json('```\n{"hair_color": "黑发"}\n```')
    _check(checks, "解析-无语言围栏", data["hair_color"] == "黑发")
    try:
        character_ai.parse_character_json("没有 JSON 的输出内容")
        _check(checks, "解析-非法抛出", False)
    except character_ai.CharacterAIError:
        _check(checks, "解析-非法抛出", True)


def _test_action_names(checks):
    """动作名同步：animations 与 personality 一致，未知动作被过滤。"""
    from . import animations
    from .personality import ACTION_NAMES, Personality
    for action in ("walk", "wave", "bow", "pat"):
        _check(checks, f"动作名同步-{action}",
               action in animations.ACTIONS and action in ACTION_NAMES)
    p = Personality({"name": "测试", "keyword_rules": [
        {"keywords": ["走走"], "replies": ["好的"], "action": "walk"},
        {"keywords": ["飞"], "replies": ["……"], "action": "fly"},
    ]}, "")
    rules = {tuple(r["keywords"]): r for r in p.rules}
    _check(checks, "动作名过滤-walk保留", rules[("走走",)]["action"] == "walk")
    _check(checks, "动作名过滤-fly清空", rules[("飞",)]["action"] == "")


def _test_walk_clamp(checks):
    """走路平移钳制：界内原值、左右边界、副屏负坐标。"""
    from PySide6.QtCore import QRect
    from .window import clamp_x
    screen = QRect(-1920, 0, 1920, 1040)      # 副屏场景：left=-1920，right=-1（闭区间）
    _check(checks, "走路钳制-界内", clamp_x(-500, 200, screen) == -500)
    _check(checks, "走路钳制-右边界", clamp_x(5000, 200, screen) == -201)
    _check(checks, "走路钳制-左边界", clamp_x(-5000, 200, screen) == -1920)


def run_selftest(app) -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    ctrl = PetController(app)
    checks = []

    def step(delay, fn):
        QTimer.singleShot(delay, fn)

    # ---- 截图时间轴 ----
    step(400, lambda: _save_grab(ctrl.window, "01_pet_window.png"))
    step(500, lambda: ctrl.bubble.show_text("主人好呀～这是气泡测试，逐字显示哦！"))
    step(900, lambda: _save_grab(ctrl.bubble, "02_bubble.png"))
    step(1000, lambda: ctrl.anims.play("pat"))
    step(1600, lambda: _save_grab(ctrl.window, "03_pat.png"))
    step(1700, lambda: ctrl.anims.play("bounce"))
    step(2300, lambda: _save_grab(ctrl.window, "04_bounce.png"))

    # ---- 人物风格动作验证：居中后走路（避免顶到屏幕边缘）----
    walk_x0 = [0]

    def _center_window():
        screen = QGuiApplication.primaryScreen().availableGeometry()
        ctrl.window.move(screen.center().x() - ctrl.window.width() // 2,
                         screen.center().y() - ctrl.window.height() // 2)

    # 时间轴说明：bounce(1700 入队，约 2940 结束) → walk 排队约 2940 开始（3 步 840ms）
    step(2400, lambda: (_center_window(),
                        walk_x0.__setitem__(0, ctrl.window.x()),
                        ctrl.anims.set_style("humanoid"),
                        ctrl.anims.play("walk")))
    step(3350, lambda: _check(checks, "人物风格-走路位移",
                              abs(ctrl.window.x() - walk_x0[0]) > 10,
                              f"dx={ctrl.window.x() - walk_x0[0]}"))
    step(3450, lambda: (_save_grab(ctrl.window, "05b_humanoid_walk.png"),
                        ctrl.anims.play("wave")))
    step(5300, lambda: _save_grab(ctrl.window, "05c_wave.png"))
    step(6400, lambda: ctrl.anims.play("bow"))
    step(6850, lambda: _save_grab(ctrl.window, "05d_bow.png"))

    # ---- 聊天面板截图 ----
    step(6900, lambda: ctrl.chat.open_near(ctrl.window.frameGeometry()))
    step(7000, lambda: ctrl.chat.append_user("团子你好呀"))
    step(7100, lambda: ctrl.chat.append_pet("主人好！团子在哦～"))
    step(7500, lambda: _save_grab(ctrl.chat, "05_chat_panel.png"))

    # ---- 逻辑断言 ----
    step(7600, lambda: _test_dialogue(ctrl, checks))
    step(7700, lambda: _test_matting(checks))
    step(7800, lambda: _test_settings(ctrl, checks))
    step(7850, lambda: _test_bubble_position(checks))
    step(7900, lambda: _test_style_heuristic(checks))
    step(7950, lambda: _test_prompt_build(checks))
    step(8000, lambda: _test_parse_character_json(checks))
    step(8050, lambda: _test_action_names(checks))
    step(8100, lambda: _test_walk_clamp(checks))

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
            ctrl.anims.set_style("cartoon")      # 还原默认风格
            ctrl.bubble.hide_now()
            ctrl.chat.hide()
            ctrl.window.hide()
            app.exit(0 if not failed else 1)

    step(8300, finish)
    return app.exec()
