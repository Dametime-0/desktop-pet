# -*- coding: utf-8 -*-
"""AI 形象生成业务层：上传图片 → 视觉分析 → 文生图 Q 版立绘 → 抠图保存。

流程（ImageImportWorker 后台线程执行）：
1. 视觉大模型分析上传图 → 人物结构化描述 JSON（发色/服装/配色/是否全身等）；
2. 用描述构造提示词 → 文生图生成「全身二头身 Q 版立绘」，约定纯浅蓝色背景；
3. 内置抠图（bg_hint=浅蓝）→ 校验主体占比 → 保存为新形象。

无 API Key / 网络失败时立即返回失败结果，由控制器降级为直接抠图。
本模块不做裸 HTTP（网络请求都在 llm_client），跨线程只传文件路径。
"""
import base64
import io
import json
import os
import random

from PIL import Image
from PySide6.QtCore import QThread, Signal

from . import llm_client, matting
from .utils import assets_dir, log

#: 约定生成图的纯浅蓝背景色（提示词要求模型按此生成，抠图 bg_hint 用它）
BG_BLUE = (179, 217, 255)
#: 风格启发式阈值：高/宽 >= 1.15 判为人物形象
STYLE_THRESHOLD = 1.15
#: 抠图后主体占原图面积的最小比例，低于则判定生成失败
MIN_CONTENT_RATIO = 0.10
#: 视觉分析图片压缩：长边上限（省 token，多数平台要求 ≤1280）
VISION_MAX_SIDE = 1024


class CharacterAIError(Exception):
    """角色描述解析失败。"""


# ---------- 风格启发式 ----------
def guess_style_by_size(w: int, h: int) -> str:
    """按图片宽高比推断动画风格：高/宽 >= 1.15 → humanoid，否则 cartoon。"""
    if h / max(1, w) >= STYLE_THRESHOLD:
        return "humanoid"
    return "cartoon"


def guess_style(img_path: str) -> str:
    """文件版风格判定（读取尺寸后委托 guess_style_by_size）。"""
    try:
        with Image.open(img_path) as img:
            return guess_style_by_size(*img.size)
    except OSError:
        return "cartoon"


# ---------- 提示词 ----------
def build_vision_prompt() -> str:
    """视觉分析提示词：要求只输出一个 JSON。"""
    return (
        "你是角色形象分析助手。请仔细分析这张图片中的角色，只输出一个 JSON 对象，"
        "不要 Markdown 代码块、不要任何解释。字段：\n"
        '{\n'
        '  "hair_color": "发色（具体色名+近似十六进制，如 银白色 #E8E8F0）",\n'
        '  "hair_style": "发型（长短、刘海、轮廓）",\n'
        '  "eye_color": "瞳色",\n'
        '  "outfit": "服装（上衣/下装/鞋/细节）",\n'
        '  "color_palette": ["主色1", "主色2", "主色3"],\n'
        '  "accessories": "配饰（帽子/发饰/披风等，无则空字符串）",\n'
        '  "posture": "姿态（站姿/坐姿等）",\n'
        '  "full_body": true,\n'
        '  "is_humanoid": true,\n'
        '  "extra_notes": "其它必须保留的特征（尾巴/角/翅膀/伤疤/体型等，无则空字符串）"\n'
        "}\n"
        "无法从图片确认的字段填空字符串或 null；color_palette 给 3~5 个"
        "该角色最标志性的颜色。"
    )


def build_generation_prompt(character: dict) -> str:
    """由角色描述生成文生图提示词：全身二头身 Q 版立绘、纯浅蓝背景。"""
    lines = ["根据角色描述生成一张【全身 Q 版（二头身 chibi）立绘】，动漫日系风格："]
    field_map = [
        ("hair_color", "发色"),
        ("hair_style", "发型"),
        ("eye_color", "瞳色"),
        ("outfit", "服装"),
        ("accessories", "配饰"),
        ("posture", "姿态"),
        ("extra_notes", "必须保留的特征"),
    ]
    for key, label in field_map:
        value = str(character.get(key) or "").strip()
        if value:
            lines.append(f"{label}：{value}")
    palette = character.get("color_palette") or []
    if isinstance(palette, list) and palette:
        lines.append("标志性配色：" + "、".join(str(c) for c in palette if str(c).strip()))
    if character.get("is_humanoid") is False:
        lines.append("角色非人形，按图中物种/生物特征还原为 Q 版。")
    lines += [
        "硬性要求：",
        "1. 完整全身，脚踩画面底边，正面站立，双手自然下垂或微抬；",
        "2. 二头身 Q 版比例：大头圆脸、大眼睛、四肢短小圆润，线条干净、色彩明快；",
        "3. 背景必须是纯浅蓝色纯色背景（近似 #B3D9FF），无渐变、无图案、无阴影、无边框；",
        "4. 角色居中，占画面高度约 85%，不要截断任何肢体；",
        "5. 严禁任何文字、水印、LOGO、签名。",
    ]
    return "\n".join(lines)


# ---------- 视觉分析 ----------
def parse_character_json(text: str) -> dict:
    """从模型输出中提取角色描述 JSON。

    多级兜底：剥 ``` 代码围栏 → 截取首个 { 到最后一个 } → json.loads →
    字段缺省补全。失败抛 CharacterAIError。
    """
    if not text or not text.strip():
        raise CharacterAIError("模型返回为空")
    text = text.strip()
    if text.startswith("```"):                 # 剥代码围栏
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise CharacterAIError("输出中找不到 JSON 对象")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise CharacterAIError(f"JSON 解析失败: {e}")
    if not isinstance(data, dict):
        raise CharacterAIError("JSON 不是对象")
    defaults = {
        "hair_color": "", "hair_style": "", "eye_color": "", "outfit": "",
        "color_palette": [], "accessories": "", "posture": "",
        "full_body": None, "is_humanoid": None, "extra_notes": "",
    }
    for key, value in defaults.items():
        data.setdefault(key, value)
    if not isinstance(data.get("color_palette"), list):
        data["color_palette"] = []
    return data


