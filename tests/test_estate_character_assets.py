"""Character PNG/manifest invariants; standard library only (Pillow is for rebuilding)."""
import json
from pathlib import Path
import struct
import unittest
import zlib

from estate.catalog import SKINS

ROOT = Path(__file__).resolve().parent.parent
CHARACTERS = ROOT / 'assets/estate/characters'
IDS = tuple(dict.fromkeys(skin.get("asset_id", key) for key, skin in SKINS.items()))


def png(path):
    data = path.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    offset, compressed, chunks = 8, b'', {}
    while offset < len(data):
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + size]
        chunks[kind] = body
        if kind == b'IDAT':
            compressed += body
        offset += size + 12
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunks[b'IHDR'])
    assert (depth, color, compression, filtering, interlace) == (8, 6, 0, 0, 0), 'Expected 8-bit non-interlaced RGBA'
    assert chunks[b'sRGB'] == b'\0'
    data = zlib.decompress(compressed)
    stride = width * 4
    previous, rows = bytearray(stride), []
    for y in range(height):
        start = y * (stride + 1)
        method = data[start]
        row = bytearray(data[start + 1:start + 1 + stride])
        for x in range(stride):
            left, up, corner = row[x - 4] if x >= 4 else 0, previous[x], previous[x - 4] if x >= 4 else 0
            if method == 1:
                predict = left
            elif method == 2:
                predict = up
            elif method == 3:
                predict = (left + up) // 2
            elif method == 4:
                p = left + up - corner
                distances = (abs(p - left), abs(p - up), abs(p - corner))
                predict = (left, up, corner)[distances.index(min(distances))]
            else:
                assert method == 0
                predict = 0
            row[x] = (row[x] + predict) & 255
        rows.append([tuple(row[x:x + 4]) for x in range(0, stride, 4)])
        previous = row
    return width, height, rows


class CharacterAssetTests(unittest.TestCase):
    def test_all_registered_characters_have_standard_manifests(self):
        self.assertTrue(set(IDS) <= {path.parent.name for path in CHARACTERS.glob('*/character.json')})
        for key in IDS:
            with self.subTest(character=key):
                manifest = json.loads((CHARACTERS / key / 'character.json').read_text(encoding='utf-8'))
                self.assertEqual(manifest['id'], key)
                self.assertEqual(manifest['assetScale'], 4)
                self.assertEqual([manifest[k] for k in ('frameWidth', 'frameHeight', 'anchorX', 'anchorY', 'columns', 'spacing')], [144, 192, 72, 184, 8, 4])
                self.assertEqual(manifest['collision'], {'width': 14, 'height': 10})
                self.assertEqual(manifest['fallbacks'], {'run': 'walk'})
                self.assertEqual(sum(a['frames'] for a in manifest['animations'].values()), 18)
                for row, name in enumerate(('idle_down', 'idle_up', 'idle_right', 'walk_down', 'walk_up', 'walk_right')):
                    self.assertEqual(manifest['animations'][name], {'row': row, 'frames': 2 if row < 3 else 4, 'fps': 2.5 if row < 3 else 8})

    def test_rgba_palette_gutters_margins_and_foot_anchor(self):
        for key in IDS:
            with self.subTest(character=key):
                width, height, pixels = png(CHARACTERS / key / 'character.png')
                self.assertEqual((width, height), (1180, 1172))
                opaque = {p for row in pixels for p in row if p[3]}
                self.assertGreater(len(opaque), 256)
                self.assertGreater(len({p[3] for row in pixels for p in row}), 2)
                for y, row in enumerate(pixels):
                    for x, p in enumerate(row):
                        if y % 196 >= 192 or x % 148 >= 144 or x // 148 >= (2 if y // 196 < 3 else 4):
                            self.assertEqual(p, (0, 0, 0, 0), (key, x, y))
                for row in range(6):
                    for col in range(2 if row < 3 else 4):
                        occupied = [(x, y) for y in range(192) for x in range(144) if pixels[row * 196 + y][col * 148 + x][3]]
                        self.assertTrue(occupied)
                        xs, ys = zip(*occupied)
                        self.assertGreaterEqual(min(xs), 4)
                        self.assertLessEqual(max(xs), 139)
                        self.assertGreaterEqual(min(ys), 1)
                        self.assertEqual(max(ys), 187, (key, row, col))
                        self.assertGreaterEqual(max(ys) - min(ys) + 1, 172)
                        self.assertLessEqual(max(ys) - min(ys) + 1, 188)

    def test_portrait_and_preview_are_transparent_rgba(self):
        for key in IDS:
            for name, size in (('portrait.png', (64, 64)), ('preview.png', (128, 192))):
                with self.subTest(character=key, asset=name):
                    width, height, rows = png(CHARACTERS / key / name)
                    self.assertEqual((width, height), size)
                    alphas = {pixel[3] for row in rows for pixel in row}
                    self.assertIn(0, alphas)
                    self.assertIn(255, alphas)
                    self.assertGreater(len(alphas), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
