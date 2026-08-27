"""Image ingestion, preprocessing, and PDF rendering."""
import time
from pathlib import Path
import pymupdf as fitz  # PyMuPDF (modern import)
from PIL import Image, ImageOps, ImageEnhance, ImageFilter

DPI = 200
MIN_LONG_EDGE = 1200


def save_upload(data: bytes, filename: str, dest: Path) -> Path:
    """Save uploaded bytes with a safe, timestamped filename."""
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "upload"
    # Prefix with timestamp + short unique suffix to avoid collisions
    ts = int(time.time() * 1000)
    p = dest / f"{ts}_{safe}"
    p.write_bytes(data)
    return p


def _preprocess(img: Image.Image) -> Image.Image:
    """Normalize EXIF orientation, upscale small images, sharpen."""
    img = ImageOps.exif_transpose(img)
    if max(img.size) < MIN_LONG_EDGE:
        f = MIN_LONG_EDGE / max(img.size)
        img = img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Mild sharpening to help AI read small digits
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    # Slight contrast boost
    img = ImageEnhance.Contrast(img).enhance(1.15)
    return img


def render_pages(path: Path, pages_dir: Path, stem_prefix: str = None) -> list[Path]:
    """Render PDF pages or normalize an image into PNG pages.

    stem_prefix: optional prefix (e.g. timestamped upload stem) to guarantee uniqueness.
    """
    out = []
    stem = stem_prefix if stem_prefix is not None else path.stem

    if path.suffix.lower() == ".pdf":
        doc = fitz.open(path)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72))
            p = pages_dir / f"{stem}_p{i + 1}.png"
            pix.save(p)
            out.append(p)
        doc.close()
    else:
        img = _preprocess(Image.open(path))
        p = pages_dir / f"{stem}.png"
        img.save(p, "PNG")
        out.append(p)
    return out
