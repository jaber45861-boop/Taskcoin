"""Tests for Micro-Order 7B: Advertiser task financial foundation.

Covers:
- create_advertiser_task() atomic wallet debit
- $0.01 eligibility gate
- 1.30 pricing model
- Input validation
- balance_cents preservation
- Schema fields (advertiser_id, total_cost_nano)
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


def _add_user(uid, balance_nano=0, activated=1):
    with gb.get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users "
            "(user_id, first_name, balance_usd_nano, activation_status) "
            "VALUES (?, 'Test', ?, ?)",
            (uid, balance_nano, activated),
        )
        conn.commit()


def _get_balance(uid):
    with gb.get_connection() as conn:
        row = conn.execute(
            "SELECT balance_usd_nano, balance_cents FROM users WHERE user_id = ?",
            (uid,),
        ).fetchone()
    return dict(row) if row else None


def _cleanup_task(task_id):
    try:
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM manual_tasks WHERE id = ?", (task_id,))
            conn.commit()
    except Exception:
        pass


def _cleanup_user(uid):
    try:
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM users WHERE user_id = ?", (uid,))
            conn.commit()
    except Exception:
        pass


class TestSchema(unittest.TestCase):
    """Verify the new columns exist on manual_tasks."""

    def test_manual_tasks_has_advertiser_id(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("advertiser_id", cols)

    def test_manual_tasks_has_total_cost_nano(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("total_cost_nano", cols)

    def test_manual_tasks_has_reward_usd_nano(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("reward_usd_nano", cols)


class TestSuccessfulCreation(unittest.TestCase):
    """Advertiser with sufficient balance can create a task."""

    def setUp(self):
        self.uid = 90001
        _add_user(self.uid, balance_nano=1_000_000_000)  # $1.00

    def tearDown(self):
        _cleanup_user(self.uid)

    def test_creates_task_with_correct_fields(self):
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Test Ad Task",
            task_link="https://t.me/testchannel",
            reward_nano=5_000_000,  # $0.005 per worker
            quantity=10,
            task_type="telegram_channel",
            target_reference="@testchannel",
        )
        self.assertIsNotNone(tid)
        self.assertIsInstance(tid, int)
        _cleanup_task(tid)

    def test_total_cost_is_reward_times_quantity_times_1_30(self):
        reward = 5_000_000
        qty = 10
        expected_total = int(
            (Decimal(str(reward * qty)) * Decimal("1.30"))
            .quantize(Decimal("1"), rounding=__import__("decimal").ROUND_HALF_UP)
        )
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Cost test",
            task_link="https://t.me/cost",
            reward_nano=reward,
            quantity=qty,
        )
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT total_cost_nano, advertiser_id, task_origin, expires_at "
                "FROM manual_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        self.assertEqual(row["total_cost_nano"], expected_total)
        self.assertEqual(row["advertiser_id"], self.uid)
        self.assertEqual(row["task_origin"], "internal")
        self.assertIsNone(row["expires_at"])
        _cleanup_task(tid)

    def test_balance_debited_correctly(self):
        reward = 2_000_000
        qty = 5
        expected_total = int(
            (Decimal(str(reward * qty)) * Decimal("1.30"))
            .quantize(Decimal("1"), rounding=__import__("decimal").ROUND_HALF_UP)
        )
        before = _get_balance(self.uid)["balance_usd_nano"]
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Debit test",
            task_link="https://t.me/debit",
            reward_nano=reward,
            quantity=qty,
        )
        self.assertIsNotNone(tid)
        after = _get_balance(self.uid)["balance_usd_nano"]
        self.assertEqual(after, before - expected_total)
        _cleanup_task(tid)

    def test_balance_cents_unchanged(self):
        before = _get_balance(self.uid)["balance_cents"]
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Cents test",
            task_link="https://t.me/cents",
            reward_nano=1_000_000,
            quantity=1,
        )
        self.assertIsNotNone(tid)
        after = _get_balance(self.uid)["balance_cents"]
        self.assertEqual(after, before)
        _cleanup_task(tid)

    def test_reward_usd_nano_stores_nano(self):
        """reward_usd_nano column stores the nano reward for worker payout;
        reward_points is 0 for advertiser tasks."""
        reward = 3_000_000
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Reward store",
            task_link="https://t.me/reward",
            reward_nano=reward,
            quantity=2,
        )
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT reward_points, reward_usd_nano FROM manual_tasks WHERE id = ?", (tid,)
            ).fetchone()
        self.assertEqual(row["reward_points"], 0)
        self.assertEqual(row["reward_usd_nano"], reward)
        _cleanup_task(tid)

    def test_quantity_set_correctly(self):
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Qty test",
            task_link="https://t.me/qty",
            reward_nano=1_000_000,
            quantity=7,
        )
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT quantity_requested, quantity_remaining FROM manual_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        self.assertEqual(row["quantity_requested"], 7)
        self.assertEqual(row["quantity_remaining"], 7)
        _cleanup_task(tid)


class TestInsufficientBalance(unittest.TestCase):
    """Task creation fails when balance is insufficient."""

    def setUp(self):
        self.uid = 90002
        _add_user(self.uid, balance_nano=1_000_000)  # $0.001

    def tearDown(self):
        _cleanup_user(self.uid)

    def test_insufficient_balance_returns_none(self):
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Too expensive",
            task_link="https://t.me/expensive",
            reward_nano=5_000_000,
            quantity=10,
        )
        self.assertIsNone(tid)

    def test_balance_unchanged_after_failure(self):
        before = _get_balance(self.uid)["balance_usd_nano"]
        gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="No change",
            task_link="https://t.me/nochange",
            reward_nano=5_000_000,
            quantity=10,
        )
        after = _get_balance(self.uid)["balance_usd_nano"]
        self.assertEqual(after, before)


class TestEligibilityGate(unittest.TestCase):
    """$0.01 eligibility gate: wallet must be >= 10,000,000 nano."""

    def test_exact_threshold_accepted(self):
        uid = 90003
        _add_user(uid, balance_nano=10_000_000)  # exactly $0.01
        tid = gb.create_advertiser_task(
            advertiser_id=uid,
            title="Exact $0.01",
            task_link="https://t.me/exact",
            reward_nano=100_000,  # tiny reward
            quantity=1,
        )
        self.assertIsNotNone(tid)
        _cleanup_task(tid)
        _cleanup_user(uid)

    def test_below_threshold_rejected(self):
        uid = 90004
        _add_user(uid, balance_nano=9_999_999)  # $0.009999999
        tid = gb.create_advertiser_task(
            advertiser_id=uid,
            title="Below $0.01",
            task_link="https://t.me/below",
            reward_nano=100_000,
            quantity=1,
        )
        self.assertIsNone(tid)
        _cleanup_user(uid)

    def test_zero_balance_rejected(self):
        uid = 90005
        _add_user(uid, balance_nano=0)
        tid = gb.create_advertiser_task(
            advertiser_id=uid,
            title="Zero balance",
            task_link="https://t.me/zero",
            reward_nano=1_000_000,
            quantity=1,
        )
        self.assertIsNone(tid)
        _cleanup_user(uid)


class TestInputValidation(unittest.TestCase):
    """Invalid inputs are rejected without modifying state."""

    def setUp(self):
        self.uid = 90006
        _add_user(self.uid, balance_nano=1_000_000_000)

    def tearDown(self):
        _cleanup_user(self.uid)

    def test_zero_reward_rejected(self):
        with self.assertRaises(ValueError):
            gb.create_advertiser_task(
                advertiser_id=self.uid,
                title="Zero reward",
                task_link="https://t.me/zero",
                reward_nano=0,
                quantity=1,
            )

    def test_negative_reward_rejected(self):
        with self.assertRaises(ValueError):
            gb.create_advertiser_task(
                advertiser_id=self.uid,
                title="Negative reward",
                task_link="https://t.me/neg",
                reward_nano=-1,
                quantity=1,
            )

    def test_zero_quantity_rejected(self):
        with self.assertRaises(ValueError):
            gb.create_advertiser_task(
                advertiser_id=self.uid,
                title="Zero qty",
                task_link="https://t.me/zeroq",
                reward_nano=1_000_000,
                quantity=0,
            )

    def test_negative_quantity_rejected(self):
        with self.assertRaises(ValueError):
            gb.create_advertiser_task(
                advertiser_id=self.uid,
                title="Negative qty",
                task_link="https://t.me/negq",
                reward_nano=1_000_000,
                quantity=-1,
            )

    def test_invalid_task_type_rejected(self):
        with self.assertRaises(ValueError):
            gb.create_advertiser_task(
                advertiser_id=self.uid,
                title="Bad type",
                task_link="https://t.me/bad",
                reward_nano=1_000_000,
                quantity=1,
                task_type="invalid_type",
            )

    def test_balance_unchanged_after_rejection(self):
        before = _get_balance(self.uid)["balance_usd_nano"]
        try:
            gb.create_advertiser_task(
                advertiser_id=self.uid,
                title="Fail",
                task_link="https://t.me/fail",
                reward_nano=0,
                quantity=1,
            )
        except ValueError:
            pass
        after = _get_balance(self.uid)["balance_usd_nano"]
        self.assertEqual(after, before)


class TestNonExistentUser(unittest.TestCase):
    """Non-existent advertiser is rejected."""

    def test_nonexistent_user_returns_none(self):
        tid = gb.create_advertiser_task(
            advertiser_id=999999,
            title="Ghost",
            task_link="https://t.me/ghost",
            reward_nano=1_000_000,
            quantity=1,
        )
        self.assertIsNone(tid)


class TestInactiveUser(unittest.TestCase):
    """Non-activated advertiser is rejected."""

    def test_inactive_user_returns_none(self):
        uid = 90007
        _add_user(uid, balance_nano=1_000_000_000, activated=0)
        tid = gb.create_advertiser_task(
            advertiser_id=uid,
            title="Inactive",
            task_link="https://t.me/inactive",
            reward_nano=1_000_000,
            quantity=1,
        )
        self.assertIsNone(tid)
        _cleanup_user(uid)


class TestBoundaryValues(unittest.TestCase):
    """Large and small values work correctly."""

    def setUp(self):
        self.uid = 90008
        _add_user(self.uid, balance_nano=100_000_000_000)  # $100

    def tearDown(self):
        _cleanup_user(self.uid)

    def test_large_quantity(self):
        reward = 1_000_000  # $0.001
        qty = 1000
        expected = int(
            (Decimal(str(reward * qty)) * Decimal("1.30"))
            .quantize(Decimal("1"), rounding=__import__("decimal").ROUND_HALF_UP)
        )
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Large qty",
            task_link="https://t.me/large",
            reward_nano=reward,
            quantity=qty,
        )
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT total_cost_nano FROM manual_tasks WHERE id = ?", (tid,)
            ).fetchone()
        self.assertEqual(row["total_cost_nano"], expected)
        _cleanup_task(tid)

    def test_minimum_reward(self):
        """1 nano reward with 1 quantity = 1.30 nano (rounds to 1)."""
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Min reward",
            task_link="https://t.me/min",
            reward_nano=1,
            quantity=1,
        )
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT total_cost_nano, reward_points, reward_usd_nano FROM manual_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        # 1 * 1.30 = 1.30, rounds to 1
        self.assertEqual(row["total_cost_nano"], 1)
        self.assertEqual(row["reward_points"], 0)
        self.assertEqual(row["reward_usd_nano"], 1)
        _cleanup_task(tid)


class TestPreservesExistingBehavior(unittest.TestCase):
    """Existing admin create_manual_task is unchanged."""

    def test_admin_task_has_no_advertiser_id(self):
        tid = gb.create_manual_task(
            title="Admin task",
            task_link="https://t.me/admin",
            reward_points=5000,
            quantity=3,
        )
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT advertiser_id, total_cost_nano, task_origin, expires_at "
                "FROM manual_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        self.assertIsNone(row["advertiser_id"])
        self.assertIsNone(row["total_cost_nano"])
        self.assertEqual(row["task_origin"], "internal")
        self.assertIsNone(row["expires_at"])
        _cleanup_task(tid)


if __name__ == "__main__":
    unittest.main()
