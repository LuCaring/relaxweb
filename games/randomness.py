"""Shared secure randomness helpers for card and tile games."""

import secrets


def shuffle(items):
    """Shuffle a mutable sequence in place using the operating system RNG."""
    secrets.SystemRandom().shuffle(items)
