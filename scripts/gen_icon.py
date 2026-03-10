"""Generate application icon files: icon.ico and icon.png"""
import os
from PIL import Image, ImageDraw, ImageFont

SIZES = [16, 32, 48, 64, 128, 256]
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "namemasker", "assets")


def draw_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = size // 8
    r = size // 6
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=r,
        fill=(59, 130, 246),
        outline=(30, 64, 175),
        width=max(1, size // 32),
    )

    font_size = size * 4 // 10
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    text = "NM"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), text, fill=(255, 255, 255), font=font)

    bar_h = max(2, size // 12)
    bar_y = size * 7 // 10
    draw.rectangle(
        [pad + r // 2, bar_y, size - pad - r // 2, bar_y + bar_h],
        fill=(248, 113, 113),
    )

    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    images = [draw_icon(s) for s in SIZES]

    png_path = os.path.join(OUT_DIR, "icon.png")
    images[-1].save(png_path, format="PNG")
    print(f"Saved {png_path}")

    ico_path = os.path.join(OUT_DIR, "icon.ico")
    images[0].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[1:],
    )
    print(f"Saved {ico_path}")


if __name__ == "__main__":
    main()
