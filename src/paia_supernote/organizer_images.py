from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image


@dataclass(slots=True)
class CachedPageImage:
    path: Path
    cache_hit: bool
    width: int
    height: int
    media_type: str = "image/png"


class PageImageCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(cache_dir)

    def cache_path(
        self,
        *,
        notebook_name: str,
        revision: str,
        page_id: str,
        scale: float,
    ) -> Path:
        key = "|".join(
            [
                str(notebook_name),
                str(revision),
                str(page_id),
                f"{float(scale):.4f}",
            ]
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / _safe_component(notebook_name) / f"{digest}.png"

    def get_or_render(
        self,
        *,
        notebook_name: str,
        revision: str,
        page_id: str,
        scale: float,
        renderer: Callable[[], Image.Image],
    ) -> CachedPageImage:
        path = self.cache_path(
            notebook_name=notebook_name,
            revision=revision,
            page_id=page_id,
            scale=scale,
        )
        if path.exists():
            with Image.open(path) as image:
                return CachedPageImage(
                    path=path,
                    cache_hit=True,
                    width=image.width,
                    height=image.height,
                )

        image = renderer()
        scaled = _scale_image(image, scale)
        path.parent.mkdir(parents=True, exist_ok=True)
        scaled.save(path, format="PNG")
        return CachedPageImage(
            path=path,
            cache_hit=False,
            width=scaled.width,
            height=scaled.height,
        )


def _scale_image(image: Image.Image, scale: float) -> Image.Image:
    scale = float(scale)
    if scale <= 0:
        raise ValueError("scale must be greater than zero")
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    if (width, height) == image.size:
        return image.copy()
    return image.resize((width, height))


def _safe_component(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)
    return safe.strip("-") or "notebook"
