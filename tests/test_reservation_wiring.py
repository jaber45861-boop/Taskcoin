"""
Tests for reservation wiring in claim/confirm_manual_task().
Verifies that reservations are created, reused, exclusive, and expired correctly.
Updated: claim reserves only, confirm completes.
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
    """Create temp database with full manual task schema."""
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
    # Patch is_subscribed to always return True for tests
    mod.is_subscribed = lambda user_id, channel: True

    return conn, tmpdir


def _create_task(conn, **overrides):
    """Insert a minimal active manual task."""
    defaults = dict(
        title="Test task",
        task_link="https://t.me/test",
        task_type="telegram_channel",
        target_reference="@test",
        reward_usd_nano=1_000_000,
        quantity_remaining=5,
        status="active",
        repeat_policy="one_time",
        reservation_minutes=15,
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


class TestReservationWiring(unittest.TestCase):
    """Test that reservation is properly wired into claim/confirm."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)
        _create_user(self.conn, 200)
        self.task_id = _create_task(self.conn)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _get_reservation(self, task_id, status='active'):
        row = self.conn.execute(
            "SELECT * FROM manual_task_reservations WHERE task_id = ? AND status = ?",
            (task_id, status),
        ).fetchone()
        return dict(row) if row else None

    def _expire_reservation(self, task_id, worker_id):
        """Manually expire a reservation for testing."""
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired', "
            "expires_at = datetime('now', '-1 hour') "
            "WHERE task_id = ? AND worker_id = ? AND status = 'active'",
            (task_id, worker_id),
        )
        self.conn.commit()

    def test_reservation_created_on_claim(self):
        """Worker reserves → active reservation created."""
        result = self.mod.claim_manual_task(self.task_id, 100)
        self.assertEqual(result, "reserved")
        rsv = self._get_reservation(self.task_id, 'active')
        self.assertIsNotNone(rsv)
        self.assertEqual(rsv["worker_id"], 100)

    def test_same_worker_no_refresh(self):
        """Same worker clicks again before expiry → expires_at unchanged."""
        result1 = self.mod.claim_manual_task(self.task_id, 100)
        self.assertEqual(result1, "reserved")
        rsv1 = self._get_reservation(self.task_id, 'active')
        self.assertIsNotNone(rsv1)
        expires1 = rsv1["expires_at"]
        # Same worker reserves again → returns existing
        result2 = self.mod.claim_manual_task(self.task_id, 100)
        self.assertEqual(result2, "reserved")
        rsv2 = self._get_reservation(self.task_id, 'active')
        self.assertIsNotNone(rsv2)
        self.assertEqual(rsv2["expires_at"], expires1)

    def test_different_worker_can_claim_with_slot_level(self):
        """Slot-level: different worker CAN reserve if slots available."""
        self.mod.claim_manual_task(self.task_id, 100)
        result = self.mod.claim_manual_task(self.task_id, 200)
        self.assertEqual(result, "reserved")

    def test_expired_reservation_allows_new_worker(self):
        """Reservation expires → worker can reserve again if task available."""
        self.mod.claim_manual_task(self.task_id, 100)
        self._expire_reservation(self.task_id, 100)
        # Worker 200 can now reserve
        result = self.mod.claim_manual_task(self.task_id, 200)
        self.assertEqual(result, "reserved")

    def test_reservation_expired_no_reward(self):
        """Expired reservation doesn't allow completion/reward."""
        task_id = _create_task(self.conn, repeat_policy="repeatable", repeat_hours=1)
        self.mod.claim_manual_task(task_id, 100)
        self._expire_reservation(task_id, 100)
        balance_before = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        # Worker 100 tries to confirm but reservation expired
        result = self.mod.confirm_manual_task(task_id, 100)
        self.assertEqual(result, "reservation_expired")
        balance_after = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.assertEqual(balance_before, balance_after)

    def test_completion_ends_reservation(self):
        """Successful confirm marks reservation as completed."""
        result = self.mod.claim_manual_task(self.task_id, 100)
        self.assertEqual(result, "reserved")
        confirm_result = self.mod.confirm_manual_task(self.task_id, 100)
        self.assertEqual(confirm_result, "completed")
        rsv = self._get_reservation(self.task_id, 'completed')
        self.assertIsNotNone(rsv)
        self.assertEqual(rsv["status"], "completed")
        self.assertEqual(rsv["worker_id"], 100)

    def test_completion_during_reservation(self):
        """Confirm during reservation works and credits reward."""
        balance_before = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.mod.claim_manual_task(self.task_id, 100)
        result = self.mod.confirm_manual_task(self.task_id, 100)
        self.assertEqual(result, "completed")
        balance_after = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.assertGreater(balance_after, balance_before)

    def test_expiry_no_repeat_cooldown(self):
        """Reservation expiry alone doesn't start repeat cooldown."""
        task_id = _create_task(self.conn, repeat_policy="repeatable", repeat_hours=1)
        self.mod.claim_manual_task(task_id, 100)
        self._expire_reservation(task_id, 100)
        completions = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM task_completions "
            "WHERE user_id = 100 AND task_key = ?",
            (f"manual_task:{task_id}",),
        ).fetchone()
        self.assertEqual(completions["cnt"], 0)

    def test_repeat_policy_after_completion(self):
        """Repeat policy works after successful confirm."""
        task_id = _create_task(self.conn, repeat_policy="repeatable", repeat_hours=1)
        self.mod.claim_manual_task(task_id, 100)
        result1 = self.mod.confirm_manual_task(task_id, 100)
        self.assertEqual(result1, "completed")
        # Second claim blocked by cooldown
        result2 = self.mod.claim_manual_task(task_id, 100)
        self.assertEqual(result2, "repeat_cooldown")

    def test_no_double_reward(self):
        """No double reward or double completion."""
        balance_before = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.mod.claim_manual_task(self.task_id, 100)
        self.mod.confirm_manual_task(self.task_id, 100)
        balance_after1 = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        # Second confirm → reservation is completed, no active reservation
        result2 = self.mod.confirm_manual_task(self.task_id, 100)
        self.assertEqual(result2, "reservation_expired")
        balance_after2 = self.conn.execute(
            "SELECT balance_usd_nano FROM users WHERE user_id = 100"
        ).fetchone()[0]
        self.assertEqual(balance_after1, balance_after2)


