"""从 assets/logo.jpeg 生成程序图标：assets/logo.png、assets/icon.icns、assets/icon.ico。

    .venv/bin/python assets/make_icons.py

生图 AI 出的是白底 JPEG，圆角外面是白的。这里把圆角方块抠出来、四角透明，
再按苹果的规范留白（图形占画布 80%），否则 Dock 里会是一个带白边的方块。
.icns 用系统自带的 iconutil 拼；.ico 由 Qt 直接写。
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath

HERE = Path(__file__).resolve().parent
SRC = HERE / "logo.jpeg"
CANVAS = 1024
FILL = 0.80          # 苹果模板：1024 画布里图形占 824
RADIUS = 0.225       # 圆角半径 / 边长，和原图的圆角一致


def crop_square(image):
    """找出非白区域的外接正方形。"""
    w, h = image.width(), image.height()
    xs, ys = [], []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            c = QColor(image.pixel(x, y))
            if c.red() < 235 or c.green() < 235 or c.blue() < 235:
                xs.append(x); ys.append(y)
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    side = max(right - left, bottom - top) + 2
    return image.copy(left, top, side, side)


def rounded(image, size):
    """缩放到 size，并用圆角矩形做透明遮罩。"""
    scaled = image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    out = QImage(size, size, QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * RADIUS, size * RADIUS)
    p.setClipPath(path)
    p.drawImage(0, 0, scaled)
    p.end()
    return out


def padded(tile, size):
    """把圆角方块居中放进带透明边距的画布。"""
    out = QImage(size, size, QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    inner = round(size * FILL)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(QRectF((size - inner) / 2, (size - inner) / 2, inner, inner), rounded(tile, inner))
    p.end()
    return out


def main():
    QGuiApplication(sys.argv)
    tile = crop_square(QImage(str(SRC)))

    # 窗口 / 任务栏用的 PNG：不留白，圆角透明
    rounded(tile, 512).save(str(HERE / "logo.png"))

    # Windows .ico：多尺寸，Qt 直接写
    ico = HERE / "icon.ico"
    rounded(tile, 256).save(str(ico))

    # macOS .icns：按 iconutil 要的文件名铺一个 .iconset
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "icon.iconset"
            iconset.mkdir()
            for pt in (16, 32, 128, 256, 512):
                padded(tile, pt).save(str(iconset / f"icon_{pt}x{pt}.png"))
                padded(tile, pt * 2).save(str(iconset / f"icon_{pt}x{pt}@2x.png"))
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")],
                           check=True)
    else:
        print("没有 iconutil（不是 macOS），跳过 .icns")
    print("done:", sorted(p.name for p in HERE.iterdir()))


if __name__ == "__main__":
    main()
