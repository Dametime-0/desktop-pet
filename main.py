# -*- coding: utf-8 -*-
"""桌宠主入口。

用法：
    python main.py            正常启动
    python main.py --selftest 自检模式（截图 + 逻辑断言后自动退出）

单实例：使用 QLocalServer 互斥。若已有实例在运行，第二次启动会通知
现有实例"把桌宠显示出来"（唤醒隐藏/跑丢的窗口），然后自己退出——
而不是弹一个"已经在运行"的提示框让用户困惑。
"""
import sys

from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

INSTANCE_NAME = "DesktopPet_SingleInstance_v1"


def _ask_existing_to_show() -> bool:
    """尝试连接已有实例并请求其显示窗口。返回是否已连接。"""
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE_NAME)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return True
    return False


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("DesktopPet")
    app.setQuitOnLastWindowClosed(False)     # 气泡/面板关闭不代表退出程序

    from pet_app.assets import ensure_icon
    app.setWindowIcon(QIcon(ensure_icon()))

    if "--selftest" in sys.argv:
        from pet_app.selftest import run_selftest
        return run_selftest(app)

    # 单实例：已有实例 → 让它把桌宠显示出来，本进程退出
    if _ask_existing_to_show():
        return 0

    server = QLocalServer()
    # 名称被占用（竞态）也视为已有实例
    if not server.listen(INSTANCE_NAME):
        _ask_existing_to_show()
        return 0

    from pet_app.controller import PetController
    controller = PetController(app)          # noqa: F841 保持引用防止被回收

    def _on_new_connection():
        """收到"show"请求：把桌宠窗口带到用户面前。"""
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: (
                controller.show_up() if bytes(conn.readAll()).strip() == b"show" else None))
            conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(_on_new_connection)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
