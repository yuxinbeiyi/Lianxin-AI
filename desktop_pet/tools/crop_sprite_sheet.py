"""Crop transparent frames from the clean four-row character action board."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "source" / "character_actions.png"
OUT = ROOT / "assets"

REGIONS = {
    "idle": [(335, 0, 535, 275), (605, 0, 805, 275),
             (875, 0, 1080, 275), (1145, 0, 1350, 275)],
    "walk_right": [(80, 260, 280, 535), (335, 260, 545, 535),
                   (590, 260, 800, 535), (845, 260, 1055, 535),
                   (1095, 260, 1305, 535), (1330, 260, 1515, 535)],
    "walk_left": [(80, 500, 320, 780), (335, 500, 570, 780),
                  (590, 500, 810, 780), (845, 500, 1055, 780),
                  (1095, 500, 1315, 780), (1330, 500, 1515, 780)],
    "thinking": [(335, 745, 535, 1024), (605, 745, 805, 1024),
                 (875, 745, 1080, 1024), (1145, 745, 1350, 1024)],
}


def _crop_and_fit(image: Image.Image) -> Image.Image:
    # The source is already transparent. Keep the explicit crop box intact so
    # every frame uses the same source coordinate system and scale.
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", (48, 64), (0, 0, 0, 0))
    rgba.thumbnail((48, 64), Image.Resampling.NEAREST)
    canvas.alpha_composite(rgba, ((48 - rgba.width) // 2, 64 - rgba.height))
    return canvas


def main():
    source = Image.open(SOURCE)
    for name, boxes in REGIONS.items():
        target = OUT / name
        target.mkdir(parents=True, exist_ok=True)
        for stale in target.glob("frame_*.png"):
            stale.unlink()
        for index, box in enumerate(boxes):
            _crop_and_fit(source.crop(box)).save(target / f"frame_{index:02d}.png")
        print(f"generated {name}: {len(boxes)} frames")


if __name__ == "__main__":
    main()
