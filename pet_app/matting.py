# -*- coding: utf-8 -*-
"""图片导入与自动抠图。

内置轻量抠图算法（flood-fill 边缘泛洪，仅依赖 Pillow）：
1. 缩小图像加速，取四条边像素的众数颜色作为背景参考色；
2. 从所有边缘像素做 BFS 泛洪，与参考色距离 <= 容差的像素视为背景；
3. 掩码放大回原尺寸并做模糊羽化，得到平滑透明的边缘；
4. 若已安装 rembg 且配置 method=auto，则优先使用 AI 抠图（效果更好）。

处理结果统一保存为 assets/pet.png。
"""
import os
from collections import Counter, deque

from PIL import Image, ImageFilter

from .utils import assets_dir, log

MAX_SIDE = 320          # 泛洪分析时的最大边长（提速）
ALPHA_THRESHOLD = 16    # 裁剪时判定为"内容"的 alpha 阈值


def _border_ref_color(img: Image.Image):
    """取四条边像素的众数颜色作为背景参考色。"""
    w, h = img.size
    px = img.load()
    colors = []
    for x in range(w):
        colors.append(px[x, 0])
        colors.append(px[x, h - 1])
    for y in range(h):
        colors.append(px[0, y])
        colors.append(px[w - 1, y])
    return Counter(colors).most_common(1)[0][0]


def _flood_fill_mask(img_rgb: Image.Image, tolerance: int,
                     ref=None) -> Image.Image:
    """对缩小后的图做边缘泛洪，返回 L 模式掩码（255=保留前景）。

    ref 为背景参考色（如 AI 生成约定的纯色背景），缺省时自动取边缘众数色。
    """
    w, h = img_rgb.size
    px = img_rgb.load()
    if ref is None:
        ref = _border_ref_color(img_rgb)
    tol2 = tolerance * tolerance * 3

    def is_bg(c):
        return sum((a - b) ** 2 for a, b in zip(c, ref)) <= tol2

    bg = bytearray(w * h)
    queue = deque()
    for x in range(w):                      # 所有边缘像素入队
        for y in (0, h - 1):
            queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        idx = y * w + x
        if bg[idx] or not is_bg(px[x, y]):
            continue
        bg[idx] = 1
        if x > 0:
            queue.append((x - 1, y))
        if x < w - 1:
            queue.append((x + 1, y))
        if y > 0:
            queue.append((x, y - 1))
        if y < h - 1:
            queue.append((x, y + 1))

    mask = Image.new("L", (w, h), 255)
    mask.putdata([0 if v else 255 for v in bg])
    return mask


def _has_alpha(img: Image.Image) -> bool:
    """是否已含有效透明通道。"""
    if img.mode != "RGBA":
        return False
    return any(a < 255 for a in img.getchannel("A").getdata())


def _hint_matches_corners(img_rgb: Image.Image, bg_hint, tolerance: int) -> bool:
    """校验图像四角与 bg_hint 接近（模型可能没按约定生成纯色背景）。"""
    w, h = img_rgb.size
    px = img_rgb.load()
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    tol2 = tolerance * tolerance * 3
    return all(sum((a - b) ** 2 for a, b in zip(c, bg_hint)) <= tol2
               for c in corners)


def remove_background(img: Image.Image, method: str = "auto",
                      tolerance: int = 32, bg_hint=None) -> Image.Image:
    """去除背景，返回 RGBA 图像。已含透明通道的图不做处理。

    bg_hint：背景参考色（如 AI 生成的约定纯色背景），四角校验通过才使用，
    否则回退边缘众数色自动识别。
    """
    img = img.convert("RGBA")
    if _has_alpha(img):
        return img

    if method == "auto":
        try:
            from rembg import remove      # 可选依赖，未安装不影响运行
            return remove(img.convert("RGB")).convert("RGBA")
        except ImportError:
            log.info("未安装 rembg，使用内置 flood-fill 抠图")
        except Exception as e:            # rembg 模型下载失败等异常时回退
            log.warning("rembg 抠图失败(%s)，回退内置算法", e)

    rgb = img.convert("RGB")
    small = rgb.copy()
    scale = 1.0
    if max(small.size) > MAX_SIDE:
        scale = MAX_SIDE / max(small.size)
        small = small.resize((max(1, int(small.width * scale)),
                              max(1, int(small.height * scale))), Image.BILINEAR)
    ref = None
    if bg_hint is not None and _hint_matches_corners(rgb, bg_hint, tolerance):
        ref = tuple(bg_hint)
    else:
        log.info("bg_hint 与四角不符，回退边缘众数色")
    mask = _flood_fill_mask(small, tolerance, ref)
    if img.size != mask.size:             # 放大回原尺寸并羽化
        mask = mask.resize(img.size, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.2))
    img.putalpha(mask)
    return img


def trim_to_content(img: Image.Image, pad: int = 6) -> Image.Image:
    """按 alpha 包围盒裁剪，去掉多余的透明边。"""
    bbox = img.getchannel("A").point(lambda a: 255 if a > ALPHA_THRESHOLD else 0).getbbox()
    if bbox is None:
        return img
    left = max(0, bbox[0] - pad)
    top = max(0, bbox[1] - pad)
    right = min(img.width, bbox[2] + pad)
    bottom = min(img.height, bbox[3] + pad)
    return img.crop((left, top, right, bottom))


def process_image_obj(img: Image.Image, method: str = "auto",
                      tolerance: int = 32, dst: str = None,
                      bg_hint=None) -> str:
    """抠图 → 裁剪 → 保存为 PNG，返回保存路径。

    img 为内存图像（AI 流水线直接传入，避免中间落盘）；
    dst 缺省为 assets/pet.png。
    """
    img = remove_background(img, method, tolerance, bg_hint)
    img = trim_to_content(img)
    if dst is None:
        os.makedirs(assets_dir, exist_ok=True)
        dst = os.path.join(assets_dir, "pet.png")
    img.save(dst)
    log.info("形象已处理并保存: %s", dst)
    return dst


def process_image(src_path: str, method: str = "auto",
                  tolerance: int = 32) -> str:
    """从文件导入图片：打开 → 抠图 → 裁剪 → 保存为 assets/pet.png。"""
    dst = process_image_obj(Image.open(src_path), method, tolerance)
    log.info("来源图片: %s", src_path)
    return dst
