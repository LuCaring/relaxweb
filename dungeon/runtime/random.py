"""Versioned named deterministic streams with serializable positions."""

import hashlib


class NamedRandom:
    VERSION = 1

    def __init__(self, seed_hex, positions=None):
        if not isinstance(seed_hex, str) or len(seed_hex) != 64:
            raise ValueError("invalid RNG seed")
        self.seed = bytes.fromhex(seed_hex)
        self.positions = dict(positions or {})
        for name, position in self.positions.items():
            if not isinstance(name, str) or not name or type(position) is not int or position < 0:
                raise ValueError("invalid RNG position")

    def random_int(self, stream, lower, upper):
        if (not isinstance(stream, str) or not stream or len(stream) > 80
                or type(lower) is not int or type(upper) is not int
                or lower > upper or upper - lower >= 2**32):
            raise ValueError("invalid RNG draw")
        span = upper - lower + 1
        limit = (2**256 // span) * span
        while True:
            position = self.positions.get(stream, 0)
            self.positions[stream] = position + 1
            block = hashlib.sha256(self.seed + b"\0" + stream.encode("utf-8")
                                   + b"\0" + position.to_bytes(16, "big")).digest()
            number = int.from_bytes(block, "big")
            if number < limit:
                return lower + number % span

    def snapshot(self):
        return {"version": self.VERSION, "seed_hex": self.seed.hex(),
                "positions": dict(self.positions)}

    @classmethod
    def restore(cls, value):
        if not isinstance(value, dict) or value.get("version") != cls.VERSION:
            raise ValueError("unsupported RNG save")
        return cls(value["seed_hex"], value["positions"])
