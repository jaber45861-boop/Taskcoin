"""
Slot-level reservation tests.
Verifies that multiple workers can reserve slots of the same task,
that slot counting works, that late completion is rejected, and that
repeat policy is preserved.
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
import shutil
import importlib.util as _ilu

_BOT = Path(__file__).resolve().parent.parent / "ganaihat_bot.py"


def _load_bot():
    spec = _ilu.spec_from_file_location("ganaihat_bot", str(_BOT))
    mod = _ilu.module_from_spec(spec)
    mod.EGP_PER_USD = Decimal("50")
    spec.loader.exec_module(mod)
    return mod


def _setup_db(mod):
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

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
            status TEXT DEFAULT 'active',
            task_origin TEXT DEFAULT 'internal',
            advertiser_id INTEGER,
            total_cost_nano INTEGER,
            repeat_policy TEXT DEFAULT 'one_time',
            repeat_hours INTEGER DEFAULT NULL,
            reservation_minutes INTEGER DEFAULT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE task_completions (
            user_id INTEGER NOT NULL,
            task_key TEXT NOT NULL,
            done_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, task_key)
        )
    """)
    conn.execute("""
        CREATE TABLE manual_task_reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            worker_id INTEGER NOT NULL,
            reserved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        )
    """)
    # Slot-level unique index: one active reservation per (task_id, worker_id)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_task_reservation_active "
        "ON manual_task_reservations(task_id, worker_id) WHERE status = 'active'"
    )
    conn.commit()

    def _conn():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    mod.get_connection = _conn
    mod.is_subscribed = lambda user_id, channel: True
    return conn, tmpdir


def _create_task(conn, **overrides):
    defaults = dict(
        title="Test task",
        task_link="https://t.me/test",
        task_type="telegram_channel",
        target_reference="@test",
        reward_usd_nano=1_000_000,
        quantity_remaining=5,
        status="active",
        repeat_policy="one_time",
        reservation_minutes=None,
    )
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    cur = conn.execute(
        f"INSERT INTO manual_tasks ({cols}) VALUES ({placeholders})",
        list(defaults.values()),
    )
    conn.commit()
    return cur.lastrowid


def _create_user(conn, user_id, balance=1_000_000_000):
    conn.execute(
        "INSERT INTO users (user_id, activation_status, balance_usd_nano) VALUES (?, 1, ?)",
        (user_id, balance),
    )
    conn.commit()


def _total_reservations(conn, task_id, status="active"):
    return conn.execute(
        "SELECT COUNT(*) as cnt FROM manual_task_reservations "
        "WHERE task_id = ? AND status = ?",
        (task_id, status),
    ).fetchone()["cnt"]


