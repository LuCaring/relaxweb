#!/usr/bin/env python3
"""Normalize the supplied estate character sheets. Requires Pillow >= 9.

Sources remain untouched. Supplied PNGs live in assets/estate/characters/sources/.
The atlas keeps four source pixels for each logical game pixel so detailed
supplied artwork remains crisp when the map scales it down for display.
"""
from pathlib import Path
import json
from PIL import Image, PngImagePlugin

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets/estate/characters"
ASSET_SCALE = 4
LOGICAL_FRAME = (36, 48)
LOGICAL_ANCHOR = (18, 46)
FRAME = tuple(value * ASSET_SCALE for value in LOGICAL_FRAME)
ANCHOR = tuple(value * ASSET_SCALE for value in LOGICAL_ANCHOR)
SPACING = ASSET_SCALE
ANIMATIONS = ["idle_down", "idle_up", "idle_right", "walk_down", "walk_up", "walk_right"]


def cells(width, bands, row, columns):
    return [(round(i * width), bands[row], round((i + 1) * width), bands[row + 1])
            for i in columns]


def directional_grid(width, height, columns=8):
    """Map three equal down/up/right rows to the shared idle/walk tracks."""
    cell_width = width / columns
    bands = [round(i * height / 3) for i in range(4)]
    return [
        *[cells(cell_width, bands, row, [0, 1]) for row in range(3)],
        *[cells(cell_width, bands, row, [0, 1, 2, 3]) for row in range(3)],
    ]


def portrait_sheet():
    """Map the 1783x882 mixed idle/walk layout used by portrait characters."""
    return [
        [(8, 0, 253, 307), (258, 0, 478, 307)],
        [(489, 0, 714, 307), (718, 0, 937, 307)],
        [(941, 0, 1145, 307), (1163, 0, 1355, 307)],
        [(0, 307, 241, 594), (241, 307, 467, 594), (467, 307, 689, 594), (689, 307, 908, 594)],
        [(908, 307, 1127, 594), (1127, 307, 1351, 594), (1351, 307, 1577, 594), (1577, 307, 1783, 594)],
        [(0, 594, 234, 882), (234, 594, 450, 882), (450, 594, 675, 882), (675, 594, 900, 882)],
    ]


CHARACTERS = {
    "berry": {
        "name": "小女孩",
        "source": ROOT / "assets/estate/nature/player/character-sheet.png",
        "boxes": [
            cells(111, [0, 133], 0, [0, 1]),
            cells(111, [0, 133], 0, [2, 3]),
            cells(111, [0, 133], 0, [4, 5]),
            cells(111, [133, 264, 395, 531], 0, [0, 2, 4, 6]),
            cells(111, [133, 264, 395, 531], 1, [1, 3, 5, 7]),
            cells(111, [133, 264, 395, 531], 2, [0, 2, 4, 6]),
        ],
    },
    "steve": {
        "name": "史蒂夫",
        "source": OUTPUT / "sources/steve.png",
        "boxes": directional_grid(1829, 860),
    },
    "dva": {
        "name": "DVA",
        "source": OUTPUT / "sources/dva.png",
        "boxes": directional_grid(1828, 860),
    },
    "little_gwen": {
        "name": "小小格温",
        "source": OUTPUT / "sources/little_gwen.png",
        "boxes": directional_grid(1832, 859),
    },
    "jamie": {
        "name": "杰米",
        "source": OUTPUT / "sources/jamie.png",
        "boxes": directional_grid(1832, 859),
    },
    "xiaofei": {
        "name": "小菲",
        "source": OUTPUT / "sources/xiaofei.png",
        "boxes": portrait_sheet(),
    },
    "weichong": {
        "name": "威虫",
        "source": OUTPUT / "sources/weichong.png",
        "boxes": directional_grid(1312, 1199, columns=4),
    },
    "collection_reward": {
        "name": "珍藏旅人",
        "source": OUTPUT / "sources/collection_reward.png",
        "boxes": portrait_sheet(),
    },
}


def clean(image):
    """Remove only unmistakable red cutting marks; preserve source antialiasing."""
    result = image.convert("RGBA")
    result.putdata([
        (0, 0, 0, 0) if r > 220 and g < 35 and b < 45 and a > 0
        else (r, g, b, a)
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
    # each track to 176px high, sharing its scale across poses (no frame-by-frame
    # size pumping). The 4x atlas retains facial and clothing details that were
    # previously destroyed by the 44px/24-colour conversion.
    frames = []
    for row_index, row in enumerate(crops):
        sy = (44 * ASSET_SCALE) / max(c.height for c in row)
        sx = min(sy, (32 * ASSET_SCALE) / max(c.width for c in row))
        frames.append([])
        for index, crop in enumerate(row):
            sprite = crop.resize(
                (round(crop.width * sx), round(crop.height * sy)),
                Image.Resampling.LANCZOS,
            )
            sprite = sprite.crop(sprite.getbbox())
            height = 43 * ASSET_SCALE if row_index < 3 and index == 1 else max(43 * ASSET_SCALE, sprite.height)
            if height != sprite.height:
                sprite = sprite.resize((sprite.width, height), Image.Resampling.LANCZOS)
            frame = Image.new("RGBA", FRAME)
            frame.alpha_composite(
                sprite,
                (ANCHOR[0] - sprite.width // 2, ANCHOR[1] + ASSET_SCALE - sprite.height),
            )
            frames[-1].append(frame)

    atlas = Image.new("RGBA", (
        (FRAME[0] + SPACING) * 8 - SPACING,
        (FRAME[1] + SPACING) * len(ANIMATIONS) - SPACING,
    ))
    for row_index, row in enumerate(frames):
        for index, frame in enumerate(row):
            frames[row_index][index] = frame
            atlas.alpha_composite(frame, (index * (FRAME[0] + SPACING), row_index * (FRAME[1] + SPACING)))

    dest = OUTPUT / character_id
    dest.mkdir(parents=True, exist_ok=True)
    save_png(atlas, dest / "character.png")
    # Preview is now a direct crop of the high-resolution frame. Portrait uses
    # high-quality reduction instead of enlarging the old 32px crop.
    preview = frames[0][0].crop((8, 0, 136, 192))
    portrait = frames[0][0].crop((8, 4, 136, 132)).resize((64, 64), Image.Resampling.LANCZOS)
    save_png(preview, dest / "preview.png")
    save_png(portrait, dest / "portrait.png")
    manifest = {
        "id": character_id, "name": spec["name"], "image": "character.png",
        "frameWidth": FRAME[0], "frameHeight": FRAME[1],
        "anchorX": ANCHOR[0], "anchorY": ANCHOR[1],
        "assetScale": ASSET_SCALE, "spacing": SPACING, "columns": 8,
        "collision": {"width": 14, "height": 10},
        "animations": {name: {"row": i, "frames": 2 if name.startswith("idle") else 4,
                               "fps": 2.5 if name.startswith("idle") else 8}
                       for i, name in enumerate(ANIMATIONS)},
        "fallbacks": {"run": "walk"},
    }
    (dest / "character.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {character_id}: 18 frames, {atlas.width}x{atlas.height}, high-resolution RGBA")


if __name__ == "__main__":
    for key, value in CHARACTERS.items():
        build(key, value)
