"""Render the app logo to an .ico for the exe."""
import sys
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtCore import Qt
from skyfold.ui.icons import draw_logo

app = QGuiApplication(sys.argv)
from PIL import Image  # noqa: E402

frames = []
for size in (256, 64, 48, 32, 24, 16):
    img = QImage(size, size, QImage.Format_RGBA8888)
    img.fill(Qt.transparent)
    p = QPainter(img)
    draw_logo(p, size)
    p.end()
    frames.append(Image.frombuffer("RGBA", (size, size), bytes(img.constBits()), "raw", "RGBA", 0, 1))
frames[0].save("skyfold.ico", sizes=[(f.width, f.height) for f in frames], append_images=frames[1:])
print("icon ok")
