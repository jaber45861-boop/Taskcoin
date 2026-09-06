"""Tests for admin-internal task expiration timer.

All test classes share one DB (module-level DB_PATH).
"""
import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import importlib.util

_BOT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ganaihat_bot.py")
)
_spec = importlib.util.spec_from_file_location(
    "ganaihat_bot", _BOT_FILE, submodule_search_locations=[]
)
_mod = importlib.util.module_from_spec(_spec)
_mod.EGP_PER_USD = Decimal("50")
_spec.loader.exec_module(_mod)
sys.modules["ganaihat_bot"] = _mod
gb = _mod

_fd, _TEST_DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
gb.DB_PATH = _TEST_DB
gb.init_db()

# Initialize FX rate required by egp_cents_to_wallet_nano (used by create_referral_task)
import reward_api as _ra
_ra._live_egp_per_usd = Decimal("50")


def _add_user(uid):
    with gb.get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, first_name, balance_usd_nano, activation_status) "
            "VALUES (?, 'Test', 100000000, 1)", (uid,),
        )
        conn.commit()


def _cleanup_task(table, task_id):
    try:
        with gb.get_connection() as conn:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (task_id,))
            conn.commit()
    except Exception:
        pass


class TestSchema(unittest.TestCase):
    def test_referral_tasks_has_expires_at(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(referral_tasks)").fetchall()}
        self.assertIn("expires_at", cols)

    def test_referral_tasks_has_task_state(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(referral_tasks)").fetchall()}
        self.assertIn("task_state", cols)

    def test_manual_tasks_has_expires_at(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("expires_at", cols)

    def test_manual_tasks_has_task_state(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("task_state", cols)


class TestTaskCreation(unittest.TestCase):
    def test_referral_task_sets_state_and_internal_origin(self):
        uid = 20001
        _add_user(uid)
        with gb.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO service_price_settings (service_key, price_points, price_cents) VALUES ('referral_boost', 100, 100)")
            conn.execute("UPDATE users SET balance_usd_nano = 50000000 WHERE user_id = ?", (uid,))
            conn.commit()
        tid = gb.create_referral_task(uid, "https://t.me/TestBot?start=abc")
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute("SELECT expires_at, task_state, task_origin FROM referral_tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        self.assertEqual(row["task_origin"], "internal")
        self.assertIsNone(row["expires_at"])  # Internal tasks have no timer
        _cleanup_task("referral_tasks", tid)

    def test_manual_task_sets_state_and_internal_origin(self):
        tid = gb.create_manual_task(title="Test task", task_link="https://example.com", reward_points=5000, quantity=5)
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute("SELECT expires_at, task_state, task_origin FROM manual_tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        self.assertEqual(row["task_origin"], "internal")
        self.assertIsNone(row["expires_at"])  # Internal tasks have no timer
        _cleanup_task("manual_tasks", tid)


class TestExpiryFiltering(unittest.TestCase):
    def setUp(self):
        _add_user(30001)
        _add_user(30999)

    def _insert(self, tid, state="AVAILABLE", expires_at=None, qty=5, buyer=30001):
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state, expires_at) VALUES (?, ?, 'https://t.me/X?start=1', ?, ?, 100, 100, 'active', ?, ?)",
                (tid, buyer, qty, qty, state, expires_at),
            )
            conn.commit()

    def test_expired_task_hidden(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(3001, state="AVAILABLE", expires_at=past)
        tasks = gb.get_active_referral_tasks(30999)
        self.assertNotIn(3001, [t["id"] for t in tasks])

    def test_deleted_task_hidden(self):
        self._insert(3002, state="DELETED")
        tasks = gb.get_active_referral_tasks(30999)
        self.assertNotIn(3002, [t["id"] for t in tasks])

    def test_available_task_visible(self):
        future = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(3003, state="AVAILABLE", expires_at=future)
        tasks = gb.get_active_referral_tasks(30999)
        self.assertIn(3003, [t["id"] for t in tasks])

    def test_legacy_task_without_expires_at_visible(self):
        _add_user(30998)
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status) VALUES (?, 30998, 'https://t.me/Y?start=2', 5, 5, 100, 100, 'active')", (3004,))
            conn.commit()
        tasks = gb.get_active_referral_tasks(30999)
        self.assertIn(3004, [t["id"] for t in tasks])
        _cleanup_task("referral_tasks", 3004)

    def test_manual_task_expired_hidden(self):
        tid = gb.create_manual_task("Expired manual", "https://x.com", 1000, 1)
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute("UPDATE manual_tasks SET expires_at = ? WHERE id = ?", (past, tid))
            conn.commit()
        tasks = gb.get_active_manual_tasks()
        self.assertNotIn(tid, [t["id"] for t in tasks])
        _cleanup_task("manual_tasks", tid)


class TestClaimGuard(unittest.TestCase):
    def setUp(self):
        _add_user(40001)
        _add_user(40002)

    def _insert(self, tid, state="AVAILABLE", expires_at=None, buyer=40002, qty=5):
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state, expires_at) VALUES (?, ?, 'https://t.me/Z?start=3', ?, ?, 100, 100, 'active', ?, ?)",
                (tid, buyer, qty, qty, state, expires_at),
            )
            conn.commit()

    def test_claim_expired_returns_unavailable(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(4001, state="AVAILABLE", expires_at=past)
        self.assertEqual(gb.claim_referral_task(4001, 40001), "unavailable")

    def test_claim_deleted_returns_unavailable(self):
        self._insert(4002, state="DELETED")
        self.assertEqual(gb.claim_referral_task(4002, 40001), "unavailable")

    def test_claim_available_succeeds(self):
        future = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        self._insert(4003, state="AVAILABLE", expires_at=future)
        self.assertEqual(gb.claim_referral_task(4003, 40001), "pending_client")

    def test_existing_submissions_preserved_after_expiry(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM referral_task_claims WHERE task_id = 4004")
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state, expires_at) VALUES (?, 40002, 'https://t.me/U?start=6', 5, 0, 100, 100, 'completed', 'EXPIRED', ?)", (4004, past))
            conn.execute("INSERT INTO referral_task_claims (task_id, worker_id, buyer_id, status) VALUES (?, 40001, 40002, 'pending_client')", (4004,))
            conn.commit()
        claim = gb.get_referral_task_claim_for_worker(4004, 40001)
        self.assertIsNotNone(claim)
        self.assertEqual(claim["status"], "pending_client")


class TestAdminFunctions(unittest.TestCase):
    def setUp(self):
        _add_user(50001)

    def test_extend_task_timer(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state, expires_at) VALUES (?, 50001, 'https://t.me/A?start=7', 5, 5, 100, 100, 'active', 'EXPIRED', ?)", (5001, past))
            conn.commit()
        ok = gb.extend_task_timer(5001, "referral_tasks", extra_hours=24)
        self.assertTrue(ok)
        with gb.get_connection() as conn:
            row = conn.execute("SELECT task_state FROM referral_tasks WHERE id = 5001").fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        _cleanup_task("referral_tasks", 5001)

    def test_delete_task_admin(self):
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO manual_tasks (id, title, task_link, reward_points, quantity_requested, quantity_remaining, status, task_state) VALUES (?, 'Del task', 'https://x.com', 1000, 10, 10, 'active', 'AVAILABLE')", (5002,))
            conn.commit()
        self.assertTrue(gb.delete_task_admin(5002, "manual_tasks"))
        with gb.get_connection() as conn:
            row = conn.execute("SELECT task_state FROM manual_tasks WHERE id = 5002").fetchone()
        self.assertEqual(row["task_state"], "DELETED")
        _cleanup_task("manual_tasks", 5002)

    def test_reactivate_task_admin(self):
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status, task_state) VALUES (?, 50001, 'https://t.me/B?start=8', 5, 5, 100, 100, 'active', 'EXPIRED')", (5003,))
            conn.commit()
        self.assertTrue(gb.reactivate_task_admin(5003, "referral_tasks"))
        with gb.get_connection() as conn:
            row = conn.execute("SELECT task_state FROM referral_tasks WHERE id = 5003").fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        _cleanup_task("referral_tasks", 5003)

    def test_get_all_tasks_for_admin(self):
        tasks = gb.get_all_tasks_for_admin(limit=50)
        self.assertIsInstance(tasks, list)
        for t in tasks:
            self.assertIn("task_source", t)

    def test_admin_timer_summary_no_expires(self):
        row = {"id": 1, "task_state": "AVAILABLE", "expires_at": None, "quantity_remaining": 3}
        summary = gb._admin_task_timer_summary(row, "referral_tasks")
        self.assertIn("AVAILABLE", summary)
        self.assertIn("Remaining: 3", summary)


class TestTimerConstant(unittest.TestCase):
    def test_task_expiry_hours_exists(self):
        self.assertEqual(gb.TASK_EXPIRY_HOURS, 24)

    def test_no_user_facing_timer_leak(self):
        import inspect
        src = inspect.getsource(gb.build_tasks_text)
        for word in ["expires_at", "deadline", "countdown", "timer"]:
            self.assertNotIn(word.lower(), src.lower(), f"build_tasks_text leaks '{word}'")


class TestBackwardCompatibility(unittest.TestCase):
    def test_legacy_referral_task_without_timer_is_available(self):
        _add_user(60001)
        _add_user(60002)
        with gb.get_connection() as conn:
            conn.execute("INSERT INTO referral_tasks (id, buyer_id, referral_link, quantity_requested, quantity_remaining, points_spent, amount_cents, status) VALUES (?, 60001, 'https://t.me/Legacy?start=9', 5, 3, 100, 100, 'active')", (6001,))
            conn.commit()
        tasks = gb.get_active_referral_tasks(60002)
        self.assertIn(6001, [t["id"] for t in tasks])
        _cleanup_task("referral_tasks", 6001)


# ══════════════════════════════════════════════════════════════════════════════
# Micro-Order 5: Internal vs External task_origin timer scope
# ══════════════════════════════════════════════════════════════════════════════


class TestTaskOriginColumn(unittest.TestCase):
    """Verify the task_origin column exists on both tables."""

    def test_referral_tasks_has_task_origin(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(referral_tasks)").fetchall()}
        self.assertIn("task_origin", cols)

    def test_manual_tasks_has_task_origin(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("task_origin", cols)


class TestInternalTaskCreationNoTimer(unittest.TestCase):
    """Internal tasks must be created with task_origin='internal' and expires_at=NULL."""

    def setUp(self):
        _add_user(70001)
        # Ensure sufficient balance for referral cost (650 EGP cents = 130M nano)
        with gb.get_connection() as conn:
            conn.execute("UPDATE users SET balance_usd_nano = 1000000000 WHERE user_id = 70001")
            conn.commit()

    def test_create_referral_task_internal_no_timer(self):
        """Referral task: task_origin='internal', expires_at is NULL."""
        tid = gb.create_referral_task(70001, "https://t.me/TestBot?start=abc")
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT task_origin, expires_at, task_state FROM referral_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        self.assertEqual(row["task_origin"], "internal")
        self.assertIsNone(row["expires_at"])
        self.assertEqual(row["task_state"], "AVAILABLE")
        _cleanup_task("referral_tasks", tid)

    def test_create_manual_task_internal_no_timer(self):
        """Manual task: task_origin='internal', expires_at is NULL."""
        tid = gb.create_manual_task(
            title="Internal task", task_link="https://example.com",
            reward_points=5000, quantity=5,
        )
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT task_origin, expires_at, task_state FROM manual_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        self.assertEqual(row["task_origin"], "internal")
        self.assertIsNone(row["expires_at"])
        self.assertEqual(row["task_state"], "AVAILABLE")
        _cleanup_task("manual_tasks", tid)


class TestInternalTaskRemainsAvailable(unittest.TestCase):
    """Internal tasks must remain claimable even after >24h simulated passage."""

    def setUp(self):
        _add_user(71001)
        _add_user(71002)
        with gb.get_connection() as conn:
            conn.execute("UPDATE users SET balance_usd_nano = 1000000000 WHERE user_id = 71001")
            conn.commit()

    def test_referral_task_visible_after_48h(self):
        tid = gb.create_referral_task(71001, "https://t.me/TBot?start=deep")
        self.assertIsNotNone(tid)
        # Simulate 48h passage by backdating created_at (expires_at remains NULL)
        with gb.get_connection() as conn:
            conn.execute(
                "UPDATE referral_tasks SET created_at = datetime('now', '-48 hours') WHERE id = ?",
                (tid,),
            )
            conn.commit()
        tasks = gb.get_active_referral_tasks(71002)
        self.assertIn(tid, [t["id"] for t in tasks])
        _cleanup_task("referral_tasks", tid)

    def test_manual_task_visible_after_48h(self):
        tid = gb.create_manual_task(
            title="Long task", task_link="https://example.com",
            reward_points=5000, quantity=5,
        )
        # Simulate 48h passage
        with gb.get_connection() as conn:
            conn.execute(
                "UPDATE manual_tasks SET created_at = datetime('now', '-48 hours') WHERE id = ?",
                (tid,),
            )
            conn.commit()
        tasks = gb.get_active_manual_tasks()
        self.assertIn(tid, [t["id"] for t in tasks])
        _cleanup_task("manual_tasks", tid)


class TestExternalTaskWithTimer(unittest.TestCase):
    """External tasks with explicit expires_at must be blocked after expiry."""

    def setUp(self):
        _add_user(72001)
        _add_user(72002)

    def test_expired_external_referral_task_blocked(self):
        """An external referral task with past expires_at is not claimable."""
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO referral_tasks "
                "(buyer_id, referral_link, quantity_requested, quantity_remaining, "
                "points_spent, amount_cents, status, task_state, task_origin, expires_at) "
                "VALUES (?, 'https://t.me/X?start=ext', 5, 5, 100, 100, 'active', 'AVAILABLE', 'external', ?)",
                (72001, past),
            )
            conn.commit()
        # Find the task id
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM referral_tasks WHERE buyer_id = 72001 AND task_origin = 'external'"
            ).fetchone()
        tid = row["id"]
        self.assertEqual(gb.claim_referral_task(tid, 72002), "unavailable")
        _cleanup_task("referral_tasks", tid)

    def test_future_external_referral_task_claimable(self):
        """An external referral task with future expires_at is claimable."""
        future = (datetime.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO referral_tasks "
                "(buyer_id, referral_link, quantity_requested, quantity_remaining, "
                "points_spent, amount_cents, status, task_state, task_origin, expires_at) "
                "VALUES (?, 'https://t.me/Y?start=ext2', 5, 5, 100, 100, 'active', 'AVAILABLE', 'external', ?)",
                (72001, future),
            )
            conn.commit()
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM referral_tasks WHERE buyer_id = 72001 AND task_origin = 'external'"
            ).fetchone()
        tid = row["id"]
        self.assertEqual(gb.claim_referral_task(tid, 72002), "pending_client")
        _cleanup_task("referral_tasks", tid)

    def test_expired_external_manual_task_hidden(self):
        """An external manual task with past expires_at is not listed."""
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        tid = gb.create_manual_task.__wrapped__(
            title="External manual", task_link="https://ext.com",
            reward_points=1000, quantity=3,
        ) if hasattr(gb.create_manual_task, '__wrapped__') else None
        # Manually insert with external origin and past expires
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO manual_tasks "
                "(title, task_link, task_type, target_reference, task_instructions, "
                "reward_points, quantity_requested, quantity_remaining, "
                "status, task_state, task_origin, expires_at) "
                "VALUES ('Ext manual', 'https://ext.com', 'social_manual', 'https://ext.com', '', "
                "1000, 3, 3, 'active', 'AVAILABLE', 'external', ?)",
                (past,),
            )
            conn.commit()
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM manual_tasks WHERE task_origin = 'external' AND expires_at = ?",
                (past,),
            ).fetchone()
        tid = row["id"]
        tasks = gb.get_active_manual_tasks()
        self.assertNotIn(tid, [t["id"] for t in tasks])
        _cleanup_task("manual_tasks", tid)


