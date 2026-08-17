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

# 每个动作的生成参数（提示词按"白底、同角色、循环动作"设计，方便抠图与衔接）
ACTIONS = {
    "idle": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，安静地站立，"
                  "身体随呼吸轻微起伏，发丝和裙摆微微飘动，动作缓慢自然，"
                  "可无缝循环，纯白色背景，无任何文字",
        "frames": 8,
    },
    "walk": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，在原地走路的循环步态，"
                  "身体轻微上下起伏，双臂自然摆动，发丝和裙摆随之摆动，"
                  "可无缝循环，纯白色背景，无任何文字",
        "frames": 8,
    },
    "jump": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，轻轻向上跳起一次再落下，"
                  "裙摆和发丝随之飘起，动作流畅自然，纯白色背景，无任何文字",
        "frames": 6,
    },
    "pat": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，被人摸头时开心地眯眼微笑，"
                  "轻轻点头，发丝微微晃动，动作温柔自然，纯白色背景，无任何文字",
        "frames": 6,
    },
    "happy": {
        "prompt": "画面中的角色保持完全相同的姿势和外观，开心地轻轻拍手，"
                  "身体微微晃动，笑容灿烂，动作活泼自然，纯白色背景，无任何文字",
        "frames": 6,
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


def submit_video(api_key, image_path, prompt, size="720x1280") -> str:
    """提交图生视频任务，返回 requestId。"""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "image": f"data:image/png;base64,{b64}",
        "image_size": size,
        "negative_prompt": "背景复杂，多余物品，文字，水印，多个人，角色外观改变",
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
    """轮询任务状态，返回视频 URL。"""
    deadline = time.time() + timeout_s
    # 状态接口兼容 /video/status 与 /video/submissions/{id}/status
    urls = [f"{API_BASE}/video/status?requestId={request_id}",
            f"{API_BASE}/video/submissions/{request_id}/status"]
    while time.time() < deadline:
        last_err = None
        for url in urls:
            try:
                data = _get_json(url, api_key)
                status = data.get("status") or (data.get("data") or {}).get("status")
                if status in ("Succeed", "succeed", "Success", "success"):
                    results = (data.get("results") or data.get("data", {}).get("results")
                               or [])
                    if results and results[0].get("url"):
                        return results[0]["url"]
                if status in ("Failed", "failed", "Fail", "fail"):
                    raise RuntimeError(f"视频生成失败: {data}")
                break
            except urllib.error.HTTPError as e:
                last_err = e
        time.sleep(10)
        print("  …生成中，等待 10 秒")
    raise RuntimeError(f"等待超时（最后错误: {last_err}）")


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


def process_action(api_key, image_path, action, cfg, out_root):
    """单动作：生成视频 → 抽帧 → 抠图 → 保存。"""
    print(f"[{action}] 提交图生视频任务…")
    rid = submit_video(api_key, image_path, cfg["prompt"])
    print(f"[{action}] 任务 {rid}，等待生成（约 1-5 分钟）…")
    url = wait_video(api_key, rid)
    video = os.path.join(out_root, f"_{action}.mp4")
    download(url, video)
    print(f"[{action}] 视频已下载，抽帧 ×{cfg['frames']}…")
    frames = extract_frames(video, cfg["frames"])
    out_dir = os.path.join(assets_dir, "animations", action)
    os.makedirs(out_dir, exist_ok=True)
    for i, frame in enumerate(frames):
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        # 逐帧抠图（视频模型输出白底，泛洪即可；失败保留原帧）
        try:
            img = matting.remove_background(img, "floodfill", 24)
        except Exception as e:                        # noqa: BLE001
            print(f"  第{i}帧抠图失败: {e}")
        img.save(os.path.join(out_dir, f"frame_{i}.png"))
    os.remove(video)
    print(f"[{action}] 完成 → {out_dir}")


def main():
    args = sys.argv[1:]
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if "--api-key" in args:
        api_key = args[args.index("--api-key") + 1]
    if not api_key:
        print("缺少 API Key：请设置环境变量 SILICONFLOW_API_KEY 或使用 --api-key 传入")
        sys.exit(1)
    action_filter = None
    if "--action" in args:
        action_filter = args[args.index("--action") + 1]
    frame_override = None
    if "--frames" in args:
        frame_override = int(args[args.index("--frames") + 1])

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
        if frame_override:
            cfg = dict(cfg, frames=frame_override)
        try:
            process_action(api_key, white_bg, action, cfg, assets_dir)
        except Exception as e:                        # noqa: BLE001
            print(f"[{action}] 失败: {e}")

    if os.path.isfile(white_bg):
        os.remove(white_bg)
    print("全部完成。桌宠重启后将自动使用帧动画。")


if __name__ == "__main__":
    main()
