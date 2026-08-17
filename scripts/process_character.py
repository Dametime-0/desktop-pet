# -*- coding: utf-8 -*-
"""角色素材一键处理：去水印 → 抠图去背景 → 生成形象与图标。

用法：
    python scripts/process_character.py <图片路径> [--keep-watermark]

说明：
- 水印检测：在指定斜向区域内找"低饱和 + 中亮度"像素，用连通域面积
  过滤出文字笔画（SAMPLE 半透明灰字），再以 cv2 修复（Telea 算法）；
- 抠图：复用 pet_app.matting（纯色背景 flood-fill，白底效果最佳）；
- 产物：assets/pet.png（桌宠形象）与 assets/pet.ico（程序图标）。

参数 WATERMARK_BAND 为水印所在斜带（两点连线 ± 半宽），可按素材调整。
"""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pet_app import matting                       # noqa: E402

# 水印斜带：从 (x0,y0) 到 (x1,y1) 的直线，左右各 HALF_WIDTH 像素宽
WATERMARK_BAND = ((250, 620), (860, 950))
HALF_WIDTH = 70
# 文字笔画连通域面积范围（像素），过小是噪点、过大是衣物褶皱等正常内容
COMPONENT_AREA = (15, 8000)
# 水印候选像素：低饱和 + 中亮度
SAT_MAX, GRAY_MIN, GRAY_MAX = 25, 100, 235


def detect_watermark_mask(img_rgb: np.ndarray) -> np.ndarray:
    """返回水印掩码（uint8 0/255）。"""
    import cv2
    a = img_rgb.astype(int)
    h, w = a.shape[:2]
    sat = a.max(axis=2) - a.min(axis=2)
    gray = a.mean(axis=2)
    cand = (sat < SAT_MAX) & (gray > GRAY_MIN) & (gray < GRAY_MAX)

    # 限制在斜带内
    (x0, y0), (x1, y1) = WATERMARK_BAND
    dx, dy = x1 - x0, y1 - y0
    length = max(1.0, float(np.hypot(dx, dy)))
    ux, uy = dx / length, dy / length
    ys, xs = np.mgrid[0:h, 0:w]
    t = np.clip(((xs - x0) * ux + (ys - y0) * uy) / length, 0.0, 1.0)
    px = x0 + t * dx
    py = y0 + t * dy
    dist = np.abs((xs - px) * uy - (ys - py) * ux)   # 点到斜线距离
    in_band = dist <= HALF_WIDTH
    cand = cand & in_band

    # 连通域面积过滤：只保留文字笔画级别的小块
    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        (cand * 255).astype(np.uint8), connectivity=8)
    mask = np.zeros((h, w), dtype=np.uint8)
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if COMPONENT_AREA[0] <= area <= COMPONENT_AREA[1]:
            mask[labels == i] = 255
    return mask


def remove_watermark(img_rgb: np.ndarray) -> np.ndarray:
    """检测并修复水印区域，返回修复后的 RGB 数组。"""
    import cv2
    mask = detect_watermark_mask(img_rgb)
    coverage = 100.0 * (mask > 0).mean()
    print(f"水印掩码覆盖: {coverage:.2f}% 像素")
    if coverage < 0.001:
        print("未检测到明显水印，跳过修复")
        return img_rgb
    return cv2.inpaint(img_rgb, mask, 3, cv2.INPAINT_TELEA)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    keep_wm = "--keep-watermark" in sys.argv
    if not os.path.isfile(src):
        print(f"找不到图片: {src}")
        sys.exit(1)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(os.path.join(root, "assets"), exist_ok=True)

    img = Image.open(src).convert("RGB")
    print(f"源图: {src} {img.size}")

    # 1) 去水印
    arr = np.asarray(img)
    if keep_wm:
        print("--keep-watermark：跳过去水印")
        clean = arr
    else:
        clean = remove_watermark(arr)
    clean_img = Image.fromarray(clean)
    preview = os.path.join(root, "_selftest", "character_clean.jpg")
    os.makedirs(os.path.dirname(preview), exist_ok=True)
    clean_img.save(preview, quality=92)
    print(f"去水印预览: {preview}")

    # 2) 抠图去背景 + 裁剪（复用正式抠图逻辑）
    pet = matting.remove_background(clean_img, method="floodfill", tolerance=30)
    pet = matting.trim_to_content(pet)
    pet_path = os.path.join(root, "assets", "pet.png")
    pet.save(pet_path)
    print(f"形象已生成: {pet_path} {pet.size}")

    # 3) 程序图标
    ico_path = os.path.join(root, "assets", "pet.ico")
    pet.resize((256, 256), Image.LANCZOS).save(
        ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    print(f"图标已生成: {ico_path}")


if __name__ == "__main__":
    main()
