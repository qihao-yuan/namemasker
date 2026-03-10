"""
Mosaic / fill rectangular regions on an image.
"""
import os
import time
import tempfile
from PIL import Image, ImageDraw, ImageFilter

MAX_IMAGE_PIXELS = 100_000_000  # 100 MP


def safe_open_image(path: str, mode: str = "RGB") -> Image.Image:
    """Open image with memory protection: downscale if exceeds MAX_IMAGE_PIXELS."""
    im = Image.open(path)
    w, h = im.size
    pixels = w * h
    if pixels > MAX_IMAGE_PIXELS:
        scale = (MAX_IMAGE_PIXELS / pixels) ** 0.5
        nw, nh = int(w * scale), int(h * scale)
        im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    return im.convert(mode)


def pixelate_region(im: Image.Image, x1: int, y1: int, x2: int, y2: int, block_size: int = 10):
    """Pixelate rectangle (x1,y1)-(x2,y2) on *im* in-place."""
    if block_size < 2:
        block_size = 2
    box = (max(0, x1), max(0, y1), min(im.width, x2), min(im.height, y2))
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    crop = im.crop(box)
    bw = max(1, crop.width // block_size)
    bh = max(1, crop.height // block_size)
    crop = crop.resize((bw, bh), Image.NEAREST)
    crop = crop.resize((box[2] - box[0], box[3] - box[1]), Image.NEAREST)
    im.paste(crop, box)


def fill_region(im: Image.Image, x1: int, y1: int, x2: int, y2: int, color=(255, 255, 255)):
    """Fill rectangle with solid color on *im* in-place."""
    box = (max(0, x1), max(0, y1), min(im.width, x2), min(im.height, y2))
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    draw = ImageDraw.Draw(im)
    draw.rectangle(box, fill=color)


def blur_region(im: Image.Image, x1: int, y1: int, x2: int, y2: int, radius: int = 20):
    """Heavy Gaussian blur on a rectangle, in-place."""
    box = (max(0, x1), max(0, y1), min(im.width, x2), min(im.height, y2))
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    crop = im.crop(box)
    crop = crop.filter(ImageFilter.GaussianBlur(radius=radius))
    im.paste(crop, box)


def expand_box(x1, y1, x2, y2, pad_pct, img_w, img_h):
    """Expand box by pad_pct (0~1) of its own dimensions, clamped to image bounds."""
    w = x2 - x1
    h = y2 - y1
    px = int(w * pad_pct)
    py = int(h * pad_pct)
    return (
        max(0, x1 - px),
        max(0, y1 - py),
        min(img_w, x2 + px),
        min(img_h, y2 + py),
    )


def _safe_save(im: Image.Image, output_path: str, retries: int = 3):
    """Write to temp file then atomic-rename, with retry on Windows lock."""
    out_dir = os.path.dirname(output_path) or "."
    ext = os.path.splitext(output_path)[1].lower()
    fmt_map = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP", ".webp": "WEBP"}
    fmt = fmt_map.get(ext, "PNG")
    save_kwargs = {"quality": 95} if fmt == "JPEG" else {}
    for attempt in range(retries):
        try:
            fd, tmp = tempfile.mkstemp(suffix=ext or ".png", dir=out_dir)
            os.close(fd)
            im.save(tmp, format=fmt, **save_kwargs)
            os.replace(tmp, output_path)
            return
        except PermissionError:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if attempt < retries - 1:
                time.sleep(0.5)
            else:
                raise


def checkerboard_region(im: Image.Image, x1: int, y1: int, x2: int, y2: int,
                        cell: int = 8, c1=(200, 200, 200), c2=(255, 255, 255),
                        alpha: int = 160):
    """Semi-transparent gray-white checkerboard over rectangle, in-place."""
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(im.width, x2), min(im.height, y2)
    if x2 <= x1 or y2 <= y1:
        return
    w, h = x2 - x1, y2 - y1
    checker = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(checker)
    for row in range(0, h, cell):
        for col in range(0, w, cell):
            c = c1 if (row // cell + col // cell) % 2 == 0 else c2
            draw.rectangle([col, row, col + cell - 1, row + cell - 1],
                           fill=(*c, alpha))
    base = im.crop((x1, y1, x2, y2))
    base = base.filter(ImageFilter.GaussianBlur(radius=20))
    base = base.convert("RGBA")
    blended = Image.alpha_composite(base, checker)
    im.paste(blended.convert("RGB"), (x1, y1))


def apply_mosaic(image_path, output_path, regions, block_size=10, mode="fill",
                 fill_color=(255, 255, 255), blur_radius=20, pad_pct=0.0):
    """
    Apply masking to *regions* (list of (x1, y1, x2, y2)) and save.
    mode: "pixelate" | "fill" | "blur" | "checker"
    pad_pct: expand each box by this fraction of its own size (e.g. 0.3 = 30%)
    """
    im = safe_open_image(image_path)
    for (x1, y1, x2, y2) in regions:
        if pad_pct > 0:
            x1, y1, x2, y2 = expand_box(x1, y1, x2, y2, pad_pct, im.width, im.height)
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(im.width, int(x2)), min(im.height, int(y2))
        if x2 <= x1 or y2 <= y1:
            continue
        if mode == "fill":
            fill_region(im, x1, y1, x2, y2, color=fill_color)
        elif mode == "blur":
            blur_region(im, x1, y1, x2, y2, radius=blur_radius)
        elif mode == "checker":
            checkerboard_region(im, x1, y1, x2, y2, cell=block_size)
        else:
            pixelate_region(im, x1, y1, x2, y2, block_size=block_size)
    _safe_save(im, output_path)
    return len(regions)
