"""Regression tests: tasks must NOT automatically expire.

Timer functionality has been removed. These tests prove:
- Tasks remain available without expiration
- No TASK_EXPIRY_HOURS constant exists
- No timer functions exist
- No timer callbacks exist
- Internal tasks have no timer logic
- Existing tasks are not blocked by expiration
"""
import os
import sys
import tempfile
import unittest
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

import reward_api as _ra
_ra._live_egp_per_usd = Decimal("50")


def _add_user(uid, balance=1_000_000_000_000):
    """Add user with sufficient balance for task creation."""
    with gb.get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, first_name, balance_usd_nano, activation_status) "
            "VALUES (?, 'Test', ?, 1)", (uid, balance),
        )
        conn.commit()


class TestNoTimerConstant(unittest.TestCase):
    """TASK_EXPIRY_HOURS must not exist."""

    def test_no_task_expiry_hours(self):
        self.assertFalse(hasattr(gb, "TASK_EXPIRY_HOURS"),
                         "TASK_EXPIRY_HOURS must be removed")


class TestNoTimerFunctions(unittest.TestCase):
    """Timer-specific functions must not exist."""

    def test_no_extend_task_timer(self):
        self.assertFalse(hasattr(gb, "extend_task_timer"),
                         "extend_task_timer must be removed")

    def test_no_reactivate_task_admin(self):
        self.assertFalse(hasattr(gb, "reactivate_task_admin"),
                         "reactivate_task_admin must be removed")

    def test_no_admin_task_timer_summary(self):
        self.assertFalse(hasattr(gb, "_admin_task_timer_summary"),
                         "_admin_task_timer_summary must be removed")


class TestManualTaskCreation(unittest.TestCase):
    """Manual tasks should be creatable and visible without expiry."""

    def test_create_manual_task(self):
        task_id = gb.create_manual_task(
            "Test Manual", "https://example.com", 500, 5,
        )
        self.assertIsNotNone(task_id)
        tasks = gb.get_active_manual_tasks()
        ids = [t["id"] for t in tasks]
        self.assertIn(task_id, ids)

    def test_manual_task_no_expiry_filter(self):
        """Manual tasks should be queryable without expiry filter."""
        task_id = gb.create_manual_task(
            "No Expiry", "https://example.com", 300, 3,
        )
        self.assertIsNotNone(task_id)
        tasks = gb.get_active_manual_tasks()
        ids = [t["id"] for t in tasks]
        self.assertIn(task_id, ids)


class TestNoTimerCallbacks(unittest.TestCase):
    """Timer-specific admin callbacks must not exist."""

    def test_no_extend_callback(self):
        """admin_extend_task callback must not be registered."""
        import inspect
        src = inspect.getsource(gb)
        self.assertNotIn("admin_extend_task_", src,
                         "admin_extend_task callback must be removed")

    def test_no_reactivate_callback(self):
        """admin_reactivate_task callback must not be registered."""
        import inspect
        src = inspect.getsource(gb)
        self.assertNotIn("admin_reactivate_task_", src,
                         "admin_reactivate_task callback must be removed")


class TestDeleteTaskStillWorks(unittest.TestCase):
    """delete_task_admin should still work (non-timer admin function)."""

    def test_delete_task_admin(self):
        task_id = gb.create_manual_task(
            "To Delete", "https://example.com", 200, 2,
        )
        self.assertIsNotNone(task_id)
        ok = gb.delete_task_admin(task_id, "manual_tasks")
        self.assertTrue(ok)


class TestInternalTaskCreation(unittest.TestCase):
    """Internal tasks should be created without timer logic."""

    def test_create_manual_task_internal(self):
        task_id = gb.create_manual_task(
            "Internal Task", "https://example.com", 300, 3,
        )
        self.assertIsNotNone(task_id)
        with gb.get_connection() as conn:
            task = conn.execute(
                "SELECT * FROM manual_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        self.assertIsNotNone(task)


class TestGetAllTasksForAdmin(unittest.TestCase):
    """get_all_tasks_for_admin should work without timer summary."""

    def test_get_all_tasks(self):
        task_id = gb.create_manual_task(
            "Admin Test", "https://example.com", 100, 1,
        )
        self.assertIsNotNone(task_id)
        tasks = gb.get_all_tasks_for_admin()
        self.assertTrue(len(tasks) > 0)


if __name__ == "__main__":
    unittest.main()
