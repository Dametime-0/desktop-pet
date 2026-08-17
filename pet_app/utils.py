# -*- coding: utf-8 -*-
"""通用工具：程序根目录定位、日志、可写目录兜底。

路径约定（全部为相对路径，禁止硬编码绝对路径）：
- ROOT        程序根目录：源码运行时为项目根目录，打包后为 exe 所在目录
- DATA_ROOT   随包资源目录：源码运行时同 ROOT；PyInstaller 打包后为 _internal
              （Personality/形象/配置的出厂副本都在这里，只读）
- 用户可写目录：优先 ROOT 下的 assets/config/personalities（用户修改优先加载），
  程序目录不可写时回退到 %APPDATA%/DesktopPet
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = ROOT
if getattr(sys, "frozen", False):          # PyInstaller 打包后
    ROOT = os.path.dirname(sys.executable)
    DATA_ROOT = getattr(sys, "_MEIPASS", ROOT)

#: 用户可写目录（绿色版中与 exe 同级，便于直接替换人格/形象）
ASSETS_DIR = os.path.join(ROOT, "assets")
CONFIG_DIR = os.path.join(ROOT, "config")
PERSONALITY_DIR = os.path.join(ROOT, "personalities")
#: 随包只读资源（出厂默认值）
BUNDLED_ASSETS_DIR = os.path.join(DATA_ROOT, "assets")
BUNDLED_CONFIG_DIR = os.path.join(DATA_ROOT, "config")
BUNDLED_PERSONALITY_DIR = os.path.join(DATA_ROOT, "personalities")


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
        os.makedirs(primary, exist_ok=True)
        return primary
    alt = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                       "DesktopPet", fallback_rel)
    os.makedirs(alt, exist_ok=True)
    return alt


#: 配置目录（可写兜底）
config_dir = writable_dir(CONFIG_DIR, "config")
#: 形象资源目录（可写兜底，新导入的形象保存在这里）
assets_dir = writable_dir(ASSETS_DIR, "assets")
#: 人格包目录（可写兜底，导入的人格包保存在这里）
personality_dir = writable_dir(PERSONALITY_DIR, "personalities")


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
    # Windows 控制台默认 GBK，改为 UTF-8 避免 emoji 输出报错（打包版同样生效）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    if not getattr(sys, "frozen", False):   # 开发环境同时输出控制台
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(sh)
    return logger


log = init_logger()
