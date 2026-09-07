"""
Advertiser task repeat policy tests.
Verifies that create_advertiser_task() stores repeat_policy/repeat_hours correctly.
"""
import os
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
import importlib.util as _ilu

_BOT = Path(__file__).resolve().parent.parent / "ganaihat_bot.py"


def _load_bot():
    spec = _ilu.spec_from_file_location("ganaihat_bot", str(_BOT))
    mod = _ilu.module_from_spec(spec)
    mod.EGP_PER_USD = Decimal("50")
    spec.loader.exec_module(mod)
    return mod


def _setup_db(mod):
    """Create a temp database with minimal schema and return (conn, tmpdir)."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Minimal users table
    conn.execute("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            activation_status INTEGER DEFAULT 1,
            balance_usd_nano INTEGER DEFAULT 0,
            balance_cents INTEGER DEFAULT 0,
            balance_migrated_at TEXT,
            withdrawal_blocked INTEGER DEFAULT 0,
            fraud_reason TEXT,
            fraud_marked_at TEXT,
            referred_by INTEGER,
            referrer_rewarded INTEGER DEFAULT 0
        )
    """)
    # Advertiser with enough balance
    conn.execute(
        "INSERT INTO users (user_id, activation_status, balance_usd_nano) VALUES (?, 1, ?)",
        (999, 1_000_000_000),
    )

    # Manual tasks table
    conn.execute("""
        CREATE TABLE manual_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            task_link TEXT,
            task_type TEXT,
            target_reference TEXT,
            task_instructions TEXT,
            reward_points INTEGER DEFAULT 0,
            reward_usd_nano INTEGER,
            quantity_requested INTEGER DEFAULT 1,
            quantity_remaining INTEGER DEFAULT 1,
            expires_at TEXT,
            task_state TEXT DEFAULT 'AVAILABLE',
            task_origin TEXT DEFAULT 'internal',
            advertiser_id INTEGER,
            total_cost_nano INTEGER,
            repeat_policy TEXT DEFAULT 'one_time',
            repeat_hours INTEGER DEFAULT NULL
        )
    """)
    conn.commit()

    # Patch get_connection to use our temp DB with Row factory
    def _conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c
    mod.get_connection = _conn

    return conn, tmpdir


class TestAdvertiserRepeatPolicy(unittest.TestCase):
    """Verify repeat_policy/repeat_hours reach manual_tasks via create_advertiser_task()."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        self.base_kwargs = dict(
            advertiser_id=999,
            title="Test task",
            task_link="https://t.me/test",
            reward_nano=1_000_000,
            quantity=1,
        )

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_task(self, task_id):
        return self.conn.execute(
            "SELECT * FROM manual_tasks WHERE id = ?", (task_id,)
        ).fetchone()

    def test_one_time(self):
        task_id = self.mod.create_advertiser_task(**self.base_kwargs)
        self.assertIsNotNone(task_id)
        task = self._get_task(task_id)
        self.assertEqual(task["repeat_policy"], "one_time")
        self.assertIsNone(task["repeat_hours"])

    def test_repeat_24(self):
        task_id = self.mod.create_advertiser_task(
            **self.base_kwargs, repeat_policy="repeatable", repeat_hours=24,
        )
        self.assertIsNotNone(task_id)
        task = self._get_task(task_id)
        self.assertEqual(task["repeat_policy"], "repeatable")
        self.assertEqual(task["repeat_hours"], 24)

    def test_repeat_48(self):
        task_id = self.mod.create_advertiser_task(
            **self.base_kwargs, repeat_policy="repeatable", repeat_hours=48,
        )
        self.assertIsNotNone(task_id)
        task = self._get_task(task_id)
        self.assertEqual(task["repeat_policy"], "repeatable")
        self.assertEqual(task["repeat_hours"], 48)

    def test_repeat_custom_6(self):
        task_id = self.mod.create_advertiser_task(
            **self.base_kwargs, repeat_policy="repeatable", repeat_hours=6,
        )
        self.assertIsNotNone(task_id)
        task = self._get_task(task_id)
        self.assertEqual(task["repeat_policy"], "repeatable")
        self.assertEqual(task["repeat_hours"], 6)

    def test_default_is_one_time(self):
        """Calling without explicit repeat_policy should default to one_time."""
        task_id = self.mod.create_advertiser_task(**self.base_kwargs)
        task = self._get_task(task_id)
        self.assertEqual(task["repeat_policy"], "one_time")
        self.assertIsNone(task["repeat_hours"])

    def test_wallet_debited_correctly(self):
        """Wallet debit should still work with repeat_policy passed."""
        balance_before = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 999"
        ).fetchone()[0]
        task_id = self.mod.create_advertiser_task(
            **self.base_kwargs, repeat_policy="repeatable", repeat_hours=12,
        )
        self.assertIsNotNone(task_id)
        balance_after = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 999"
        ).fetchone()[0]
        self.assertLess(balance_after, balance_before)


if __name__ == "__main__":
    unittest.main()
