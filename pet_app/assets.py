# -*- coding: utf-8 -*-
"""形象与图标资源管理。

优先级：用户目录（assets/pet.png 可替换/导入）→ 随包资源（_internal/assets）→ 报错。
形象图片由 scripts/process_character.py 预处理生成（去水印 + 抠图），随包分发。

版本升级同步：assets/pet.custom 标记文件表示"用户自定义形象"。
- 用户通过菜单/拖拽导入形象时写入该标记，之后的升级不再覆盖；
- 无标记的 pet.png 视为随包旧副本，启动时用最新随包形象替换——
  避免用户解压新版本后仍看到旧版形象（白块/缺腿等历史问题）。"""
import os
import shutil

from .utils import BUNDLED_ASSETS_DIR, assets_dir, log


def mark_custom_image():
    """标记当前形象为用户自定义（导入新形象后调用）。"""
    try:
        with open(os.path.join(assets_dir, "pet.custom"), "w", encoding="utf-8"):
            pass
    except OSError as e:
        log.warning("自定义形象标记失败: %s", e)


def ensure_pet_image() -> str:
    """确保形象文件存在且为最新，返回路径。"""
    path = os.path.join(assets_dir, "pet.png")
    marker = os.path.join(assets_dir, "pet.custom")
    bundled = os.path.join(BUNDLED_ASSETS_DIR, "pet.png")
    bundled_ok = os.path.isfile(bundled) and os.path.normcase(bundled) != os.path.normcase(path)
    # 版本升级同步：无自定义标记的旧副本 → 用随包新版替换
    if os.path.isfile(path) and not os.path.isfile(marker) and bundled_ok:
        try:
            shutil.copyfile(bundled, path)
            log.info("已同步出厂形象: %s", bundled)
        except OSError as e:
            log.warning("出厂形象同步失败: %s", e)
    if os.path.isfile(path):
        return path
    if bundled_ok:
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
