"""Unit tests for the M0 foundations (stdlib unittest, no network, no GUI).

Run from the project root:

    python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from hd2 import store
from hd2.modules import parse_games, load_registry
from hd2.modules.base import BaseModule, WidgetCard
from hd2.netcache import RateLimiter, http_cached
from hd2.scheduler import Scheduler


class TempDb(unittest.TestCase):
    def setUp(self) -> None:
        store.close()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "test.db"

    def tearDown(self) -> None:
        store.close()
        self._tmp.cleanup()


class StoreTests(TempDb):
    def test_schema_created(self) -> None:
        store.connect(self.db)
        names = set(store.table_names())
        self.assertTrue({"meta", "notes", "matches", "follows", "http_cache"} <= names)
        version = store.query_one("PRAGMA user_version")[0]
        self.assertEqual(version, store.SCHEMA_VERSION)

    def test_crud_helpers(self) -> None:
        store.connect(self.db)
        store.execute("INSERT INTO meta (key, value) VALUES (?, ?)", ("hello", "world"))
        self.assertEqual(store.query_one("SELECT value FROM meta WHERE key=?", ("hello",))["value"], "world")
        self.assertEqual(store.execute("UPDATE meta SET value=? WHERE key=?", ("x", "hello")), 1)
        self.assertEqual(len(store.query("SELECT * FROM meta")), 1)

    def test_reopen_keeps_data(self) -> None:
        store.connect(self.db)
        store.execute("INSERT INTO meta (key, value) VALUES ('a', 'b')")
        store.close()
        store.connect(self.db)
        self.assertIsNotNone(store.query_one("SELECT value FROM meta WHERE key='a'"))


class SchedulerTests(unittest.TestCase):
    def test_runs_due_job_and_reschedules(self) -> None:
        scheduler = Scheduler(tick=0.05)
        calls: list[float] = []
        job = scheduler.add("t", 60, lambda: calls.append(time.time()), run_now=True)
        self.assertEqual(scheduler.tick_once(), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(job.runs, 1)
        self.assertGreater(job.next_at, time.time() + 30)
        self.assertEqual(scheduler.tick_once(), 0)

    def test_failure_backs_off_and_recovers(self) -> None:
        scheduler = Scheduler(tick=0.05)
        state = {"fail": True}

        def flaky() -> None:
            if state["fail"]:
                raise RuntimeError("boom")

        job = scheduler.add("f", 60, flaky, run_now=True, backoff=(10, 40))
        scheduler.tick_once()
        self.assertEqual(job.failures, 1)
        self.assertIn("boom", job.last_error)
        first_delay = job.next_at - time.time()
        self.assertGreater(first_delay, 5)
        job.next_at = 0
        scheduler.tick_once()
        self.assertEqual(job.failures, 2)
        self.assertGreater(job.next_at - time.time(), first_delay - 1)  # exponential
        state["fail"] = False
        job.next_at = 0
        scheduler.tick_once()
        self.assertEqual(job.failures, 0)
        self.assertEqual(job.last_error, "")

    def test_callable_interval_tracks_settings(self) -> None:
        settings = {"poll_seconds": 45}
        scheduler = Scheduler(tick=0.05)
        job = scheduler.add("war", lambda: max(30, min(300, int(settings["poll_seconds"]))), lambda: None)
        self.assertEqual(job.interval_seconds(), 45)
        settings["poll_seconds"] = 120
        self.assertEqual(job.interval_seconds(), 120)

    def test_snapshot_shape(self) -> None:
        scheduler = Scheduler()
        scheduler.add("a", 30, lambda: None, run_now=True)
        scheduler.tick_once()
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot[0]["name"], "a")
        self.assertEqual(snapshot[0]["runs"], 1)
        self.assertIn("last_duration_ms", snapshot[0])


class NetcacheTests(TempDb):
    def setUp(self) -> None:
        super().setUp()
        store.connect(self.db)

    def test_fresh_then_cache_then_stale(self) -> None:
        calls = {"n": 0}
        mode = {"status": 200}

        def fetcher(url, headers, timeout):
            calls["n"] += 1
            if mode["status"] == 200:
                return 200, json.dumps({"ok": True, "n": calls["n"]}).encode(), {"ETag": '"v1"'}
            return mode["status"], b"", {}

        limiter = RateLimiter(0)
        payload, status = http_cached("https://example.test/a", ttl=60, limiter=limiter, fetcher=fetcher)
        self.assertEqual(status, "fresh")
        self.assertEqual(payload["n"], 1)

        payload, status = http_cached("https://example.test/a", ttl=60, limiter=limiter, fetcher=fetcher)
        self.assertEqual(status, "cache")
        self.assertEqual(calls["n"], 1)

        payload, status = http_cached("https://example.test/a", ttl=60, limiter=limiter, fetcher=fetcher, force=True)
        self.assertEqual(status, "fresh")
        self.assertEqual(payload["n"], 2)

        mode["status"] = 429
        payload, status = http_cached("https://example.test/a", ttl=60, limiter=limiter, fetcher=fetcher, force=True)
        self.assertEqual(status, "stale")
        self.assertEqual(payload["n"], 2)  # cached body still served
        row = store.query_one("SELECT next_try FROM http_cache WHERE url=?", ("https://example.test/a",))
        self.assertGreater(row["next_try"], time.time())

    def test_network_error_without_cache(self) -> None:
        def fetcher(url, headers, timeout):
            raise TimeoutError("no network")

        payload, status = http_cached("https://example.test/b", limiter=RateLimiter(0), fetcher=fetcher)
        self.assertIsNone(payload)
        self.assertEqual(status, "error")

    def test_rate_limiter_enforces_gap(self) -> None:
        limiter = RateLimiter(0.2)
        limiter.wait("host")
        started = time.time()
        slept = limiter.wait("host")
        self.assertGreaterEqual(slept, 0.1)
        self.assertGreaterEqual(time.time() - started, 0.1)


class _FakeModule(BaseModule):
    def widget_card(self):
        return WidgetCard(module_id=self.info.id, title=self.info.cn, metrics=(("X", "1"),))


class RegistryTests(unittest.TestCase):
    def test_parse_games_validation(self) -> None:
        payload = {
            "games": [
                {"id": "hd2", "name": "HD2", "cn": "绝地潜兵 2", "kind": ["war"], "module": "hd2", "enabled": True},
                {"id": "hd2", "name": "dup"},
                {"name": "no id"},
                {"id": "r6", "name": "R6", "kind": "esports", "enabled": False, "liquipedia": "rainbowsix"},
                "junk",
            ]
        }
        games = parse_games(payload)
        self.assertEqual([g.id for g in games], ["hd2", "r6"])
        self.assertEqual(games[1].kinds, ("esports",))
        self.assertEqual(games[1].extra["liquipedia"], "rainbowsix")
        self.assertFalse(games[1].enabled)

    def test_load_registry_binds_available_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "games.json"
            path.write_text(
                json.dumps(
                    {
                        "games": [
                            {"id": "hd2", "name": "HD2", "cn": "绝地潜兵 2", "kind": ["war"], "module": "hd2", "enabled": True},
                            {"id": "r6", "name": "R6", "cn": "彩虹六号", "kind": ["esports"], "enabled": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            registry = load_registry({}, path=path, factories={"hd2": lambda info, services: _FakeModule(info)})
        self.assertEqual(registry.error, "")
        described = {row["id"]: row for row in registry.describe()}
        self.assertTrue(described["hd2"]["available"])
        self.assertFalse(described["r6"]["available"])
        self.assertEqual(len(registry.enabled()), 1)
        self.assertEqual(registry.cards()[0]["title"], "绝地潜兵 2")

    def test_missing_registry_is_tolerated(self) -> None:
        registry = load_registry({}, path=Path(tempfile.gettempdir()) / "definitely-missing-games.json")
        self.assertEqual(registry.games(), [])
        self.assertIn("unreadable", registry.error)


class BundledRegistryTests(unittest.TestCase):
    def test_bundled_games_json_loads(self) -> None:
        from hd2.config import DATA_DIR

        registry = load_registry({}, path=DATA_DIR / "games.json")
        self.assertEqual(registry.error, "")
        ids = [game.id for game in registry.games()]
        self.assertEqual(ids, ["hd2", "r6", "lol", "ow2"])
        described = {row["id"]: row for row in registry.describe()}
        self.assertTrue(described["hd2"]["available"])
        # Placeholders must stay listed even though their modules do not exist yet.
        for placeholder in ("r6", "lol", "ow2"):
            self.assertIn(placeholder, described)
            self.assertFalse(described[placeholder]["available"])


if __name__ == "__main__":
    unittest.main()