class TestExistingSubmissionsPreservedAfterExternalExpiry(unittest.TestCase):
    """Pending submissions on an external task remain reviewable after expiry."""

    def setUp(self):
        _add_user(73001)
        _add_user(73002)

    def test_claim_survives_external_task_expiry(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM referral_task_claims WHERE task_id = 7301")
            conn.execute(
                "INSERT INTO referral_tasks "
                "(id, buyer_id, referral_link, quantity_requested, quantity_remaining, "
                "points_spent, amount_cents, status, task_state, task_origin, expires_at) "
                "VALUES (7301, ?, 'https://t.me/Z?start=pres', 5, 0, 100, 100, 'completed', 'EXPIRED', 'external', ?)",
                (73002, past),
            )
            conn.execute(
                "INSERT INTO referral_task_claims (task_id, worker_id, buyer_id, status) "
                "VALUES (7301, ?, ?, 'pending_client')",
                (73001, 73002),
            )
            conn.commit()
        claim = gb.get_referral_task_claim_for_worker(7301, 73001)
        self.assertIsNotNone(claim)
        self.assertEqual(claim["status"], "pending_client")
        _cleanup_task("referral_tasks", 7301)


class TestAdminTimerOnExternalTask(unittest.TestCase):
    """Admin extend/reactivate works on explicitly timed external tasks."""

    def setUp(self):
        _add_user(74001)

    def test_extend_external_task(self):
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO referral_tasks "
                "(id, buyer_id, referral_link, quantity_requested, quantity_remaining, "
                "points_spent, amount_cents, status, task_state, task_origin, expires_at) "
                "VALUES (7401, ?, 'https://t.me/A?start=ext', 5, 5, 100, 100, 'active', 'AVAILABLE', 'external', ?)",
                (74001, past),
            )
            conn.commit()
        ok = gb.extend_task_timer(7401, "referral_tasks", extra_hours=24)
        self.assertTrue(ok)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT task_state, expires_at, task_origin FROM referral_tasks WHERE id = 7401"
            ).fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        self.assertEqual(row["task_origin"], "external")
        # expires_at should now be in the future
        exp_dt = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
        self.assertGreater(exp_dt, datetime.utcnow())
        _cleanup_task("referral_tasks", 7401)

    def test_reactivate_external_task(self):
        with gb.get_connection() as conn:
            conn.execute(
                "INSERT INTO referral_tasks "
                "(id, buyer_id, referral_link, quantity_requested, quantity_remaining, "
                "points_spent, amount_cents, status, task_state, task_origin) "
                "VALUES (7402, ?, 'https://t.me/B?start=re', 5, 5, 100, 100, 'active', 'EXPIRED', 'external')",
                (74001,),
            )
            conn.commit()
        ok = gb.reactivate_task_admin(7402, "referral_tasks")
        self.assertTrue(ok)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT task_state, task_origin FROM referral_tasks WHERE id = 7402"
            ).fetchone()
        self.assertEqual(row["task_state"], "AVAILABLE")
        self.assertEqual(row["task_origin"], "external")
        _cleanup_task("referral_tasks", 7402)


class TestExistingBehaviorUnchanged(unittest.TestCase):
    """Timer-related existing behavior remains correct."""

    def test_task_expiry_hours_still_24(self):
        self.assertEqual(gb.TASK_EXPIRY_HOURS, 24)

    def test_no_user_facing_timer_leak(self):
        import inspect
        src = inspect.getsource(gb.build_tasks_text)
        for word in ["expires_at", "deadline", "countdown", "timer"]:
            self.assertNotIn(word.lower(), src.lower(), f"build_tasks_text leaks '{word}'")

    def test_admin_all_tasks_includes_task_origin(self):
        tasks = gb.get_all_tasks_for_admin(limit=50)
        self.assertIsInstance(tasks, list)
        for t in tasks:
            self.assertIn("task_source", t)


if __name__ == "__main__":
    unittest.main()
