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

# 强制不透明保护区（过曝面部/颈部、双腿等 AI 掩码会误删的浅色区域）
# 源图坐标 (x0, y0, x1, y1)
PROTECT_REGIONS = [
    (370, 170, 780, 850),     # 面部 + 颈部
    (290, 1490, 440, 1960),   # 左腿（含白袜）
    (650, 1490, 795, 1960),   # 右腿
]
# 白晕清理：亮度 ≥ 255 - HALO_TOL 且从边缘连通的像素视为背景残留
HALO_TOL = 6
# 白色残留阈值：亮度 ≥ WHITE_TH 且通过近白路径与背景连通的像素视为背景残留
# （阈值取 215 以穿过边缘抗锯齿像素；人物浅色区域由 PROTECT_REGIONS 保护）
WHITE_TH = 215
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


_SESSION_CACHE = {}


def resolve_model(explicit: str = "auto") -> str:
    """auto 时选择本地已有模型（isnet 优先）。"""
    if explicit not in ("", "auto"):
        return explicit
    home = os.path.expanduser("~")
    if os.path.isfile(os.path.join(home, ".u2net", "isnet-general-use.onnx")):
        return "isnet-general-use"
    if os.path.isfile(os.path.join(home, ".u2net", "u2net.onnx")):
        return "u2net"
    return "u2net"


def _get_session(model: str):
    """缓存 rembg 会话（模型加载开销大，逐帧抠图必须复用）。"""
    if model not in _SESSION_CACHE:
        from rembg import new_session
        _SESSION_CACHE[model] = new_session(model)
    return _SESSION_CACHE[model]


def segment(img_rgb: np.ndarray, model: str = "u2net",
            verbose: bool = True) -> np.ndarray:
    """智能抠图，返回 0-255 的 alpha 数组（保证人物完整）。

    以 rembg(isnet) 语义分割为主——它能正确区分"人物身上的白色内容"
    （白袜、过曝皮肤）与"背景缝隙白块"，从根本上避免误删/误留；
    模型不可用时退回 flood-fill + 背景残留清理。
    """
    import cv2
    gray = np.asarray(Image.fromarray(img_rgb).convert("L"))
    h, w = gray.shape

    # 1) rembg AI 掩码（模型文件缺失/下载失败则退回泛洪）
    ub_a = None
    model_path = os.path.join(os.path.expanduser("~"), ".u2net", f"{model}.onnx")
    if os.path.isfile(model_path):
        try:
            from rembg import remove
            out = remove(Image.fromarray(img_rgb),
                         session=_get_session(model)).convert("RGBA")
            ub_a = np.asarray(out.getchannel("A"))
            if verbose:
                print(f"rembg({model}) 掩码完成")
        except Exception as e:                        # noqa: BLE001
            if verbose:
                print(f"rembg 失败({e})，退回 flood-fill")
    else:
        if verbose:
            print(f"未找到模型 {model_path}，跳过 rembg（首次运行会自动下载）")

    if ub_a is not None:
        base = ub_a.copy()          # rembg 返回只读数组，这里需要写入
    else:
        # 兜底：低容差泛洪 + 白晕清理 + 背景残留清理
        ff = matting.remove_background(Image.fromarray(img_rgb), "floodfill", 10)
        base = np.asarray(ff.getchannel("A")).astype(np.uint8)
        white = _luma_flood_mask(gray, HALO_TOL)
        base[white & (base > 0)] = 0
        light = (gray >= WHITE_TH).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(light, 8)
        border_labels = {i for i in range(1, n)
                         if (stats[i][0] <= 0 or stats[i][1] <= 0
                             or stats[i][0] + stats[i][2] >= w
                             or stats[i][1] + stats[i][3] >= h)}
        if border_labels:
            remove = np.isin(labels, list(border_labels)) & (base > 0)
            base[remove] = 0

    # 2) 白色残留清理：不透明且近白、并且通过近白像素与掩码外部连通的像素
    #    = 背景缝隙/手臂旁白块。人物身上的白色内容（白袜/过曝皮肤）被深色
    #    描边包围，白色泛洪无法穿过，因此不会被误删。
    white = ((gray >= WHITE_TH).astype(np.uint8) * 255)   # 注意 0/255 值，floodFill 按值连通
    pad = cv2.copyMakeBorder(white, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=255)
    ff_mask = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(pad, ff_mask, (0, 0), 0, loDiff=0, upDiff=0,
                  flags=8 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY)
    reached = ff_mask[2:-2, 2:-2] > 0
    remove = reached & (base > 128)
    for (x0, y0, x1, y1) in PROTECT_REGIONS:
        remove[y0:y1, x0:x1] = False
    base[remove] = 0

    # 3) 仅保留最大连通域（不做闭运算/全局孔洞填充——
    #    它们会把背景缝隙封死再填成白块，也救不回被 AI 误删的白袜）
    mask = (base > 128).astype(np.uint8)
    n2, labels2, stats2, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n2 > 1:
        largest = 1 + int(np.argmax(stats2[1:, cv2.CC_STAT_AREA]))
        filled = (labels2 == largest).astype(np.uint8)
    else:
        filled = mask

    # 3) 保护区：强制不透明（区内孔洞一并填平，保护区内没有背景）
    for (x0, y0, x1, y1) in PROTECT_REGIONS:
        filled[y0:y1, x0:x1] = 1

    # 4) 边缘羽化
    soft = cv2.GaussianBlur(filled.astype(np.float32), (3, 3), 0)
    return (soft * 255).astype(np.uint8)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    keep_wm = "--keep-watermark" in sys.argv
    model = resolve_model(sys.argv[sys.argv.index("--model") + 1]
                          if "--model" in sys.argv else "auto")
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
