# Dungeon Beta art intake

These PNGs were provided by the project artist on 2026-09-25. The source images are preserved without resizing or repainting. The current browser prototype uses the tomato player, enemy idle/move sheets, floor, pickup, and shop icons. Other sheets are staged for later animation and UI integration.

- `player/idle-source.png`: five rows, six columns; `move-source.png`: five rows, eight columns. The player is intentionally a tomato. Both sheets have uneven generated cell dimensions, so the prototype uses measured frame boundaries and mirrors the left-facing side row when moving right. The row mapping is provisional and should be checked visually before production use.
- `enemies/cave_bat/` and `enemies/stone_brute/`: each action sheet is 2172 × 724, six nominal frames of 362 × 724. `frames.json` records the available actions. The source frames contain generous transparent padding. The stone brute now plays its supplied `move.png` during arena movement.
- `player/blade-attack.png` and `player/staff-attack.png`: six nominal frames each, played during blade and flame staff attacks. The attack sheets use a slightly different tomato design from the idle/move sheets. Some figures and effects cross nominal frame boundaries, so the fixed-width playback may clip or show adjacent pixels; clean, separated frames are needed for a final export.
- `weapons/`: source icons and projectile images. `club-b.png` is an alternative to the active `club-a.png`. `fireball-sheet.png` is a 2 × 2 source sheet. Runtime projectile animation and swing overlays are still pending.
- `arena/`: pickup images, stone floor, and obstacle atlas. The floor is a full texture; seamless tiling and collision placement have not been verified.
- `ui/`, `icons/`, `effects/`: source atlases and icons. These are not yet cut into deployable nine-slice UI pieces or small effect frames.

The shop scales the source icons in CSS. For pixel-perfect release art, the uneven player sheet, oversized icons, and atlases need a separate export pass and visual review. Permanent game rewards remain controlled by the server; these images only change presentation.
