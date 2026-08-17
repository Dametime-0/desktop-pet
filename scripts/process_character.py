# -*- coding: utf-8 -*-
"""角色素材一键处理：去水印 → 智能抠图 → 生成形象与图标。

用法：
    python scripts/process_character.py <图片路径> [--keep-watermark] [--model u2net|isnet-general-use]

抠图策略（保证人物完整）：
1. rembg AI 掩码（u2net / isnet-general-use，需 pip install rembg onnxruntime）
   与低容差 flood-fill 掩码取并集——过曝/浅色皮肤（如面部高光）不会被误删；
2. 并集掩码做闭运算弥合发丝缺口、填充内部孔洞、仅保留最大前景连通域；
3. 用源图亮度再做一次"近白连通"泛洪，去掉连到外部的白底残留（白晕），
   但 PROTECT_REGIONS 保护区内强制保留（面部等易过曝区域）；
4. 边缘高斯羽化后裁剪保存。

参数说明：
- WATERMARK_BAND   水印所在斜带（两点连线 ± 半宽），用于 SAMPLE 类灰字水印检测；
- PROTECT_REGIONS  强制不透明区域（源图坐标 x0,y0,x1,y1），用于保护过曝的面部等。
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

# 强制不透明保护区（面部/颈部等浅色区域），源图坐标 (x0, y0, x1, y1)
PROTECT_REGIONS = [(370, 170, 780, 850)]
# 白晕清理：亮度 ≥ 255 - HALO_TOL 且从边缘连通的像素视为背景残留
HALO_TOL = 6
# 白色残留阈值：亮度 ≥ WHITE_TH 且远离人物特征的大块视为背景缝隙/阴影残留
# （背景缝隙往往带浅灰阴影 235~255；人物主体亮度多在 220 以下，脸部受保护区保护）
WHITE_TH = 230
# 闭运算核大小（弥合发丝缝隙）
CLOSE_KERNEL = 13


def detect_watermark_mask(img_rgb: np.ndarray) -> np.ndarray:
    """返回水印掩码（uint8 0/255）。"""
    import cv2
    a = img_rgb.astype(int)
    h, w = a.shape[:2]
    sat = a.max(axis=2) - a.min(axis=2)
    gray = a.mean(axis=2)
    cand = (sat < SAT_MAX) & (gray > GRAY_MIN) & (gray < GRAY_MAX)

    (x0, y0), (x1, y1) = WATERMARK_BAND
    dx, dy = x1 - x0, y1 - y0
    length = max(1.0, float(np.hypot(dx, dy)))
    ux, uy = dx / length, dy / length
    ys, xs = np.mgrid[0:h, 0:w]
    t = np.clip(((xs - x0) * ux + (ys - y0) * uy) / length, 0.0, 1.0)
    px = x0 + t * dx
    py = y0 + t * dy
    dist = np.abs((xs - px) * uy - (ys - py) * ux)
    cand = cand & (dist <= HALF_WIDTH)

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


def _luma_flood_mask(gray: np.ndarray, tol: int) -> np.ndarray:
    """从图像边缘泛洪（cv2 C++ 实现，速度快）：与白色(255)亮度差 <= tol 的连通区域。"""
    import cv2
    h, w = gray.shape
    pad = cv2.copyMakeBorder(gray, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=255)
    # 注意：floodFill 的掩码必须比图像大 2 圈（每维 +4 相对原图）
    ff = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(pad, ff, (0, 0), 0, loDiff=tol, upDiff=tol,
                  flags=8 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY)
    return ff[2:-2, 2:-2] > 0


def segment(img_rgb: np.ndarray, model: str = "u2net") -> np.ndarray:
    """智能抠图，返回 0-255 的 alpha 数组（保证人物完整）。"""
    import cv2
    gray = np.asarray(Image.fromarray(img_rgb).convert("L"))
    h, w = gray.shape

    # 1) rembg AI 掩码（模型文件缺失/下载失败则只用泛洪，避免卡在慢速下载）
    ub_a = None
    model_path = os.path.join(os.path.expanduser("~"), ".u2net", f"{model}.onnx")
    if os.path.isfile(model_path):
        try:
            from rembg import new_session, remove
            out = remove(Image.fromarray(img_rgb),
                         session=new_session(model)).convert("RGBA")
            ub_a = np.asarray(out.getchannel("A"))
            print(f"rembg({model}) 掩码完成")
        except Exception as e:                        # noqa: BLE001
            print(f"rembg 失败({e})，仅使用 flood-fill")
    else:
        print(f"未找到模型 {model_path}，跳过 rembg（首次运行会自动下载，"
              f"网络慢可稍后重试）")

    # 2) 低容差泛洪掩码（保留浅色皮肤）
    ff = matting.remove_background(Image.fromarray(img_rgb), "floodfill", 10)
    ff_a = np.asarray(ff.getchannel("A"))

    union = np.maximum(ff_a, ub_a if ub_a is not None else 0).astype(np.uint8)

    # 3) 白晕清理：亮度近白且从边缘连通的像素抹掉（保护区除外）
    white = _luma_flood_mask(gray, HALO_TOL)
    remove_mask = white & (union > 0)
    for (x0, y0, x1, y1) in PROTECT_REGIONS:
        remove_mask[y0:y1, x0:x1] = False
    union[remove_mask] = 0

    # 4) 闭运算弥合缺口 → 填充内部孔洞 → 仅保留最大连通域
    mask = (union > 128).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (CLOSE_KERNEL, CLOSE_KERNEL))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    holes = (1 - closed)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(holes, 8)
    filled = closed.copy()
    n_fill = 0
    for i in range(1, n):
        x, y, bw, bh, _area = stats[i]
        if x > 0 and y > 0 and x + bw < w and y + bh < h:
            filled[labels == i] = 1
            n_fill += 1
    n2, labels2, stats2, _ = cv2.connectedComponentsWithStats(filled, 8)
    if n2 > 1:
        largest = 1 + int(np.argmax(stats2[1:, cv2.CC_STAT_AREA]))
        filled = (labels2 == largest).astype(np.uint8)
    print(f"内部孔洞填充 {n_fill} 处")

    # 5) 保护区强制不透明
    for (x0, y0, x1, y1) in PROTECT_REGIONS:
        filled[y0:y1, x0:x1] = 1

    # 6) 白色残留清理（背景缝隙/脚下阴影被误保留的白块）
    #    a. 远离人物特征（深色内容 5px 以外）的浅色像素
    nonwhite = gray < WHITE_TH
    band = cv2.dilate(nonwhite.astype(np.uint8),
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    white_far = (gray >= WHITE_TH) & (band == 0) & (filled > 0)
    for (x0, y0, x1, y1) in PROTECT_REGIONS:
        white_far[y0:y1, x0:x1] = False
    filled[white_far] = 0
    #    b. 大块浅色连通域（面积 >= 800）整体移除，但保护区内像素除外——
    #       逐像素保护：即使白块与面部皮肤连成一个大组件，也只移除保护区外的部分
    ow = ((filled > 0) & (gray >= WHITE_TH)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ow, 8)
    protected_mask = np.zeros_like(filled, dtype=bool)
    for (x0, y0, x1, y1) in PROTECT_REGIONS:
        protected_mask[y0:y1, x0:x1] = True
    n_rm = 0
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 800:
            comp = (labels == i) & (~protected_mask)
            filled[comp] = 0
            n_rm += 1
    if n_rm:
        print(f"白色残留块移除 {n_rm} 处")

    # 7) 边缘羽化
    soft = cv2.GaussianBlur(filled.astype(np.float32), (3, 3), 0)
    return (soft * 255).astype(np.uint8)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    keep_wm = "--keep-watermark" in sys.argv
    model = "auto"
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
    if model == "auto":                    # 自动选择本地已有模型
        home = os.path.expanduser("~")
        if os.path.isfile(os.path.join(home, ".u2net", "isnet-general-use.onnx")):
            model = "isnet-general-use"
        elif os.path.isfile(os.path.join(home, ".u2net", "u2net.onnx")):
            model = "u2net"
    print(f"抠图模型: {model}")
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

    # 2) 智能抠图（保证人物完整）
    alpha = segment(clean, model)
    pet = clean_img.convert("RGBA")
    pet.putalpha(Image.fromarray(alpha))
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
