"""Generate the extension icons. Run from the repo root:

    .venv/bin/python scripts/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "extension" / "icons"
SIZE = 512
SCALE = 4


def rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    canvas = SIZE * SCALE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Navy rounded square with a subtle teal edge.
    margin = int(canvas * 0.03)
    rounded_rect(
        draw,
        (margin, margin, canvas - margin, canvas - margin),
        int(canvas * 0.23),
        (15, 23, 42, 255),
    )
    rounded_rect(
        draw,
        (margin, margin, canvas - margin, canvas - margin),
        int(canvas * 0.23),
        None,
    )
    edge = int(canvas * 0.018)
    draw.rounded_rectangle(
        (margin, margin, canvas - margin, canvas - margin),
        radius=int(canvas * 0.23),
        outline=(20, 184, 166, 255),
        width=edge,
    )

    # Shield.
    left = int(canvas * 0.29)
    right = int(canvas * 0.71)
    top = int(canvas * 0.21)
    bottom = int(canvas * 0.80)
    shield = [
        (canvas / 2, top),
        (right, top + int(canvas * 0.07)),
        (right - int(canvas * 0.03), top + int(canvas * 0.30)),
        (canvas / 2, bottom),
        (left + int(canvas * 0.03), top + int(canvas * 0.30)),
        (left, top + int(canvas * 0.07)),
    ]
    draw.polygon(shield, fill=(13, 148, 136, 255))
    inner = [
        (canvas / 2, top + int(canvas * 0.055)),
        (right - int(canvas * 0.045), top + int(canvas * 0.105)),
        (right - int(canvas * 0.07), top + int(canvas * 0.30)),
        (canvas / 2, bottom - int(canvas * 0.06)),
        (left + int(canvas * 0.07), top + int(canvas * 0.30)),
        (left + int(canvas * 0.045), top + int(canvas * 0.105)),
    ]
    draw.polygon(inner, fill=(15, 118, 110, 255))

    # Check mark.
    width = int(canvas * 0.055)
    draw.line(
        [
            (int(canvas * 0.38), int(canvas * 0.51)),
            (int(canvas * 0.47), int(canvas * 0.61)),
            (int(canvas * 0.64), int(canvas * 0.40)),
        ],
        fill=(240, 253, 250, 255),
        width=width,
        joint="curve",
    )

    for size in (16, 32, 48, 128):
        resized = image.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(OUT_DIR / f"icon{size}.png")
        print(f"wrote {OUT_DIR / f'icon{size}.png'}")


if __name__ == "__main__":
    main()
