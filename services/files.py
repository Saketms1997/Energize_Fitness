"""Copy owner-chosen images into the app data folder so they survive the original being moved."""
from __future__ import annotations

import shutil
from pathlib import Path

from config.settings import get_settings
from services.errors import ValidationError

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


def store_image(source: Path | str, stem: str, label: str = "Image") -> str:
    """Copy to ``<data>/images/<stem><ext>``; returns the path relative to the data folder."""
    src = Path(source)
    if src.suffix.lower() not in IMAGE_EXTENSIONS or not src.is_file():
        raise ValidationError(f"{label} must be an image file (png, jpg, bmp, gif, webp).", label)
    folder = get_settings().data_dir / "images"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{stem}.*"):
        old.unlink(missing_ok=True)
    target = folder / f"{stem}{src.suffix.lower()}"
    shutil.copyfile(src, target)
    return f"images/{target.name}"
