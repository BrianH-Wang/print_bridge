import sys

from PyQt6.QtWidgets import QApplication

from ui.main_window import WebSocketServerGUI

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setOrganizationName("PrintBridge")
    app.setApplicationName("Print Bridge")

    window = WebSocketServerGUI()
    window.show()

    sys.exit(app.exec())
