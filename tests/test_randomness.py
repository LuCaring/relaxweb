import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from games import randomness
from games.guandan import build_deck as build_guandan_deck
from games.holdem import new_deck as build_holdem_deck
from games.mahjong import build_wall as build_mahjong_wall
from games.uno import UnoRoom, build_deck as build_uno_deck


class SecureShuffleTests(unittest.TestCase):
    def test_shuffle_delegates_to_system_random(self):
        cards = [1, 2, 3]
        secure_rng = Mock()

        with patch.object(randomness.secrets, "SystemRandom", return_value=secure_rng):
            result = randomness.shuffle(cards)

        secure_rng.shuffle.assert_called_once_with(cards)
        self.assertIsNone(result)

    def test_all_initial_decks_use_shared_secure_shuffle(self):
        with patch.object(randomness, "shuffle", wraps=randomness.shuffle) as shuffle:
            decks = [
                build_holdem_deck(),
                build_uno_deck(),
                build_mahjong_wall(),
                build_guandan_deck(),
            ]

        self.assertEqual([len(deck) for deck in decks], [52, 108, 144, 108])
        self.assertEqual(shuffle.call_count, 4)

    def test_uno_recycled_discard_pile_uses_shared_secure_shuffle(self):
        room = UnoRoom(room_id=1, name="test", owner="alice", buy_in=100, blind=1)
        room.game = {
            "deck": [],
            "discard": [
                {"c": "r", "v": "1"},
                {"c": "g", "v": "2"},
                {"c": "b", "v": "3"},
            ],
            "hands": {"alice": [{"c": "y", "v": "4"}]},
            "uno_pending": set(),
            "uno_deadlines": {},
        }

        with patch.object(randomness, "shuffle", wraps=randomness.shuffle) as shuffle:
            drawn = room.draw_cards("alice", 1)

        self.assertEqual(len(drawn), 1)
        shuffle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
