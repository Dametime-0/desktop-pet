# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：单文件夹绿色版。
# 打包命令：pyinstaller desktop_pet.spec --noconfirm
# 也可直接双击 build.bat 一键完成。

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # 运行时需要的文件全部随包分发（相对路径）
    datas=[
        ('personalities', 'personalities'),
        ('config', 'config'),
        ('assets', 'assets'),
        ('README.md', '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DesktopPet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # 不使用 UPX，避免杀软误报
    console=False,                # 无控制台窗口
    icon='assets/pet.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='桌宠绿色版',
)