class TestSlotLevelMultiWorker(unittest.TestCase):
    """A: Multiple workers can complete the same task (slot-level)."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        for uid in (100, 200, 300, 400, 500):
            _create_user(self.conn, uid)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_quantity_10_three_workers_complete(self):
        """quantity=10: A, B, C each claim → all succeed, quantity=7."""
        task_id = _create_task(self.conn, quantity_remaining=10, reservation_minutes=15)
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "claimed")
        self.assertEqual(self.mod.claim_manual_task(task_id, 200), "claimed")
        self.assertEqual(self.mod.claim_manual_task(task_id, 300), "claimed")
        task = self.conn.execute(
            "SELECT quantity_remaining FROM manual_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        self.assertEqual(task["quantity_remaining"], 7)

    def test_quantity_3_three_workers_fourth_rejected(self):
        """quantity=3: A, B, C succeed; D rejected (no slots left)."""
        task_id = _create_task(self.conn, quantity_remaining=3, reservation_minutes=15)
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "claimed")
        self.assertEqual(self.mod.claim_manual_task(task_id, 200), "claimed")
        self.assertEqual(self.mod.claim_manual_task(task_id, 300), "claimed")
        result = self.mod.claim_manual_task(task_id, 400)
        self.assertIn(result, ("slot_held", "unavailable"))

    def test_same_worker_no_double_claim(self):
        """Same worker cannot claim twice (one_time task)."""
        task_id = _create_task(self.conn, quantity_remaining=5, reservation_minutes=15)
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "claimed")
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "already_done")

    def test_reservation_created_per_worker(self):
        """Each worker gets their own reservation row."""
        task_id = _create_task(self.conn, quantity_remaining=5, reservation_minutes=15)
        self.mod.claim_manual_task(task_id, 100)
        self.mod.claim_manual_task(task_id, 200)
        # Both reservations should exist (as 'completed' after claim)
        r1 = self.conn.execute(
            "SELECT worker_id FROM manual_task_reservations "
            "WHERE task_id = ? AND worker_id = ? AND status = 'completed'",
            (task_id, 100),
        ).fetchone()
        r2 = self.conn.execute(
            "SELECT worker_id FROM manual_task_reservations "
            "WHERE task_id = ? AND worker_id = ? AND status = 'completed'",
            (task_id, 200),
        ).fetchone()
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)


class TestReservationReservationHelpers(unittest.TestCase):
    """Test create_manual_task_reservation directly for slot-level behavior."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)
        _create_user(self.conn, 200)
        _create_user(self.conn, 300)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_two_workers_can_reserve_same_task(self):
        """Two workers can each hold an active reservation for the same task."""
        task_id = _create_task(self.conn, quantity_remaining=5)
        r1 = self.mod.create_manual_task_reservation(task_id, 100, 15)
        self.assertIsNotNone(r1)
        r2 = self.mod.create_manual_task_reservation(task_id, 200, 15)
        self.assertIsNotNone(r2)
        self.assertNotEqual(r1["id"], r2["id"])
        self.assertEqual(_total_reservations(self.conn, task_id, "active"), 2)

    def test_slot_limit_enforced(self):
        """Cannot exceed quantity_remaining active reservations."""
        task_id = _create_task(self.conn, quantity_remaining=2)
        self.assertIsNotNone(self.mod.create_manual_task_reservation(task_id, 100, 15))
        self.assertIsNotNone(self.mod.create_manual_task_reservation(task_id, 200, 15))
        # Third worker rejected
        self.assertIsNone(self.mod.create_manual_task_reservation(task_id, 300, 15))

    def test_same_worker_returns_existing(self):
        """Same worker calling again returns existing reservation."""
        task_id = _create_task(self.conn, quantity_remaining=5)
        r1 = self.mod.create_manual_task_reservation(task_id, 100, 15)
        r2 = self.mod.create_manual_task_reservation(task_id, 100, 15)
        self.assertEqual(r1["id"], r2["id"])
        self.assertEqual(r1["expires_at"], r2["expires_at"])

    def test_expired_slot_freed_for_new_worker(self):
        """After A's reservation expires, B can reserve the same slot."""
        task_id = _create_task(self.conn, quantity_remaining=1)
        r1 = self.mod.create_manual_task_reservation(task_id, 100, 5)
        self.assertIsNotNone(r1)
        # Expire A's reservation
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired', "
            "expires_at = datetime('now', '-1 hour') "
            "WHERE task_id = ? AND worker_id = ?",
            (task_id, 100),
        )
        self.conn.commit()
        # B can now reserve
        r2 = self.mod.create_manual_task_reservation(task_id, 200, 5)
        self.assertIsNotNone(r2)

    def test_no_timer_allows_unlimited_workers(self):
        """Tasks with no reservation_minutes allow unlimited concurrent claims."""
        task_id = _create_task(self.conn, quantity_remaining=3, reservation_minutes=None)
        # No reservation needed — claim works directly
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "claimed")
        self.assertEqual(self.mod.claim_manual_task(task_id, 200), "claimed")
        self.assertEqual(_total_reservations(self.conn, task_id, "active"), 0)


class TestLateCompletion(unittest.TestCase):
    """F: After reservation expires, a fresh claim creates a new reservation."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_late_claim_creates_fresh_reservation(self):
        """Reservation expired → new claim creates fresh reservation and succeeds."""
        task_id = _create_task(self.conn, quantity_remaining=5, reservation_minutes=5)
        # Reserve (via helper, not claim — claim would complete)
        self.mod.create_manual_task_reservation(task_id, 100, 5)
        # Expire
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired', "
            "expires_at = datetime('now', '-1 hour') "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        )
        self.conn.commit()
        # New claim creates fresh reservation and completes
        result = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(result, "claimed")
        # Quantity decremented
        task = self.conn.execute(
            "SELECT quantity_remaining FROM manual_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        self.assertEqual(task["quantity_remaining"], 4)


class TestRepeatPolicy(unittest.TestCase):
    """H: Repeat policy still works."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_repeatable_with_reservation(self):
        task_id = _create_task(
            self.conn, repeat_policy="repeatable", repeat_hours=1,
            reservation_minutes=10, quantity_remaining=5,
        )
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "claimed")
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "repeat_cooldown")

    def test_one_time_with_reservation(self):
        task_id = _create_task(
            self.conn, repeat_policy="one_time",
            reservation_minutes=10, quantity_remaining=5,
        )
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "claimed")
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "already_done")


if __name__ == "__main__":
    unittest.main()