class TestReservationHelpers(unittest.TestCase):
    """Test reservation helper functions directly."""

    def setUp(self):
        self.mod = _load_bot()
        self.conn, self.tmpdir = _setup_db(self.mod)
        _create_user(self.conn, 100)
        _create_user(self.conn, 200)
        self.task_id = _create_task(self.conn)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_reservation(self):
        rsv = self.mod.create_manual_task_reservation(self.task_id, 100)
        self.assertIsNotNone(rsv)
        self.assertEqual(rsv["worker_id"], 100)
        self.assertEqual(rsv["status"], "active")

    def test_same_worker_returns_existing(self):
        """Same worker calling again should NOT refresh expires_at."""
        rsv1 = self.mod.create_manual_task_reservation(self.task_id, 100)
        rsv2 = self.mod.create_manual_task_reservation(self.task_id, 100)
        self.assertIsNotNone(rsv2)
        self.assertEqual(rsv1["expires_at"], rsv2["expires_at"])

    def test_different_worker_can_reserve(self):
        """Slot-level: different worker CAN reserve if slots available."""
        self.mod.create_manual_task_reservation(self.task_id, 100)
        rsv = self.mod.create_manual_task_reservation(self.task_id, 200)
        self.assertIsNotNone(rsv)
        self.assertEqual(rsv["worker_id"], 200)

    def test_get_active_reservation(self):
        self.mod.create_manual_task_reservation(self.task_id, 100)
        rsv = self.mod.get_active_manual_task_reservation(self.task_id)
        self.assertIsNotNone(rsv)
        self.assertEqual(rsv["worker_id"], 100)

    def test_get_active_after_expire(self):
        self.mod.create_manual_task_reservation(self.task_id, 100)
        self.conn.execute(
            "UPDATE manual_task_reservations SET status = 'expired' "
            "WHERE task_id = ? AND worker_id = ?",
            (self.task_id, 100),
        )
        self.conn.commit()
        rsv = self.mod.get_active_manual_task_reservation(self.task_id)
        self.assertIsNone(rsv)


if __name__ == "__main__":
    unittest.main()