def _prepare_vision_image(img_path: str) -> str:
    """压缩图片并转 data URL（长边 ≤1024，控制 token 消耗）。"""
    with Image.open(img_path) as img:
        img = img.convert("RGBA")
        if max(img.size) > VISION_MAX_SIDE:
            ratio = VISION_MAX_SIDE / max(img.size)
            img = img.resize((max(1, int(img.width * ratio)),
                              max(1, int(img.height * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(
            buf.getvalue()).decode("ascii")


def vision_analyze(cfg: dict, image_path: str) -> dict:
    """视觉分析上传图，返回 {"ok": True, "data": 角色描述} 或失败结果。

    api_key 为空立即返回离线结果（保证 selftest 零网络请求）。
    """
    if not cfg or not cfg.get("enabled") or not cfg.get("api_key"):
        return {"ok": False, "offline": True, "error": "未配置视觉模型 API Key"}
    try:
        data_url = _prepare_vision_image(image_path)
    except OSError as e:
        return {"ok": False, "offline": False, "error": f"图片读取失败: {e}"}
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": build_vision_prompt()},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }]
    result = llm_client.chat_completion(
        cfg.get("base_url"), cfg.get("api_key"), cfg.get("model"),
        messages, int(cfg.get("timeout", 60)),
        int(cfg.get("max_tokens", 512)), float(cfg.get("temperature", 0.2)))
    if not result.get("ok"):
        return result
    try:
        return {"ok": True, "data": parse_character_json(result["text"])}
    except CharacterAIError as e:
        log.warning("角色描述解析失败: %s", e)
        return {"ok": False, "offline": False, "error": f"角色描述解析失败: {e}"}


# ---------- 导入流水线（后台线程） ----------
class ImageImportWorker(QThread):
    """AI 形象生成流水线：分析 → 生成 → 抠图 → 保存。

    跨线程只传文件路径；生成图字节在 worker 内解码并完成抠图保存。
    """
    progress = Signal(str)
    done = Signal(dict)

    def __init__(self, settings, src_path: str):
        super().__init__()
        self._settings = settings
        self._src_path = src_path

    def _generate(self, gen_cfg: dict, prompt: str, seed: int) -> dict:
        return llm_client.image_generation(
            gen_cfg.get("base_url"), gen_cfg.get("api_key"), gen_cfg.get("model"),
            prompt, gen_cfg.get("image_size", "768x1024"),
            int(gen_cfg.get("batch_size", 1)), float(gen_cfg.get("guidance_scale", 7.5)),
            int(gen_cfg.get("num_inference_steps", 30)), seed,
            int(gen_cfg.get("timeout", 120)))

    def run(self):
        vision_cfg = self._settings.get("vision") or {}
        gen_cfg = self._settings.get("image_gen") or {}
        matting_cfg = self._settings.get("matting") or {}
        try:
            # ① 视觉分析
            self.progress.emit("正在分析角色特征…")
            result = vision_analyze(vision_cfg, self._src_path)
            if not result.get("ok"):
                self.done.emit({"ok": False, "stage": "vision", "fallback": True,
                                "error": result.get("error", "未知错误")})
                return

            # ② 文生图（失败换 seed 重试一次）
            prompt = build_generation_prompt(result["data"])
            seed = int(gen_cfg.get("seed", -1))
            self.progress.emit("正在绘制 Q 版立绘，约 30~60 秒…")
            img_result = self._generate(gen_cfg, prompt, seed)
            if not img_result.get("ok"):
                self.progress.emit("生成失败，重试一次…")
                retry_seed = seed + 1000 if seed > 0 else random.randint(1, 10**9)
                img_result = self._generate(gen_cfg, prompt, retry_seed)
            if not img_result.get("ok"):
                self.done.emit({"ok": False, "stage": "generate", "fallback": True,
                                "error": img_result.get("error", "生成失败")})
                return

            # ③ 抠图 + 主体占比校验
            self.progress.emit("生成完成，正在抠图换装…")
            img = Image.open(io.BytesIO(img_result["bytes"]))
            processed = matting.remove_background(
                img, matting_cfg.get("method", "auto"),
                int(matting_cfg.get("tolerance", 32)), bg_hint=BG_BLUE)
            trimmed = matting.trim_to_content(processed)
            bbox = trimmed.getchannel("A").point(
                lambda a: 255 if a > 16 else 0).getbbox()
            if bbox is None or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) \
                    < img.width * img.height * MIN_CONTENT_RATIO:
                self.done.emit({"ok": False, "stage": "matting", "fallback": True,
                                "error": "生成图中未识别到主体"})
                return

            # ④ 保存
            os.makedirs(assets_dir, exist_ok=True)
            dst = os.path.join(assets_dir, "pet.png")
            trimmed.save(dst)
            log.info("AI 形象已保存: %s", dst)
            self.done.emit({"ok": True, "path": dst})
        except Exception as e:                    # noqa: BLE001 线程内兜底
            log.error("AI 形象生成异常: %s", e)
            self.done.emit({"ok": False, "stage": "unknown", "fallback": True,
                            "error": str(e)})
