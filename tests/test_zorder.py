"""Window layer setting: legacy migration and the native three-state helper."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hd2 import config, winflash


class ZorderMigrationTest(unittest.TestCase):
    def load(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(config, "SETTINGS_PATH", path):
                return config.load_settings()

    def test_defaults_to_top(self):
        settings = self.load({})
        self.assertEqual(settings["window_zorder"], "top")
        self.assertIs(settings["always_on_top"], True)

    def test_legacy_false_becomes_normal(self):
        settings = self.load({"always_on_top": False})
        self.assertEqual(settings["window_zorder"], "normal")
        self.assertIs(settings["always_on_top"], False)

    def test_legacy_true_stays_top(self):
        settings = self.load({"always_on_top": True})
        self.assertEqual(settings["window_zorder"], "top")

    def test_explicit_value_wins_over_legacy_mirror(self):
        settings = self.load({"window_zorder": "bottom", "always_on_top": True})
        self.assertEqual(settings["window_zorder"], "bottom")
        self.assertIs(settings["always_on_top"], False)

    def test_unknown_value_falls_back_to_legacy(self):
        settings = self.load({"window_zorder": "sideways", "always_on_top": False})
        self.assertEqual(settings["window_zorder"], "normal")

    def test_normalize_zorder(self):
        self.assertEqual(config.normalize_zorder(" BOTTOM "), "bottom")
        self.assertEqual(config.normalize_zorder(None, {"always_on_top": False}), "normal")
        self.assertEqual(config.normalize_zorder("nope", {"always_on_top": True}), "top")
        self.assertEqual(config.normalize_zorder(None), "top")


class ZorderNativeTest(unittest.TestCase):
    def test_missing_window_is_a_no_op(self):
        self.assertFalse(winflash.set_window_zorder("hd2 :: no such window", "bottom"))
        self.assertFalse(winflash.set_window_zorder("hd2 :: no such window", "top"))
        self.assertEqual(winflash.get_window_zorder("hd2 :: no such window"), "normal")

    def test_keeper_starts_idle(self):
        keeper = winflash.ZOrderKeeper("hd2 :: no such window", interval=0.2)
        self.assertEqual(keeper.position, "normal")
        keeper.set_position("bottom")
        self.assertEqual(keeper.position, "bottom")
        keeper.set_position("bogus")
        self.assertEqual(keeper.position, "normal")
        keeper.tick()  # must not raise or hit any window


if __name__ == "__main__":
    unittest.main()
