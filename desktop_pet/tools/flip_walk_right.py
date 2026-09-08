"""Horizontally flip the desktop pet's walk_right animation frames."""

from pathlib import Path
import shutil

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "assets" / "walk_right"
BACKUP_DIR = ROOT / "assets" / "walk_right_backup_before_flip"


def main() -> None:
    frames = sorted(SOURCE_DIR.glob("*.png"))
    if not frames:
        raise SystemExit(f"No PNG frames found: {SOURCE_DIR}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        backup = BACKUP_DIR / frame.name
        if not backup.exists():
            shutil.copy2(frame, backup)
        with Image.open(frame) as image:
            flipped = ImageOps.mirror(image)
            flipped.save(frame, format="PNG")
        print(f"Flipped: {frame.name}")

    print(f"Completed {len(frames)} frame(s). Backup: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
