"""Shared dungeon application error."""


class DungeonError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
