"""
Slot-level reservation tests.
Verifies that multiple workers can reserve slots of the same task,
that slot counting works, that late completion is rejected, and that
repeat policy is preserved.

Updated: claim now only reserves, confirm completes.
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
            title TEXT NOT NULL,
            task_link TEXT,
            task_type TEXT DEFAULT 'telegram_channel',
            target_reference TEXT,
            task_instructions TEXT,
            reward_points INTEGER NOT NULL DEFAULT 0,
            reward_usd_nano INTEGER NOT NULL DEFAULT 0,
            quantity_requested INTEGER NOT NULL DEFAULT 1,
            quantity_remaining INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            repeat_policy TEXT DEFAULT 'one_time',
            repeat_hours INTEGER,
            reservation_minutes INTEGER,
            task_state TEXT DEFAULT 'AVAILABLE',
            task_origin TEXT DEFAULT 'internal',
            advertiser_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE task_completions (
            user_id INTEGER NOT NULL,
            task_key TEXT NOT NULL,
            reward_points INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'granted',
            done_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            deducted_points INTEGER DEFAULT 0,
            granted_at DATETIME,
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
    """A: Multiple workers can reserve+complete the same task (slot-level)."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        for uid in (100, 200, 300, 400, 500):
            _create_user(self.conn, uid)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_quantity_10_three_workers_complete(self):
        """quantity=10: A, B, C each reserve+confirm → quantity=7."""
        task_id = _create_task(self.conn, quantity_remaining=10, reservation_minutes=15)
        # Reserve
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "reserved")
        self.assertEqual(self.mod.claim_manual_task(task_id, 200), "reserved")
        self.assertEqual(self.mod.claim_manual_task(task_id, 300), "reserved")
        # Confirm each
        self.assertEqual(self.mod.confirm_manual_task(task_id, 100), "completed")
        self.assertEqual(self.mod.confirm_manual_task(task_id, 200), "completed")
        self.assertEqual(self.mod.confirm_manual_task(task_id, 300), "completed")
        task = self.conn.execute(
            "SELECT quantity_remaining FROM manual_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        self.assertEqual(task["quantity_remaining"], 7)

    def test_quantity_3_three_workers_fourth_rejected(self):
        """quantity=3: A, B, C reserve; D rejected (no slots left)."""
        task_id = _create_task(self.conn, quantity_remaining=3, reservation_minutes=15)
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "reserved")
        self.assertEqual(self.mod.claim_manual_task(task_id, 200), "reserved")
        self.assertEqual(self.mod.claim_manual_task(task_id, 300), "reserved")
        result = self.mod.claim_manual_task(task_id, 400)
        self.assertIn(result, ("slot_held", "unavailable"))

    def test_same_worker_no_double_claim(self):
        """Same worker cannot claim twice (one_time task)."""
        task_id = _create_task(self.conn, quantity_remaining=5, reservation_minutes=15)
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "reserved")
        # Confirm the task
        self.assertEqual(self.mod.confirm_manual_task(task_id, 100), "completed")
        # Same worker tries again → already_done (one_time completed)
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "already_done")

    def test_reservation_created_per_worker(self):
        """Each worker gets their own reservation row."""
        task_id = _create_task(self.conn, quantity_remaining=5, reservation_minutes=15)
        self.mod.claim_manual_task(task_id, 100)
        self.mod.claim_manual_task(task_id, 200)
        # Both reservations should exist as 'active' (not confirmed yet)
        r1 = self.conn.execute(
            "SELECT worker_id FROM manual_task_reservations "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        ).fetchone()
        r2 = self.conn.execute(
            "SELECT worker_id FROM manual_task_reservations "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
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
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired', "
            "expires_at = datetime('now', '-1 hour') "
            "WHERE task_id = ? AND worker_id = ?",
            (task_id, 100),
        )
        self.conn.commit()
        r2 = self.mod.create_manual_task_reservation(task_id, 200, 5)
        self.assertIsNotNone(r2)

    def test_no_timer_allows_unlimited_workers(self):
        """Tasks with no reservation_minutes allow unlimited concurrent claims."""
        task_id = _create_task(self.conn, quantity_remaining=3, reservation_minutes=None)
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
        """Reservation expired → new claim creates fresh reservation (reserved)."""
        task_id = _create_task(self.conn, quantity_remaining=5, reservation_minutes=5)
        self.mod.create_manual_task_reservation(task_id, 100, 5)
        # Expire
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired', "
            "expires_at = datetime('now', '-1 hour') "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        )
        self.conn.commit()
        # New claim creates fresh reservation
        result = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(result, "reserved")
        # Confirm it
        self.assertEqual(self.mod.confirm_manual_task(task_id, 100), "completed")
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
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "reserved")
        self.assertEqual(self.mod.confirm_manual_task(task_id, 100), "completed")
        # Second claim blocked by repeat_cooldown
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "repeat_cooldown")

    def test_one_time_with_reservation(self):
        task_id = _create_task(
            self.conn, repeat_policy="one_time",
            reservation_minutes=10, quantity_remaining=5,
        )
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "reserved")
        self.assertEqual(self.mod.confirm_manual_task(task_id, 100), "completed")
        # Second claim blocked by already_done
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "already_done")


