#!/usr/bin/env python3
"""Normalize the three supplied character sheets. Requires Pillow >= 9.

Sources remain untouched. Run from any directory after placing the two supplied
PNGs in assets/estate/characters/sources/{xiaopang,rose_mage}.png.
This is a nearest-neighbour import, not newly drawn 96-frame character art.
"""
from pathlib import Path
import json
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets/estate/characters"
FRAME = (36, 48)
ANCHOR = (18, 46)
ANIMATIONS = ["idle_down", "idle_up", "idle_right", "walk_down", "walk_up", "walk_right"]


def cells(width, bands, row, columns):
    return [(round(i * width), bands[row], round((i + 1) * width), bands[row + 1])
            for i in columns]


CHARACTERS = {
    "berry": {
        "name": "经典莓果",
        "source": ROOT / "assets/estate/xiaopang/player/character-sheet.png",
        "boxes": [
            cells(111, [0, 133], 0, [0, 1]),
            cells(111, [0, 133], 0, [2, 3]),
            cells(111, [0, 133], 0, [4, 5]),
            cells(111, [133, 264, 395, 531], 0, [0, 2, 4, 6]),
            cells(111, [133, 264, 395, 531], 1, [1, 3, 5, 7]),
            cells(111, [133, 264, 395, 531], 2, [0, 2, 4, 6]),
        ],
    },
    "xiaopang": {
        "name": "小胖庄园主",
        "source": OUTPUT / "sources/xiaopang.png",
        "boxes": [
            cells(160, [0, 226, 432, 633], 0, [0, 1]),
            cells(160, [0, 226, 432, 633], 0, [2, 3]),
            cells(160, [0, 226, 432, 633], 0, [4, 5]),
            cells(160, [0, 226, 432, 633], 1, [0, 1, 2, 3]),
            cells(160, [0, 226, 432, 633], 1, [4, 5, 6, 7]),
            cells(160, [0, 226, 432, 633], 2, [0, 1, 2, 3]),
        ],
    },
    "rose_mage": {
        "name": "粉樱礼帽",
        "source": OUTPUT / "sources/rose_mage.png",
        "boxes": [
            [(8, 0, 253, 307), (258, 0, 478, 307)],
            [(489, 0, 714, 307), (718, 0, 937, 307)],
            [(941, 0, 1145, 307), (1163, 0, 1355, 307)],
            [(0, 307, 241, 594), (241, 307, 467, 594), (467, 307, 689, 594), (689, 307, 908, 594)],
            [(908, 307, 1127, 594), (1127, 307, 1351, 594), (1351, 307, 1577, 594), (1577, 307, 1783, 594)],
            [(0, 594, 234, 882), (234, 594, 450, 882), (450, 594, 675, 882), (675, 594, 900, 882)],
        ],
    },
}


def clean(image):
    """Discard low-alpha generation halos and bright red cutting marks."""
    result = image.convert("RGBA")
    result.putdata([
        (r, g, b, 255) if a >= 200 and not (r > 180 and g < 85 and b < 100)
        else (0, 0, 0, 0)
        for r, g, b, a in result.getdata()
    ])
    box = result.getbbox()
    if not box:
        raise ValueError("Empty source frame")
    return result.crop(box)


def save_png(image, path):
    metadata = PngImagePlugin.PngInfo()
    metadata.add(b"sRGB", b"\x00")
    image.convert("RGBA").save(path, pnginfo=metadata, optimize=True)


def build(character_id, spec):
    source = Image.open(spec["source"]).convert("RGBA")
    crops = [[clean(source.crop(box)) for box in row] for row in spec["boxes"]]
    # Generated sources use different scales between animation rows. Normalize
    # each track to 44px high, sharing its scale across poses (no frame-by-frame
    # size pumping). Compress only over-wide hair to respect the 32px envelope.
    frames = []
    for row_index, row in enumerate(crops):
        sy = 44 / max(c.height for c in row)
        sx = min(sy, 32 / max(c.width for c in row))
        frames.append([])
        for index, crop in enumerate(row):
            sprite = crop.resize((round(crop.width * sx), round(crop.height * sy)), Image.Resampling.NEAREST)
            sprite = sprite.crop(sprite.getbbox())
            height = 43 if row_index < 3 and index == 1 else max(43, sprite.height)
            if height != sprite.height:
                sprite = sprite.resize((sprite.width, height), Image.Resampling.NEAREST)
            frame = Image.new("RGBA", FRAME)
            frame.paste(sprite, (ANCHOR[0] - sprite.width // 2, ANCHOR[1] + 1 - sprite.height))
            frames[-1].append(frame)

    # Shared 24-colour palette across every pose, with no dithering or alpha fringe.
    pixels = [pixel[:3] for row in frames for frame in row for pixel in frame.getdata() if pixel[3]]
    sample = Image.new("RGB", (len(pixels), 1))
    sample.putdata(pixels)
    palette = sample.quantize(colors=24, method=Image.Quantize.MEDIANCUT)
    atlas = Image.new("RGBA", (295, 293))
    for row_index, row in enumerate(frames):
        for index, frame in enumerate(row):
            alpha = frame.getchannel("A")
            frame = frame.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE).convert("RGBA")
            frame.putalpha(alpha)
            # Zero invisible RGB as well, keeping the gutters genuinely empty.
            frame.putdata([pixel if pixel[3] else (0, 0, 0, 0) for pixel in frame.getdata()])
            frames[row_index][index] = frame
            atlas.paste(frame, (index * 37, row_index * 49))

    dest = OUTPUT / character_id
    dest.mkdir(parents=True, exist_ok=True)
    save_png(atlas, dest / "character.png")
    # Four-times nearest-neighbour preview; crop only the transparent side padding.
    preview = frames[0][0].resize((144, 192), Image.Resampling.NEAREST).crop((8, 0, 136, 192))
    portrait = frames[0][0].crop((2, 1, 34, 33)).resize((64, 64), Image.Resampling.NEAREST)
    save_png(preview, dest / "preview.png")
    save_png(portrait, dest / "portrait.png")
    manifest = {
        "id": character_id, "name": spec["name"], "image": "character.png",
        "frameWidth": 36, "frameHeight": 48, "anchorX": 18, "anchorY": 46,
        "spacing": 1, "columns": 8, "collision": {"width": 14, "height": 10},
        "animations": {name: {"row": i, "frames": 2 if name.startswith("idle") else 4,
                               "fps": 2.5 if name.startswith("idle") else 8}
                       for i, name in enumerate(ANIMATIONS)},
        "fallbacks": {"run": "walk"},
    }
    (dest / "character.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {character_id}: 18 frames, {atlas.width}x{atlas.height}, RGBA, 24 colours")


if __name__ == "__main__":
    for key, value in CHARACTERS.items():
        build(key, value)
