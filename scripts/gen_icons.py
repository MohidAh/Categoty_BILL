"""Generate BillBook PWA icons (192px, 512px, apple-touch-icon 180px) using PIL."""
from PIL import Image, ImageDraw, ImageFont
import os

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "static", "icons")
os.makedirs(OUT_DIR, exist_ok=True)

def make_icon(size, filename, bg_color="#2563EB", text="BB"):
    img = Image.new("RGB", (size, size), bg_color)
    draw = ImageDraw.Draw(img)
    # Try to load a font, fall back to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                   size=int(size * 0.45))
    except (OSError, IOError):
        font = ImageFont.load_default()
    # Center text
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), text, fill="white", font=font)
    filepath = os.path.join(OUT_DIR, filename)
    img.save(filepath, "PNG")
    print(f"✓ {filename} ({size}x{size})")

make_icon(192, "icon-192.png")
make_icon(512, "icon-512.png")
make_icon(180, "apple-touch-icon.png")
make_icon(32, "favicon-32.png")
make_icon(16, "favicon-16.png")

# Also copy to desktop/icons for Tauri
DESKTOP_ICONS = os.path.join(os.path.dirname(__file__), "..", "desktop", "icons")
os.makedirs(DESKTOP_ICONS, exist_ok=True)
for name in ["icon-192.png", "icon-512.png"]:
    src = os.path.join(OUT_DIR, name)
    dst = os.path.join(DESKTOP_ICONS, name)
    if os.path.exists(src):
        import shutil
        shutil.copy(src, dst)

print(f"\nAll icons saved to {OUT_DIR}/")
