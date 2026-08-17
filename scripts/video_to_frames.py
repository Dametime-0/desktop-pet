# -*- coding: utf-8 -*-
"""本地视频/GIF → 动画帧素材（不需要 AI 也可用）。

任何动作短视频（如手机拍的、其他 AI 工具生成的、GIF 动图）都能转为
桌宠帧动画素材：抽帧 → 白底抠图 → 按动作目录保存。

用法：
    python scripts/video_to_frames.py <视频或GIF路径> <动作名> [--frames 8]

动作名对应：idle / walk / jump / pat / happy（桌宠按目录名匹配动作）。
"""
import os
import sys

import cv2
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pet_app import matting                       # noqa: E402
from pet_app.utils import assets_dir              # noqa: E402

ACTIONS = ("idle", "walk", "jump", "pat", "happy", "spin", "dance", "shake")


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[1] not in ACTIONS:
        print(__doc__)
        sys.exit(1)
    src, action = args[0], args[1]
    frames_n = 8
    if "--frames" in args:
        frames_n = int(args[args.index("--frames") + 1])
    if not os.path.isfile(src):
        print(f"找不到文件: {src}")
        sys.exit(1)

    cap = cv2.VideoCapture(src)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out_dir = os.path.join(assets_dir, "animations", action)
    os.makedirs(out_dir, exist_ok=True)
    saved = 0
    for i in range(frames_n):
        idx = int(total * i / frames_n)
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        try:
            img = matting.remove_background(img, "floodfill", 24)
        except Exception as e:                        # noqa: BLE001
            print(f"第{i}帧抠图失败: {e}")
        img.save(os.path.join(out_dir, f"frame_{saved}.png"))
        saved += 1
    cap.release()
    print(f"完成：{saved} 帧 → {out_dir}")
    print("桌宠重启后该动作将自动使用帧动画。")


if __name__ == "__main__":
    main()
