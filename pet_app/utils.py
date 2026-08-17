# -*- coding: utf-8 -*-
"""通用工具：程序根目录定位、日志、可写目录兜底。

路径约定（全部为相对路径，禁止硬编码绝对路径）：
- 程序根目录 ROOT：源码运行时为项目根目录，打包后为 exe 所在目录
- assets/      形象图片等资源
- config/      全局设置与日志
- personalities/  人格包目录
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if getattr(sys, "frozen", False):          # PyInstaller 打包后
    ROOT = os.path.dirname(sys.executable)

ASSETS_DIR = os.path.join(ROOT, "assets")
CONFIG_DIR = os.path.join(ROOT, "config")
PERSONALITY_DIR = os.path.join(ROOT, "personalities")


def _ensure_root_writable():
    """若程序目录不可写（如被解压到 Program Files），返回 False。"""
    probe = os.path.join(ROOT, ".write_test")
    try:
        with open(probe, "w", encoding="utf-8"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


ROOT_WRITABLE = _ensure_root_writable()


def writable_dir(primary: str, fallback_rel: str) -> str:
    """返回可写目录：优先程序目录内，不可写时回退到 %APPDATA%/DesktopPet 下。"""
    if ROOT_WRITABLE:
        return primary
    alt = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                       "DesktopPet", fallback_rel)
    os.makedirs(alt, exist_ok=True)
    return alt


#: 配置目录（可写兜底）
config_dir = writable_dir(CONFIG_DIR, "config")
#: 形象资源目录（可写兜底）
assets_dir = writable_dir(ASSETS_DIR, "assets")


def init_logger() -> logging.Logger:
    """日志写入 config/logs/app.log（轮转，单文件 512KB），排查问题先看这里。"""
    logger = logging.getLogger("deskpet")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    log_dir = os.path.join(config_dir, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(os.path.join(log_dir, "app.log"),
                                 maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(fh)
    except OSError:
        pass
    if not getattr(sys, "frozen", False):   # 开发环境同时输出控制台
        try:                                # Windows 控制台默认 GBK，改为 UTF-8 避免 emoji 报错
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(sh)
    return logger


log = init_logger()
