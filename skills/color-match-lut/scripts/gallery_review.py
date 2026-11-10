"""Local scrolling review helpers. Payloads contain file URLs, never image bytes."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote


class Assets:
    def __init__(self, output, color_engine):
        self.output = Path(output).resolve()
        self.engine = color_engine
        self.directory = self.output / "gallery_assets"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cache = {}

    def link(self, path):
        return quote(Path(os.path.relpath(Path(path).resolve(), self.output)).as_posix(), safe="/:")

    def image(self, path, label):
        path = Path(path).resolve()
        key = str(path)
        if key not in self.cache:
            thumb = self.directory / (hashlib.sha256(key.encode()).hexdigest()[:20] + ".jpg")
            if not thumb.exists():
                image, _ = self.engine.read_srgb(path, long_edge=1600)
                self.engine.preview(image, thumb)
            self.cache[key] = {"preview": self.link(thumb), "url": self.link(path)}
        return {**self.cache[key], "label": label}

    def result(self, info, index=0, label=""):
        rendered = info["renders"][index]
        return {"label": label or info.get("label", "追色结果"),
                "preview": self.link(rendered["preview"]), "url": self.link(rendered["png"]),
                "cube": self.link(info["cube"])}


def write_gallery(data, output, filename="review.html"):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).resolve().parents[1] / "assets" / "gallery.html"
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    path = output / filename
    path.write_text(template.read_text(encoding="utf-8").replace("__DATA__", payload), encoding="utf-8")
    (output / "gallery_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())
