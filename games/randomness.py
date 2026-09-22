"""Shared secure randomness helpers for card and tile games."""

import secrets


def shuffle(items):
    """Shuffle a mutable sequence in place using the operating system RNG."""
    secrets.SystemRandom().shuffle(items)


def roll_die(sides=6):
    """Roll a fair die using the operating system RNG."""
    return secrets.SystemRandom().randint(1, sides)
