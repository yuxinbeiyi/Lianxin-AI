"""Remove the connected dark background from prepared sprite frames."""
from collections import deque
from pathlib import Path
import math
import argparse

from PIL import Image

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def remove_background(image):
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    visited = set()
    queue = deque()
    seeds = [(x, y) for x in range(width) for y in (0, height - 1)]
    seeds += [(x, y) for y in range(height) for x in (0, width - 1)]
    background = pixels[seeds[0][0], seeds[0][1]][:3]
    for point in seeds:
        if point not in visited:
            visited.add(point)
            queue.append(point)

    while queue:
        x, y = queue.popleft()
        rgb = pixels[x, y][:3]
        # The prepared boards use a dark, low-saturation backdrop. A strict
        # threshold keeps the dark suit and outline outside this flood region.
        if _distance(rgb, background) > 24 or max(rgb) > 75:
            continue
        pixels[x, y] = (*rgb, 0)
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in visited:
                visited.add((nx, ny))
                queue.append((nx, ny))
    return rgba


def main():
    parser = argparse.ArgumentParser(description="Remove connected background from pet frames")
    parser.add_argument(
        "folders", nargs="*", default=None,
        help="asset folders to process, for example walk_left walk_right; default: all folders",
    )
    args = parser.parse_args()
    folders = args.folders or [path.name for path in ASSETS.iterdir() if path.is_dir()]
    files = sorted(
        path for folder in folders
        for path in (ASSETS / folder).glob("frame_*.png")
    )
    for path in files:
        with Image.open(path) as image:
            remove_background(image).save(path)
        print(f"processed {path.relative_to(ASSETS)}")
    print(f"processed {len(files)} frames")


if __name__ == "__main__":
    main()
