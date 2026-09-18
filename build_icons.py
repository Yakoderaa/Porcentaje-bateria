from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PIL import Image

ROOT=Path(__file__).resolve().parent
ASSETS=ROOT/"assets"

def render(svg_path: Path, size: int, out_path: Path):
    renderer=QSvgRenderer(str(svg_path))
    image=QImage(size,size,QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter=QPainter(image)
    renderer.render(painter)
    painter.end()
    if not image.save(str(out_path),"PNG"):
        raise RuntimeError(f"No se pudo renderizar {out_path}")

def make_icon(stem: str):
    png=ASSETS/f"{stem}.png"
    render(ASSETS/f"{stem}.svg",1024,png)
    im=Image.open(png).convert("RGBA")
    sizes=[(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(96,96),(128,128),(256,256)]
    im.save(ASSETS/f"{stem}.ico",format="ICO",sizes=sizes)
    render(ASSETS/f"{stem}.svg",512,ASSETS/f"{stem}_512.png")

if __name__=="__main__":
    make_icon("app")
    make_icon("tray")
