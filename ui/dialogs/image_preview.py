"""双击历史表格里的图片列时弹出的大图预览。"""

from PyQt6.QtCore import QMargins, QSize, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout


class ImagePreviewDialog(QDialog):
    """双击图片列时弹出的大图预览。"""

    def __init__(self, path, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(360, 280)

        pixmap = QPixmap(str(path))
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if pixmap.isNull():
            label.setText("无法读取图片文件")
        else:
            screen = self.screen().availableGeometry()
            limit = pixmap.size().boundedTo(
                QSize(int(screen.width() * 0.8), int(screen.height() * 0.8))
            )
            label.setPixmap(pixmap.scaled(
                limit,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            self.resize(label.pixmap().size().grownBy(QMargins(24, 24, 24, 24)))

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(label)

        box = QVBoxLayout(self)
        box.setContentsMargins(8, 8, 8, 8)
        box.addWidget(area)
