#!/usr/bin/env python3
"""Build native .excalidraw files from label-shorthand JSON sources.

The MCP renderer accepts a convenience `label` field on shapes. Real Excalidraw
does not: a labelled shape is a container plus a separate text element that
points back at it via `containerId`, with the container listing the text in
`boundElements`. Exporting the shorthand directly produces shapes with no text.

Usage:
    uv run --no-project python scripts/excalidraw_build.py
    python scripts/excalidraw_build.py

Reads  docs/diagrams/src/*.json
Writes docs/diagrams/*.excalidraw
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "diagrams" / "src"
OUT = ROOT / "docs" / "diagrams"

# Excalifont advance width is roughly 0.55em; 1.25 line height matches the editor.
CHAR_W = 0.55
LINE_H = 1.25


def seed() -> int:
    return random.randint(1, 2**31 - 1)


def text_size(text: str, font_size: int) -> tuple[float, float]:
    lines = text.split("\n")
    width = max((len(line) for line in lines), default=0) * font_size * CHAR_W
    height = len(lines) * font_size * LINE_H
    return width, height


def base(el: dict) -> dict:
    """Fill in every field Excalidraw expects, without clobbering what is set."""
    el.setdefault("angle", 0)
    el.setdefault("strokeColor", "#1e1e1e")
    el.setdefault("backgroundColor", "transparent")
    el.setdefault("fillStyle", "solid")
    el.setdefault("strokeWidth", 2)
    el.setdefault("strokeStyle", "solid")
    el.setdefault("roughness", 1)
    el.setdefault("opacity", 100)
    el.setdefault("groupIds", [])
    el.setdefault("frameId", None)
    el.setdefault("roundness", None)
    el.setdefault("seed", seed())
    el.setdefault("version", 1)
    el.setdefault("versionNonce", seed())
    el.setdefault("isDeleted", False)
    el.setdefault("boundElements", None)
    el.setdefault("updated", 1)
    el.setdefault("link", None)
    el.setdefault("locked", False)
    return el


def build(elements: list[dict]) -> list[dict]:
    out: list[dict] = []

    for raw in elements:
        el = dict(raw)
        kind = el.get("type")

        # cameraUpdate is a renderer directive, not a real element.
        if kind in {"cameraUpdate", "delete", "restoreCheckpoint"}:
            continue

        label = el.pop("label", None)

        if kind == "arrow":
            el.setdefault("points", [[0, 0], [el.get("width", 0), el.get("height", 0)]])
            el.setdefault("lastCommittedPoint", None)
            el.setdefault("startBinding", None)
            el.setdefault("endBinding", None)
            el.setdefault("startArrowhead", None)
            el.setdefault("endArrowhead", "arrow")
            el.setdefault("elbowed", False)

        if kind == "text":
            el.setdefault("fontSize", 16)
            el.setdefault("fontFamily", 1)
            el.setdefault("textAlign", "left")
            el.setdefault("verticalAlign", "top")
            el.setdefault("containerId", None)
            el.setdefault("originalText", el.get("text", ""))
            el.setdefault("lineHeight", LINE_H)
            el.setdefault("autoResize", True)
            w, h = text_size(el.get("text", ""), el["fontSize"])
            el.setdefault("width", w)
            el.setdefault("height", h)

        out.append(base(el))

        if not label:
            continue

        # A labelled shape becomes container + bound text pointing at each other.
        font_size = label.get("fontSize", 16)
        text = label.get("text", "")
        tw, th = text_size(text, font_size)
        tid = f"{el['id']}_lbl"

        cx = el.get("x", 0) + el.get("width", 0) / 2
        cy = el.get("y", 0) + el.get("height", 0) / 2

        out[-1]["boundElements"] = [{"type": "text", "id": tid}]
        out.append(
            base(
                {
                    "type": "text",
                    "id": tid,
                    "x": cx - tw / 2,
                    "y": cy - th / 2,
                    "width": tw,
                    "height": th,
                    "text": text,
                    "originalText": text,
                    "fontSize": font_size,
                    "fontFamily": 1,
                    "textAlign": "center",
                    "verticalAlign": "middle",
                    "containerId": el["id"],
                    "lineHeight": LINE_H,
                    "autoResize": True,
                    "strokeColor": label.get("strokeColor", "#1e1e1e"),
                }
            )
        )

    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = sorted(SRC.glob("*.json"))
    if not sources:
        raise SystemExit(f"no sources found in {SRC}")

    for path in sources:
        data = json.loads(path.read_text(encoding="utf-8"))
        elements = build(data["elements"])
        doc = {
            "type": "excalidraw",
            "version": 2,
            "source": "humani-t",
            "elements": elements,
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
            "files": {},
        }
        dest = OUT / f"{path.stem}.excalidraw"
        dest.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        labelled = sum(1 for e in elements if e.get("containerId"))
        print(f"{dest.name}: {len(elements)} elements, {labelled} bound labels")


if __name__ == "__main__":
    main()