class TestReservationSeparation(unittest.TestCase):
    """Separation: claim reserves, confirm completes."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        for uid in (100, 200):
            _create_user(self.conn, uid)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_execute_returns_reserved(self):
        """claim_manual_task returns 'reserved' when task has reservation_minutes."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=5)
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "reserved")

    def test_execute_no_reward(self):
        """Execute does NOT give reward."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=5)
        balance_before = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.mod.claim_manual_task(task_id, 100)
        balance_after = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.assertEqual(balance_before, balance_after)

    def test_execute_no_quantity_decrement(self):
        """Execute does NOT decrement quantity."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=5)
        self.mod.claim_manual_task(task_id, 100)
        task = self.conn.execute(
            "SELECT quantity_remaining FROM manual_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        self.assertEqual(task["quantity_remaining"], 5)

    def test_execute_no_task_completion(self):
        """Execute does NOT create task_completion."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=5)
        self.mod.claim_manual_task(task_id, 100)
        count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM task_completions "
            "WHERE task_key = ?", (f"manual_task:{task_id}",)
        ).fetchone()["cnt"]
        self.assertEqual(count, 0)

    def test_execute_creates_active_reservation(self):
        """Execute creates an active reservation."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=5)
        self.mod.claim_manual_task(task_id, 100)
        rsv = self.conn.execute(
            "SELECT * FROM manual_task_reservations "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        ).fetchone()
        self.assertIsNotNone(rsv)

    def test_confirm_completes_task(self):
        """confirm_manual_task completes the task and credits reward."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=5)
        self.mod.claim_manual_task(task_id, 100)
        balance_before = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        result = self.mod.confirm_manual_task(task_id, 100)
        self.assertEqual(result, "completed")
        balance_after = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.assertGreater(balance_after, balance_before)
        # Quantity decremented
        task = self.conn.execute(
            "SELECT quantity_remaining FROM manual_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        self.assertEqual(task["quantity_remaining"], 4)
        # Reservation marked completed
        rsv = self.conn.execute(
            "SELECT * FROM manual_task_reservations "
            "WHERE task_id = ? AND worker_id = ? AND status = 'completed'",
            (task_id, 100),
        ).fetchone()
        self.assertIsNotNone(rsv)

    def test_expired_reservation_rejects_confirm(self):
        """Confirm after expiry → rejected, no reward."""
        task_id = _create_task(self.conn, reservation_minutes=5, quantity_remaining=5)
        self.mod.claim_manual_task(task_id, 100)
        # Expire reservation
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired', "
            "expires_at = datetime('now', '-1 hour') "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        )
        self.conn.commit()
        balance_before = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        result = self.mod.confirm_manual_task(task_id, 100)
        self.assertEqual(result, "reservation_expired")
        balance_after = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.assertEqual(balance_before, balance_after)

    def test_expiry_no_repeat_cooldown(self):
        """Expiry does NOT start repeat cooldown."""
        task_id = _create_task(
            self.conn, repeat_policy="repeatable", repeat_hours=1,
            reservation_minutes=15, quantity_remaining=5,
        )
        self.mod.claim_manual_task(task_id, 100)
        # Expire reservation
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired', "
            "expires_at = datetime('now', '-1 hour') "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        )
        self.conn.commit()
        # No completion exists
        count = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM task_completions "
            "WHERE task_key = ?", (f"manual_task:{task_id}",)
        ).fetchone()["cnt"]
        self.assertEqual(count, 0)

    def test_other_worker_can_reserve_after_expiry(self):
        """After A's reservation expires, B can reserve."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=1)
        self.mod.claim_manual_task(task_id, 100)
        # Expire A's reservation
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired', "
            "expires_at = datetime('now', '-1 hour') "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        )
        self.conn.commit()
        # B can now reserve
        self.assertEqual(self.mod.claim_manual_task(task_id, 200), "reserved")

    def test_same_worker_no_refresh(self):
        """Same worker pressing again doesn't refresh reservation."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=5)
        self.mod.claim_manual_task(task_id, 100)
        rsv1 = self.conn.execute(
            "SELECT expires_at FROM manual_task_reservations "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        ).fetchone()
        # Same worker reserves again → returns existing
        self.assertEqual(self.mod.claim_manual_task(task_id, 100), "reserved")
        rsv2 = self.conn.execute(
            "SELECT expires_at FROM manual_task_reservations "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, 100),
        ).fetchone()
        self.assertEqual(rsv1["expires_at"], rsv2["expires_at"])


class TestExpiredReservationPriorityOverQuantity(unittest.TestCase):
    """Regression: expired reservation must return 'reservation_expired'
    even when quantity_remaining == 0, not 'unavailable'.
    The reservation check must come before the quantity/status check."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_expired_rsv_overrides_zero_quantity(self):
        """Worker with expired reservation on task with quantity=0
        gets 'reservation_expired', not 'unavailable'."""
        task_id = _create_task(self.conn, reservation_minutes=5, quantity_remaining=0)
        # Manually create an expired reservation for worker 100
        self.conn.execute(
            "INSERT INTO manual_task_reservations "
            "(task_id, worker_id, reserved_at, expires_at, status) "
            "VALUES (?, ?, datetime('now', '-10 minutes'), "
            "datetime('now', '-5 minutes'), 'expired')",
            (task_id, 100),
        )
        self.conn.commit()
        result = self.mod.confirm_manual_task(task_id, 100)
        self.assertEqual(result, "reservation_expired")

    def test_expired_rsv_overrides_inactive_status(self):
        """Worker with expired reservation on inactive task
        gets 'reservation_expired', not 'unavailable'."""
        task_id = _create_task(
            self.conn, reservation_minutes=5, quantity_remaining=5, status="inactive"
        )
        self.conn.execute(
            "INSERT INTO manual_task_reservations "
            "(task_id, worker_id, reserved_at, expires_at, status) "
            "VALUES (?, ?, datetime('now', '-10 minutes'), "
            "datetime('now', '-5 minutes'), 'expired')",
            (task_id, 100),
        )
        self.conn.commit()
        result = self.mod.confirm_manual_task(task_id, 100)
        self.assertEqual(result, "reservation_expired")

    def test_active_rsv_still_checks_quantity(self):
        """Worker with active reservation on task with quantity=0
        gets 'unavailable' (quantity check still applies when reservation is active)."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=0)
        self.mod.claim_manual_task(task_id, 100)
        result = self.mod.confirm_manual_task(task_id, 100)
        self.assertEqual(result, "unavailable")

    def test_no_reservation_quantity_zero_returns_unavailable(self):
        """Worker with no reservation on task with quantity=0
        gets 'unavailable' (existing behavior preserved)."""
        task_id = _create_task(self.conn, reservation_minutes=15, quantity_remaining=0)
        result = self.mod.confirm_manual_task(task_id, 100)
        self.assertEqual(result, "unavailable")


if __name__ == "__main__":
    unittest.main()
