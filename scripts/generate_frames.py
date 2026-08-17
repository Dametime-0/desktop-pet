# -*- coding: utf-8 -*-
"""AI 补帧管线：一张立绘 → 各动作的动画帧素材。

原理：把立绘交给图生视频模型（默认硅基流动 Wan2.2-I2V），用动作提示词
生成短视频（要求纯白背景、角色保持一致、循环动作），再用 cv2 抽帧、
逐帧抠图去背景，最后按动作整理到 assets/animations/<动作>/ 目录，
桌宠程序检测到帧素材后自动切换为帧动画播放。

用法：
    1. 设置环境变量 SILICONFLOW_API_KEY（或 --api-key 传入）；
    2. python scripts/generate_frames.py                # 生成全部默认动作
       python scripts/generate_frames.py --action idle  # 只生成指定动作
       python scripts/generate_frames.py --action walk --frames 8 --size 720x1280

输出目录结构（桌宠自动识别）：
    assets/animations/
      idle/  frame_0.png ...     待机（循环）
      walk/  frame_0.png ...     走路（循环，程序按方向水平翻转）
      jump/  frame_0.png ...     跳跃（单次）
      pat/   frame_0.png ...     摸头（单次）
      happy/ frame_0.png ...     开心（单次）

生成提示词可以按角色微调（见 ACTIONS 中的 prompt 字段）。
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pet_app import matting                       # noqa: E402
from pet_app.utils import assets_dir              # noqa: E402

API_BASE = "https://api.siliconflow.cn/v1"
MODEL = "Wan-AI/Wan2.2-I2V-A14B"

# 每个动作的生成参数（提示词强调"缓慢轻柔"——AI 生成的视频动作
# 幅度与速率完全按提示词控制，不自然通常源于动作过快）
ACTIONS = {
    "idle": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，安静站立，"
                  "身体随呼吸极其缓慢、轻柔地起伏，衣物、发丝和配饰以非常缓慢的"
                  "速度轻微摆动，动作幅度极小，节奏舒缓自然，"
                  "可无缝循环。纯白色背景，无任何文字",
    },
    "walk": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，在原地走路的循环步态，"
                  "步态缓慢而自然，身体轻微起伏，双臂小幅摆动，发丝和裙摆缓慢地"
                  "随之轻摆，节奏舒缓，可无缝循环。纯白色背景，无任何文字",
    },
    "jump": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，缓慢地轻轻原地跳起一次"
                  "再落下，裙摆和发丝随之缓慢地轻柔飘动，动作流畅自然，"
                  "节奏舒缓。纯白色背景，无任何文字",
    },
    "pat": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，被人摸头时开心地眯眼微笑，"
                  "缓慢地轻轻点头，发丝轻柔微晃，动作温柔自然，"
                  "节奏舒缓。纯白色背景，无任何文字",
    },
    "happy": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，开心地轻轻拍手，"
                  "身体缓慢地微微晃动，笑容自然，动作轻柔，"
                  "节奏舒缓。纯白色背景，无任何文字",
    },
}


def _post_json(url, payload, api_key, timeout=120):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url, api_key, timeout=30):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_video_status(api_key, request_id):
    """查询单个任务状态（供调试/手动续查）。"""
    return _post_json(f"{API_BASE}/video/status", {"requestId": request_id},
                      api_key, timeout=30)


def submit_video(api_key, image_path, prompt, size="720x1280") -> str:
    """提交图生视频任务，返回 requestId。"""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = {
        "model": getattr(sys.modules[__name__], "_MODEL", MODEL),
        "prompt": prompt,
        "image": f"data:image/png;base64,{b64}",
        "image_size": size,
        "negative_prompt": "背景复杂，多余物品，文字，水印，多个人，角色外观改变，"
                           "动作过快，快速晃动，动作幅度过大，身体变形，手指畸形",
    }
    # 官方接口为 /video/submit（若服务端 404 再尝试 /video/submissions）
    for endpoint in ("/video/submit", "/video/submissions"):
        try:
            data = _post_json(API_BASE + endpoint, payload, api_key, timeout=300)
            rid = data.get("requestId") or data.get("id")
            if rid:
                return rid
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        except urllib.error.URLError:
            raise
    raise RuntimeError("视频生成接口调用失败，请检查网络与 API Key")


def wait_video(api_key, request_id, timeout_s=900) -> str:
    """轮询任务状态（官方接口为 POST /video/status），返回视频 URL。"""
    deadline = time.time() + timeout_s
    url = f"{API_BASE}/video/status"
    while time.time() < deadline:
        # 官方返回: {status: Succeed|InQueue|InProgress|Failed, results: {videos: [{url}]}}
        data = _post_json(url, {"requestId": request_id}, api_key, timeout=30)
        status = data.get("status", "")
        if status == "Succeed":
            videos = (data.get("results") or {}).get("videos") or []
            if videos and videos[0].get("url"):
                return videos[0]["url"]
            raise RuntimeError(f"任务成功但未返回视频链接: {data}")
        if status == "Failed":
            raise RuntimeError(f"视频生成失败: {data.get('reason') or data}")
        print(f"  状态 {status or '未知'}，等待 10 秒…")
        time.sleep(10)
    raise RuntimeError("等待超时：任务可能仍在排队，可稍后在平台查看结果")


def download(url, dst):
    with urllib.request.urlopen(url, timeout=300) as resp, open(dst, "wb") as f:
        f.write(resp.read())


def extract_frames(video_path, count):
    """cv2 抽帧：均匀取 count 帧，返回 BGR 数组列表。"""
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    frames = []
    for i in range(count):
        idx = int(total * i / count)
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
    cap.release()
    return frames


def extract_and_save(video_path, action, frames_n=None):
    """从视频按 10fps（与程序播放帧率一致）抽帧 → AI 抠图 → 保存帧素材。

    帧数未指定时按视频时长 × 10fps 计算（上限 60），保证播放速度与
    AI 生成速度一致。此前固定抽 8 帧会把 5 秒视频压缩成 0.8 秒播放，
    导致动作速率过快、不自然。
    """
    cap = cv2.VideoCapture(video_path)
    vfps = cap.get(cv2.CAP_PROP_FPS) or 24
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    duration = total / vfps
    cap.release()
    if frames_n is None:
        frames_n = max(8, min(60, int(round(duration * 10))))
    print(f"  视频 {duration:.1f}s → 抽帧 ×{frames_n}（10fps）+ 逐帧抠图…")
    import process_character as pc
    frames = extract_frames(video_path, frames_n)
    out_dir = os.path.join(assets_dir, "animations", action)
    os.makedirs(out_dir, exist_ok=True)
    model = pc.resolve_model("auto")
    # 主形象尺寸（用于把保护区坐标按比例映射到帧画布）
    main_path = os.path.join(assets_dir, "pet.png")
    main_size = Image.open(main_path).size if os.path.isfile(main_path) else None

    for i, frame in enumerate(frames):
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        # 与主形象相同的 AI 语义分割抠图（避免人物身上出现小空缺）
        try:
            alpha = pc.segment(np.asarray(img.convert("RGB")), model, verbose=False)
            # 额外清理：远离深色内容的近白像素（isnet 掩码中残留的
            # 背景口袋 = 人物背后的白色块）
            gray = np.asarray(img.convert("L"))
            nonwhite = (gray < 235).astype(np.uint8)
            band = cv2.dilate(
                nonwhite, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21)))
            far = (gray >= 235) & (band == 0)
            if main_size:                       # 保护区按比例映射到帧画布
                sx = gray.shape[1] / main_size[0]
                sy = gray.shape[0] / main_size[1]
                protect = np.zeros_like(far, dtype=bool)
                for (x0, y0, x1, y1) in pc.PROTECT_REGIONS:
                    protect[int(y0 * sy):int(y1 * sy),
                            int(x0 * sx):int(x1 * sx)] = True
                far &= ~protect
            alpha[far] = 0
            img = img.convert("RGBA")
            img.putalpha(Image.fromarray(alpha))
        except Exception as e:                        # noqa: BLE001
            print(f"  第{i}帧抠图失败，保留原帧: {e}")
            img = img.convert("RGBA")
        img.save(os.path.join(out_dir, f"frame_{i}.png"))
    print(f"  完成 → {out_dir}")


def process_action(api_key, image_path, action, cfg, out_root, shots=1,
                   frames_n=None):
    """单动作：生成视频（可多候选）→ 保留原片 → 抽帧抠图。"""
    raw_dir = os.path.join(assets_dir, "animations", "_raw")
    os.makedirs(raw_dir, exist_ok=True)
    for shot in range(shots):
        label = action if shots == 1 else f"{action}_{shot + 1}"
        print(f"[{label}] 提交图生视频任务…")
        rid = submit_video(api_key, image_path, cfg["prompt"])
        print(f"[{label}] 任务 {rid}，等待生成（约 1-5 分钟）…")
        url = wait_video(api_key, rid)
        video = os.path.join(raw_dir, f"{label}.mp4")
        download(url, video)
        print(f"[{label}] 视频已保存（保留原片）: {video}")
        if shots == 1:
            extract_and_save(video, action, frames_n)


def main():
    args = sys.argv[1:]
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if "--api-key" in args:
        api_key = args[args.index("--api-key") + 1]

    # 从已保留的原片重新抽帧（本地操作，不需要 API Key）
    if "--from-raw" in args:
        idx = args.index("--from-raw")
        video = args[idx + 1]
        action = args[idx + 2] if len(args) > idx + 2 and args[idx + 2] in ACTIONS else "idle"
        frame_override = int(args[args.index("--frames") + 1]) if "--frames" in args else None
        if not os.path.isfile(video):
            print(f"找不到视频: {video}")
            sys.exit(1)
        extract_and_save(video, action, frame_override)
        print("桌宠重启后生效。")
        sys.exit(0)

    if not api_key:
        print("缺少 API Key：请设置环境变量 SILICONFLOW_API_KEY 或使用 --api-key 传入")
        sys.exit(1)
    # 查询某个历史任务的状态（排查用）
    if "--check-status" in args:
        rid = args[args.index("--check-status") + 1]
        data = check_video_status(api_key, rid)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        sys.exit(0)
    model = MODEL
    if "--model" in args:
        model = args[args.index("--model") + 1]
    # 把模型传给提交函数（闭包变量）
    global _MODEL
    _MODEL = model
    print(f"使用模型: {model}")
    action_filter = None
    if "--action" in args:
        action_filter = args[args.index("--action") + 1]
    frame_override = None
    if "--frames" in args:
        frame_override = int(args[args.index("--frames") + 1])
    shots = int(args[args.index("--shots") + 1]) if "--shots" in args else 1

    image_path = os.path.join(assets_dir, "pet.png")
    if not os.path.isfile(image_path):
        print(f"缺少形象文件 {image_path}")
        sys.exit(1)
    # 抠图素材须为白底：先生成一张白底版本
    img = Image.open(image_path).convert("RGBA")
    base = Image.new("RGBA", img.size, (255, 255, 255, 255))
    base.alpha_composite(img)
    white_bg = os.path.join(assets_dir, "_white_bg_tmp.png")
    base.convert("RGB").save(white_bg)

    for action, cfg in ACTIONS.items():
        if action_filter and action != action_filter:
            continue
        try:
            process_action(api_key, white_bg, action, cfg, assets_dir,
                           shots=shots, frames_n=frame_override)
        except Exception as e:                        # noqa: BLE001
            print(f"[{action}] 失败: {e}")
    if shots > 1:
        print(f"已生成 {shots} 个候选视频，保存在 assets/animations/_raw/，"
              f"挑选满意的后用 --from-raw 抽取帧素材。")

    if os.path.isfile(white_bg):
        os.remove(white_bg)
    print("全部完成。桌宠重启后将自动使用帧动画。")


if __name__ == "__main__":
    main()
