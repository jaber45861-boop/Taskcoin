"""
Repeat Policy tests for manual tasks.
"""
import os
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock

import importlib.util as _ilu
from decimal import Decimal
from pathlib import Path

import reward_api as _reward_api

_BOT = Path(__file__).resolve().parent.parent / "ganaihat_bot.py"


def _load_bot():
    spec = _ilu.spec_from_file_location("ganaihat_bot", str(_BOT))
    mod = _ilu.module_from_spec(spec)
    mod.EGP_PER_USD = Decimal("50")
    spec.loader.exec_module(mod)
    # Patch bot.get_chat_member so is_subscribed always returns True.
    _member = MagicMock()
    _member.status = "member"
    mod.bot.get_chat_member = lambda chat, uid: _member
    _reward_api._live_egp_per_usd = Decimal("50")
    return mod


def _make_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["BOT_DB_PATH"] = path
    bot = _load_bot()
    bot.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "balance_usd_nano" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN balance_usd_nano INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    return bot, conn, path


def _create_task(conn, repeat_policy="one_time", repeat_hours=None, quantity=5):
    conn.execute(
        "INSERT INTO manual_tasks "
        "(title, task_link, task_type, target_reference, task_instructions, "
        "reward_points, quantity_requested, quantity_remaining, "
        "repeat_policy, repeat_hours) "
        "VALUES ('T', 'http://x', 'telegram_channel', '@ch', '', 500, ?, ?, ?, ?)",
        (quantity, quantity, repeat_policy, repeat_hours),
    )
    conn.commit()
    return conn.execute("SELECT id FROM manual_tasks ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _create_worker(conn, uid):
    conn.execute(
        "INSERT INTO users (user_id, first_name, username, balance_usd_nano) "
        "VALUES (?, 'W', 'w', 0)", (uid,)
    )
    conn.commit()


class TestOneTimeBlocksReExecution(unittest.TestCase):
    def test_one_time_blocks_second_claim(self):
        bot, conn, path = _make_db()
        try:
            task_id = _create_task(conn, repeat_policy="one_time")
            _create_worker(conn, 100)
            result = bot.claim_manual_task(task_id, 100)
            self.assertEqual(result, "claimed")
            result2 = bot.claim_manual_task(task_id, 100)
            self.assertEqual(result2, "already_done")
        finally:
            conn.close()
            os.unlink(path)


class TestRepeatableAllowsAfterCooldown(unittest.TestCase):
    def test_repeatable_allows_after_hours(self):
        bot, conn, path = _make_db()
        try:
            task_id = _create_task(conn, repeat_policy="repeatable", repeat_hours=6)
            _create_worker(conn, 200)
            result = bot.claim_manual_task(task_id, 200)
            self.assertEqual(result, "claimed")
            conn.execute(
                "UPDATE task_completions SET done_at = datetime('now', '-7 hours') "
                "WHERE user_id = 200 AND task_key = ?",
                (f"manual_task:{task_id}",),
            )
            conn.commit()
            result2 = bot.claim_manual_task(task_id, 200)
            self.assertEqual(result2, "claimed")
        finally:
            conn.close()
            os.unlink(path)


class TestRepeatableBlocksBeforeCooldown(unittest.TestCase):
    def test_repeatable_blocks_before_hours(self):
        bot, conn, path = _make_db()
        try:
            task_id = _create_task(conn, repeat_policy="repeatable", repeat_hours=24)
            _create_worker(conn, 300)
            result = bot.claim_manual_task(task_id, 300)
            self.assertEqual(result, "claimed")
            result2 = bot.claim_manual_task(task_id, 300)
            self.assertEqual(result2, "repeat_cooldown")
        finally:
            conn.close()
            os.unlink(path)


class TestCooldownFromLastExecution(unittest.TestCase):
    def test_cooldown_from_last_execution(self):
        bot, conn, path = _make_db()
        try:
            task_id = _create_task(conn, repeat_policy="repeatable", repeat_hours=2)
            _create_worker(conn, 400)
            bot.claim_manual_task(task_id, 400)
            conn.execute(
                "UPDATE task_completions SET done_at = datetime('now', '-3 hours') "
                "WHERE user_id = 400 AND task_key = ?",
                (f"manual_task:{task_id}",),
            )
            conn.commit()
            result = bot.claim_manual_task(task_id, 400)
            self.assertEqual(result, "claimed")
            result2 = bot.claim_manual_task(task_id, 400)
            self.assertEqual(result2, "repeat_cooldown")
        finally:
            conn.close()
            os.unlink(path)


class TestChangingSettingDoesNotBreakExecution(unittest.TestCase):
    def test_change_policy_after_first_claim(self):
        bot, conn, path = _make_db()
        try:
            task_id = _create_task(conn, repeat_policy="repeatable", repeat_hours=1)
            _create_worker(conn, 500)
            result = bot.claim_manual_task(task_id, 500)
            self.assertEqual(result, "claimed")
            conn.execute(
                "UPDATE manual_tasks SET repeat_policy = 'one_time' WHERE id = ?",
                (task_id,),
            )
            conn.commit()
            result2 = bot.claim_manual_task(task_id, 500)
            self.assertEqual(result2, "already_done")
        finally:
            conn.close()
            os.unlink(path)


class TestDefaultOneTimeBehavior(unittest.TestCase):
    def test_default_blocks_second_claim(self):
        bot, conn, path = _make_db()
        try:
            task_id = _create_task(conn, repeat_policy="one_time")
            _create_worker(conn, 600)
            result = bot.claim_manual_task(task_id, 600)
            self.assertEqual(result, "claimed")
            result2 = bot.claim_manual_task(task_id, 600)
            self.assertEqual(result2, "already_done")
        finally:
            conn.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
