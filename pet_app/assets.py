# -*- coding: utf-8 -*-
"""形象与图标资源管理。

优先级：用户目录（assets/pet.png 可替换/导入）→ 随包资源（_internal/assets）→ 报错。
形象图片由 scripts/process_character.py 预处理生成（去水印 + 抠图），随包分发。
"""
import os
import shutil

from .utils import BUNDLED_ASSETS_DIR, assets_dir, log


def ensure_pet_image() -> str:
    """确保形象文件存在，返回路径；缺失时从随包资源复制。"""
    path = os.path.join(assets_dir, "pet.png")
    if os.path.isfile(path):
        return path
    bundled = os.path.join(BUNDLED_ASSETS_DIR, "pet.png")
    if os.path.isfile(bundled) and os.path.normcase(bundled) != os.path.normcase(path):
        try:
            shutil.copyfile(bundled, path)
            log.info("已复制出厂形象: %s -> %s", bundled, path)
            return path
        except OSError as e:
            log.warning("出厂形象复制失败: %s", e)
    log.error("缺少形象文件: %s（可运行 scripts/process_character.py 生成）", path)
    return path


def ensure_icon() -> str:
    """确保程序图标存在，返回 assets/pet.ico 路径。"""
    path = os.path.join(assets_dir, "pet.ico")
    if os.path.isfile(path):
        return path
    bundled = os.path.join(BUNDLED_ASSETS_DIR, "pet.ico")
    if os.path.isfile(bundled) and os.path.normcase(bundled) != os.path.normcase(path):
        try:
            shutil.copyfile(bundled, path)
            return path
        except OSError as e:
            log.warning("出厂图标复制失败: %s", e)
    return path
