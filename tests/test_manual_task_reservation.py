"""
Manual task reservation layer — runtime regression tests.

Proves:
- Reservation creation
- expires_at = reserved_at + 15 minutes
- Only one active reservation per task slot
- Reservation belongs to the correct worker
- Reservation does NOT create task_completions
- Reservation does NOT change wallet balance
"""
import os
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

import importlib.util as _ilu
from pathlib import Path

_BOT = Path(__file__).resolve().parent.parent / "ganaihat_bot.py"


def _load_bot():
    spec = _ilu.spec_from_file_location("ganaihat_bot", str(_BOT))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["BOT_DB_PATH"] = path
    bot = _load_bot()
    bot.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Order 2 removed balance_usd_nano from init_db(); add it for reservation tests.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "balance_usd_nano" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN balance_usd_nano INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    return bot, conn, path


class TestReservationCreation(unittest.TestCase):
    """Basic reservation lifecycle."""

    def test_creates_reservation(self):
        bot, conn, path = _make_db()
        try:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining) "
                "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, 3, 3)"
            )
            conn.commit()
            task_id = conn.execute("SELECT id FROM manual_tasks LIMIT 1").fetchone()["id"]

            conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
                "VALUES (100, 'W', 'w', 0)"
            )
            conn.commit()

            r = bot.create_manual_task_reservation(task_id, 100)
            self.assertIsNotNone(r)
            self.assertEqual(r["task_id"], task_id)
            self.assertEqual(r["worker_id"], 100)
            self.assertEqual(r["status"], "active")
        finally:
            conn.close()
            os.unlink(path)

    def test_expires_at_is_15_minutes(self):
        bot, conn, path = _make_db()
        try:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining) "
                "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, 3, 3)"
            )
            conn.commit()
            task_id = conn.execute("SELECT id FROM manual_tasks LIMIT 1").fetchone()["id"]

            conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
                "VALUES (200, 'W2', 'w2', 0)"
            )
            conn.commit()

            r = bot.create_manual_task_reservation(task_id, 200)
            reserved_at = datetime.fromisoformat(r["reserved_at"])
            expires_at = datetime.fromisoformat(r["expires_at"])
            diff = expires_at - reserved_at
            self.assertEqual(diff.total_seconds(), 15 * 60)
        finally:
            conn.close()
            os.unlink(path)


class TestReservationExclusivity(unittest.TestCase):
    """Only one active reservation per task at a time."""

    def test_blocks_different_worker(self):
        bot, conn, path = _make_db()
        try:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining) "
                "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, 3, 3)"
            )
            conn.commit()
            task_id = conn.execute("SELECT id FROM manual_tasks LIMIT 1").fetchone()["id"]

            for uid in (300, 301):
                conn.execute(
                    "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
                    "VALUES (?, 'U', 'u', 0)", (uid,)
                )
            conn.commit()

            # Worker 300 reserves first.
            r1 = bot.create_manual_task_reservation(task_id, 300)
            self.assertIsNotNone(r1)

            # Worker 301 must be blocked.
            r2 = bot.create_manual_task_reservation(task_id, 301)
            self.assertIsNone(r2)
        finally:
            conn.close()
            os.unlink(path)

    def test_same_worker_can_refresh(self):
        bot, conn, path = _make_db()
        try:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining) "
                "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, 3, 3)"
            )
            conn.commit()
            task_id = conn.execute("SELECT id FROM manual_tasks LIMIT 1").fetchone()["id"]

            conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
                "VALUES (400, 'W', 'w', 0)"
            )
            conn.commit()

            r1 = bot.create_manual_task_reservation(task_id, 400)
            self.assertIsNotNone(r1)
            r2 = bot.create_manual_task_reservation(task_id, 400)
            self.assertIsNotNone(r2)
            self.assertNotEqual(r1["id"], r2["id"])
        finally:
            conn.close()
            os.unlink(path)


class TestReservationBelongsToCorrectWorker(unittest.TestCase):
    def test_worker_id_is_correct(self):
        bot, conn, path = _make_db()
        try:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining) "
                "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, 3, 3)"
            )
            conn.commit()
            task_id = conn.execute("SELECT id FROM manual_tasks LIMIT 1").fetchone()["id"]

            conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
                "VALUES (500, 'W', 'w', 0)"
            )
            conn.commit()

            r = bot.create_manual_task_reservation(task_id, 500)
            active = bot.get_active_manual_task_reservation(task_id)
            self.assertIsNotNone(active)
            self.assertEqual(active["worker_id"], 500)
        finally:
            conn.close()
            os.unlink(path)


class TestReservationDoesNotComplete(unittest.TestCase):
    """Reservation must NOT insert into task_completions or change balance."""

    def test_no_task_completion(self):
        bot, conn, path = _make_db()
        try:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining) "
                "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, 3, 3)"
            )
            conn.commit()
            task_id = conn.execute("SELECT id FROM manual_tasks LIMIT 1").fetchone()["id"]

            conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
                "VALUES (600, 'W', 'w', 0)"
            )
            conn.commit()

            bot.create_manual_task_reservation(task_id, 600)

            tc = conn.execute(
                "SELECT 1 FROM task_completions WHERE user_id = 600 AND task_key = ?",
                (f"manual_task:{task_id}",),
            ).fetchone()
            self.assertIsNone(tc, "reservation must NOT create task_completions row")
        finally:
            conn.close()
            os.unlink(path)

    def test_balance_unchanged(self):
        bot, conn, path = _make_db()
        try:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining) "
                "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, 3, 3)"
            )
            conn.commit()
            task_id = conn.execute("SELECT id FROM manual_tasks LIMIT 1").fetchone()["id"]

            conn.execute(
                "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
                "VALUES (700, 'W', 'w', 0)"
            )
            conn.commit()

            bot.create_manual_task_reservation(task_id, 700)

            balance = conn.execute(
                "SELECT balance_usd_nano FROM users WHERE user_id = 700"
            ).fetchone()["balance_usd_nano"]
            self.assertEqual(balance, 0, "reservation must NOT change wallet balance")
        finally:
            conn.close()
            os.unlink(path)


class TestLazyExpiration(unittest.TestCase):
    def test_expired_reservation_allows_new_worker(self):
        bot, conn, path = _make_db()
        try:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining) "
                "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, 3, 3)"
            )
            conn.commit()
            task_id = conn.execute("SELECT id FROM manual_tasks LIMIT 1").fetchone()["id"]

            for uid in (800, 801):
                conn.execute(
                    "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
                    "VALUES (?, 'U', 'u', 0)", (uid,)
                )
            conn.commit()

            # Worker 800 reserves.
            r = bot.create_manual_task_reservation(task_id, 800)
            self.assertIsNotNone(r)

            # Manually expire the reservation by backdating expires_at.
            conn.execute(
                "UPDATE manual_task_reservations SET expires_at = datetime('now', '-1 minute') "
                "WHERE id = ?", (r["id"],)
            )
            conn.commit()

            # Worker 801 should now be able to reserve.
            r2 = bot.create_manual_task_reservation(task_id, 801)
            self.assertIsNotNone(r2)
            self.assertEqual(r2["worker_id"], 801)
        finally:
            conn.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
