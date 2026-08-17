# -*- coding: utf-8 -*-
"""桌宠主入口。

用法：
    python main.py            正常启动
    python main.py --selftest 自检模式（截图 + 逻辑断言后自动退出）
"""
import sys

from PySide6.QtCore import QSharedMemory
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DesktopPet")
    app.setQuitOnLastWindowClosed(False)     # 气泡/面板关闭不代表退出程序

    from pet_app.assets import ensure_icon
    app.setWindowIcon(QIcon(ensure_icon()))

    # 单实例保护：桌宠同时只运行一个
    shared = QSharedMemory("DesktopPet_SingleInstance_v1")
    if not shared.create(1):
        QMessageBox.information(None, "桌宠", "桌宠已经在运行啦～")
        return 0

    if "--selftest" in sys.argv:
        from pet_app.selftest import run_selftest
        return run_selftest(app)

    from pet_app.controller import PetController
    controller = PetController(app)          # noqa: F841 保持引用防止被回收
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
