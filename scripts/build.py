# -*- coding: utf-8 -*-
"""一键打包脚本：确保形象/图标 → PyInstaller → 压缩绿色版 zip。

用法：python scripts/build.py
产物：dist/桌宠绿色版/（单文件夹绿色版）与 dist/桌宠绿色版_v{版本}.zip
"""
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "2.3.1"


def main():
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)

    # 1) 确保形象与图标存在（打包时随包分发 assets/pet.png 当前形象）
    assets = os.path.join(ROOT, "assets")
    os.makedirs(assets, exist_ok=True)
    img_path = os.path.join(assets, "pet.png")
    if os.path.isfile(img_path):
        print("[1/3] 使用现有形象 assets/pet.png")
    else:
        print("错误：缺少 assets/pet.png，请先运行 "
              "python scripts/process_character.py <图片路径>")
        sys.exit(1)
    ico_path = os.path.join(assets, "pet.ico")
    if not os.path.isfile(ico_path):
        from PIL import Image
        Image.open(img_path).convert("RGBA").resize((256, 256)).save(
            ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                             (64, 64), (128, 128), (256, 256)])
        print("      已生成图标 assets/pet.ico")

    # 2) PyInstaller 打包（先清理开发日志，避免随包分发）
    print("[2/3] PyInstaller 打包中（首次较慢，请耐心等待）……")
    logs_dir = os.path.join(ROOT, "config", "logs")
    if os.path.isdir(logs_dir):
        for fn in os.listdir(logs_dir):
            try:
                os.remove(os.path.join(logs_dir, fn))
            except OSError:
                pass
    subprocess.check_call([sys.executable, "-m", "PyInstaller",
                           "desktop_pet.spec", "--noconfirm"])

    # 3) 压缩绿色版
    print("[3/3] 压缩绿色版 zip ……")
    folder = os.path.join(ROOT, "dist", "桌宠绿色版")
    if not os.path.isdir(folder):
        print("错误：未找到打包产物 " + folder)
        sys.exit(1)
    zip_name = os.path.join(ROOT, "dist", f"桌宠绿色版_v{VERSION}.zip")
    if os.path.isfile(zip_name):
        os.remove(zip_name)
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirnames, filenames in os.walk(folder):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.join("桌宠绿色版", os.path.relpath(full, folder))
                zf.write(full, rel)
    print(f"\n打包完成：{zip_name}")
    print("将压缩包发给朋友，解压后双击 DesktopPet.exe 即可运行（无需安装 Python 或任何依赖）。")


if __name__ == "__main__":
    main()
