# -*- coding: utf-8 -*-
"""内置默认形象「团子」的程序化绘制（仅用 Pillow，无外部素材依赖）。

首次启动若 assets/pet.png 不存在，将自动生成默认形象；
scripts/generate_assets.py 也调用本模块预生成图片与图标。
绘制采用 4 倍超采样 + 降采样，保证边缘平滑。
"""
import os

from PIL import Image, ImageDraw, ImageFilter

from .utils import BUNDLED_ASSETS_DIR, assets_dir, log

# 配色（奶油团子风）
C_BODY_EDGE = (255, 224, 178)
C_BODY_CENTER = (255, 248, 235)
C_EAR = (255, 214, 160)
C_EAR_INNER = (255, 183, 197)
C_OUTLINE = (139, 109, 88)
C_EYE = (74, 59, 50)
C_BLUSH = (255, 158, 181)
C_SPROUT = (123, 201, 111)
C_PAW = (255, 236, 200)


def _lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _radial_ellipse(draw, cx, cy, rx, ry, c_edge, c_center, steps=48):
    """同心椭圆近似径向渐变填充。"""
    for i in range(steps):
        t = i / (steps - 1)
        rr, ryy = rx * (1.0 - t), ry * (1.0 - t)
        draw.ellipse([cx - rr, cy - ryy, cx + rr, cy + ryy],
                     fill=_lerp(c_edge, c_center, t))


def draw_default_pet(size: int = 512) -> Image.Image:
    """绘制默认桌宠形象，返回带透明通道的 RGBA 图像。"""
    s = size * 4                       # 4 倍超采样抗锯齿
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx, by = s * 0.5, s * 0.84         # 身体中心 / 底部
    r = s * 0.40                       # 身体半径

    # 头顶草芽（先画，会被身体遮住底部）
    stem_w = s * 0.035
    d.rounded_rectangle([cx - stem_w / 2, by - r * 1.52, cx + stem_w / 2, by - r * 1.18],
                        radius=stem_w / 2, fill=C_SPROUT)
    for dx, angle in ((-1, 1), (1, -1)):
        lx = cx + dx * r * 0.10
        ly = by - r * 1.40
        leaf = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ld = ImageDraw.Draw(leaf)
        ld.ellipse([lx - r * 0.16, ly - r * 0.26, lx + r * 0.16, ly + r * 0.12], fill=C_SPROUT)
        leaf = leaf.rotate(angle * 28, center=(lx, ly), resample=Image.BICUBIC)
        img.alpha_composite(leaf)

    # 耳朵（三角形，尖角朝上）
    for dx in (-1, 1):
        ex = cx + dx * r * 0.58
        tri = [(ex - r * 0.34, by - r * 0.55),
               (ex + r * 0.34, by - r * 0.55),
               (ex + dx * r * 0.22, by - r * 1.42)]
        d.polygon(tri, fill=C_EAR)
        inner = [(ex - r * 0.17, by - r * 0.62),
                 (ex + r * 0.17, by - r * 0.62),
                 (ex + dx * r * 0.11, by - r * 1.22)]
        d.polygon(inner, fill=C_EAR_INNER)

    # 身体（渐变圆）
    _radial_ellipse(d, cx, by, r, r, C_BODY_EDGE, C_BODY_CENTER)

    # 头顶高光
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.ellipse([cx - r * 0.38, by - r * 0.85, cx + r * 0.08, by - r * 0.55],
               fill=(255, 255, 255, 90))
    img.alpha_composite(hl)

    # 眼睛（黑色椭圆 + 高光点）
    for dx in (-1, 1):
        ex = cx + dx * r * 0.28
        ey = by - r * 0.10
        d.ellipse([ex - r * 0.095, ey - r * 0.16, ex + r * 0.095, ey + r * 0.16], fill=C_EYE)
        d.ellipse([ex + r * 0.035, ey - r * 0.10, ex + r * 0.075, ey + 0.02 * r],
                  fill=(255, 255, 255, 230))

    # 腮红
    for dx in (-1, 1):
        ex = cx + dx * r * 0.52
        ey = by + r * 0.02
        d.ellipse([ex - r * 0.11, ey - r * 0.07, ex + r * 0.11, ey + r * 0.07],
                  fill=C_BLUSH + (120,))

    # 嘴巴（微笑弧 + 小舌头）
    d.arc([cx - r * 0.14, by + r * 0.02, cx + r * 0.14, by + r * 0.26],
          200, 340, fill=C_OUTLINE, width=int(r * 0.045))
    d.ellipse([cx - r * 0.06, by + r * 0.15, cx + r * 0.06, by + r * 0.24],
              fill=(255, 140, 160))

    # 前爪
    for dx in (-1, 1):
        px = cx + dx * r * 0.24
        py = by + r * 0.62
        d.ellipse([px - r * 0.14, py - r * 0.09, px + r * 0.14, py + r * 0.09], fill=C_PAW)

    # 描边：将内容 alpha 向外膨胀一圈作为轮廓（MaxFilter 尺寸须为奇数）
    alpha = img.split()[3]
    dilate = int(s * 0.012) | 1
    outline_mask = alpha.filter(ImageFilter.MaxFilter(dilate))
    outline_img = Image.new("RGBA", (s, s), C_OUTLINE + (255,))
    base = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    base.paste(outline_img, (0, 0), outline_mask)
    base.alpha_composite(img)
    img = base

    return img.resize((size, size), Image.LANCZOS)


def ensure_default_image() -> str:
    """确保默认形象存在，返回图片路径。

    优先级：用户目录 assets/pet.png（用户导入/替换）→ 随包默认形象 → 现场绘制。
    """
    path = os.path.join(assets_dir, "pet.png")
    if not os.path.isfile(path):
        bundled = os.path.join(BUNDLED_ASSETS_DIR, "pet.png")
        if os.path.isfile(bundled) and os.path.normcase(bundled) != os.path.normcase(path):
            try:
                import shutil
                shutil.copyfile(bundled, path)
                log.info("已复制出厂形象: %s -> %s", bundled, path)
                return path
            except OSError as e:
                log.warning("出厂形象复制失败: %s", e)
        try:
            draw_default_pet().save(path)
            log.info("已生成默认形象: %s", path)
        except OSError as e:
            log.warning("默认形象生成失败: %s", e)
    return path


def ensure_icon() -> str:
    """确保程序图标存在，返回 assets/pet.ico 路径。"""
    path = os.path.join(assets_dir, "pet.ico")
    if not os.path.isfile(path):
        bundled = os.path.join(BUNDLED_ASSETS_DIR, "pet.ico")
        if os.path.isfile(bundled) and os.path.normcase(bundled) != os.path.normcase(path):
            try:
                import shutil
                shutil.copyfile(bundled, path)
                return path
            except OSError as e:
                log.warning("出厂图标复制失败: %s", e)
        try:
            img = draw_default_pet(256)
            img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                  (64, 64), (128, 128), (256, 256)])
        except OSError as e:
            log.warning("图标生成失败: %s", e)
    return path
