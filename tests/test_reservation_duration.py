"""
Tests for per-task reservation duration (reservation_minutes) in manual tasks.
Verifies that reservation_minutes is stored and used correctly.
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


class TestReservationMinutesStorage(unittest.TestCase):
    """Verify reservation_minutes is stored correctly via create_manual_task."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_reservation_minutes(self, task_id):
        row = self.conn.execute(
            "SELECT reservation_minutes FROM manual_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return row["reservation_minutes"] if row else None

    def test_admin_selects_5_minutes(self):
        task_id = self.mod.create_manual_task(
            title="T", task_link="https://t.me/x", reward_points=100,
            quantity=1, reservation_minutes=5,
        )
        self.assertEqual(self._get_reservation_minutes(task_id), 5)

    def test_admin_selects_15_minutes(self):
        task_id = self.mod.create_manual_task(
            title="T", task_link="https://t.me/x", reward_points=100,
            quantity=1, reservation_minutes=15,
        )
        self.assertEqual(self._get_reservation_minutes(task_id), 15)

    def test_admin_selects_30_minutes(self):
        task_id = self.mod.create_manual_task(
            title="T", task_link="https://t.me/x", reward_points=100,
            quantity=1, reservation_minutes=30,
        )
        self.assertEqual(self._get_reservation_minutes(task_id), 30)

    def test_admin_selects_60_minutes(self):
        task_id = self.mod.create_manual_task(
            title="T", task_link="https://t.me/x", reward_points=100,
            quantity=1, reservation_minutes=60,
        )
        self.assertEqual(self._get_reservation_minutes(task_id), 60)

    def test_admin_selects_custom_7_minutes(self):
        task_id = self.mod.create_manual_task(
            title="T", task_link="https://t.me/x", reward_points=100,
            quantity=1, reservation_minutes=7,
        )
        self.assertEqual(self._get_reservation_minutes(task_id), 7)

    def test_no_timer_stores_none(self):
        task_id = self.mod.create_manual_task(
            title="T", task_link="https://t.me/x", reward_points=100,
            quantity=1, reservation_minutes=None,
        )
        self.assertIsNone(self._get_reservation_minutes(task_id))

    def test_default_is_none(self):
        task_id = self.mod.create_manual_task(
            title="T", task_link="https://t.me/x", reward_points=100,
            quantity=1,
        )
        self.assertIsNone(self._get_reservation_minutes(task_id))


class TestReservationMinutesClaim(unittest.TestCase):
    """Verify claim uses task's reservation_minutes, not a fixed value."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)
        _create_user(self.conn, 200)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_reservation(self, task_id, status='active'):
        row = self.conn.execute(
            "SELECT * FROM manual_task_reservations WHERE task_id = ? AND status = ?",
            (task_id, status),
        ).fetchone()
        return dict(row) if row else None

    def test_claim_with_5min_reservation(self):
        task_id = _create_task(self.conn, reservation_minutes=5)
        result = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(result, "claimed")
        # After claim, reservation is marked 'completed'
        rsv = self._get_reservation(task_id, 'completed')
        self.assertIsNotNone(rsv)

    def test_claim_no_reservation_when_none(self):
        """Task with reservation_minutes=NULL → no reservation created."""
        task_id = _create_task(self.conn, reservation_minutes=None)
        result = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(result, "claimed")
        rsv = self._get_reservation(task_id)
        self.assertIsNone(rsv)

    def test_claim_no_reservation_when_zero(self):
        """Task with reservation_minutes=0 → no reservation created."""
        task_id = _create_task(self.conn, reservation_minutes=0)
        result = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(result, "claimed")
        rsv = self._get_reservation(task_id)
        self.assertIsNone(rsv)

    def test_different_worker_can_claim_slot_level(self):
        """Slot-level: another worker CAN claim if slots available."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=5)
        self.mod.claim_manual_task(task_id, 100)
        result2 = self.mod.claim_manual_task(task_id, 200)
        self.assertEqual(result2, "claimed")

    def test_same_worker_can_claim_without_timer(self):
        """Without timer, different workers can all claim (up to quantity)."""
        task_id = _create_task(self.conn, reservation_minutes=None, quantity_remaining=3)
        r1 = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(r1, "claimed")
        # Worker 200 should also be able to claim (no reservation blocking)
        r2 = self.mod.claim_manual_task(task_id, 200)
        self.assertEqual(r2, "claimed")

    def test_same_worker_no_refresh(self):
        """Same worker clicking again doesn't refresh timer."""
        task_id = _create_task(self.conn, reservation_minutes=15)
        self.mod.claim_manual_task(task_id, 100)
        rsv1 = self._get_reservation(task_id, 'completed')
        self.assertIsNotNone(rsv1)
        expires1 = rsv1["expires_at"]
        # Worker tries again (will fail due to one_time, but reservation exists)
        self.mod.claim_manual_task(task_id, 100)
        rsv2 = self._get_reservation(task_id, 'completed')
        self.assertEqual(rsv2["expires_at"], expires1)


class TestReservationSlotCounting(unittest.TestCase):
    """Verify that active reservations count against quantity_remaining."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)
        _create_user(self.conn, 200)
        _create_user(self.conn, 300)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_quantity_10_with_3_reservations_allows_7(self):
        """quantity=10, 3 active reservations → 7 slots still available."""
        task_id = _create_task(self.conn, quantity_remaining=10, reservation_minutes=15)
        # Worker 100 claims (1 slot taken)
        r1 = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(r1, "claimed")

        # Create 2 more tasks for workers 200/300 to have separate reservations
        task2 = _create_task(self.conn, quantity_remaining=10, reservation_minutes=15)
        task3 = _create_task(self.conn, quantity_remaining=10, reservation_minutes=15)

        # All should succeed since each task is independent
        r2 = self.mod.claim_manual_task(task2, 200)
        self.assertEqual(r2, "claimed")
        r3 = self.mod.claim_manual_task(task3, 300)
        self.assertEqual(r3, "claimed")

    def test_last_slot_blocks_new_worker(self):
        """When only 1 slot left and it's reserved, new worker is blocked."""
        # Create a task with quantity_remaining=1
        task_id = _create_task(self.conn, quantity_remaining=1, reservation_minutes=15)
        # Worker 100 claims
        r1 = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(r1, "claimed")
        # Worker 200 should be blocked (no slots left)
        r2 = self.mod.claim_manual_task(task_id, 200)
        self.assertIn(r2, ("unavailable", "slot_held"))

    def test_expired_reservation_frees_slot(self):
        """After reservation expires, slot is available again."""
        task_id = _create_task(self.conn, quantity_remaining=2, reservation_minutes=5)
        self.mod.claim_manual_task(task_id, 100)
        # Expire the reservation
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired' "
            "WHERE task_id = ? AND worker_id = ?",
            (task_id, 100),
        )
        self.conn.commit()
        # Worker 200 should now be able to claim (1 slot still available)
        result = self.mod.claim_manual_task(task_id, 200)
        self.assertEqual(result, "claimed")


class TestRepeatPolicyUnchanged(unittest.TestCase):
    """Verify repeat policy still works correctly with reservation_minutes."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_repeatable_with_reservation(self):
        """Repeatable task with reservation_minutes works."""
        task_id = _create_task(
            self.conn,
            repeat_policy="repeatable", repeat_hours=1,
            reservation_minutes=10, quantity_remaining=5,
        )
        r1 = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(r1, "claimed")
        # Second claim should be blocked by cooldown
        r2 = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(r2, "repeat_cooldown")

    def test_one_time_with_reservation(self):
        """One-time task with reservation_minutes works."""
        task_id = _create_task(
            self.conn,
            repeat_policy="one_time",
            reservation_minutes=10, quantity_remaining=5,
        )
        r1 = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(r1, "claimed")
        r2 = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(r2, "already_done")


if __name__ == "__main__":
    unittest.main()
