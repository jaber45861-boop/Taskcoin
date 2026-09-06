"""Micro-Order 7D regression tests: reward_usd_nano unit separation.

Proves:
- admin task reward_points remains EGP cents
- advertiser reward stored in reward_usd_nano, reward_points=0
- advertiser worker payout uses reward_usd_nano directly (no EGP conversion)
- admin worker payout still converts reward_points via egp_cents_to_wallet_nano
- display helpers return correct values for both paths
- callback_claim_manual display is correct for both
- $0.005 advertiser reward remains exactly 5,000,000 USD nano (no 2M× double conversion)
- balance_cents remains unchanged
- total_cost_nano remains correct
- task_origin remains internal
- expires_at remains NULL
"""
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

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
            "INSERT OR REPLACE INTO users "
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


def _cleanup_completions(uid):
    try:
        with gb.get_connection() as conn:
            conn.execute(
                "DELETE FROM task_completions WHERE user_id = ?", (uid,)
            )
            conn.commit()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# 1. Schema
# ══════════════════════════════════════════════════════════════════════════════
class TestSchema(unittest.TestCase):
    def test_reward_usd_nano_column_exists(self):
        with gb.get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_tasks)").fetchall()}
        self.assertIn("reward_usd_nano", cols)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Advertiser task: reward_usd_nano stores nano, reward_points = 0
# ══════════════════════════════════════════════════════════════════════════════
class TestAdvertiserTaskStorage(unittest.TestCase):
    def setUp(self):
        self.uid = 91001
        _add_user(self.uid, balance_nano=100_000_000)

    def tearDown(self):
        _cleanup_user(self.uid)

    def test_reward_usd_nano_stored_correctly(self):
        """Advertiser task stores reward_nano in reward_usd_nano, reward_points=0."""
        reward = 5_000_000  # $0.005
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Unit test",
            task_link="https://t.me/test",
            reward_nano=reward,
            quantity=3,
        )
        self.assertIsNotNone(tid)
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT reward_points, reward_usd_nano FROM manual_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        self.assertEqual(row["reward_points"], 0)
        self.assertEqual(row["reward_usd_nano"], reward)
        _cleanup_task(tid)

    def test_reward_points_not_used_as_nano(self):
        """reward_points is 0 — must NOT be interpreted as USD nano."""
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Unit test 2",
            task_link="https://t.me/test2",
            reward_nano=7_000_000,
            quantity=1,
        )
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT reward_points, reward_usd_nano FROM manual_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        self.assertEqual(row["reward_points"], 0)
        self.assertEqual(row["reward_usd_nano"], 7_000_000)
        _cleanup_task(tid)

    def test_total_cost_nano_unchanged(self):
        """1.30 margin calculation still works in USD nano."""
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Cost test",
            task_link="https://t.me/cost",
            reward_nano=10_000_000,
            quantity=2,
        )
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT total_cost_nano FROM manual_tasks WHERE id = ?", (tid,)
            ).fetchone()
        self.assertEqual(row["total_cost_nano"], 26_000_000)
        _cleanup_task(tid)

    def test_task_origin_remains_internal(self):
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Origin test",
            task_link="https://t.me/orig",
            reward_nano=1_000_000,
            quantity=1,
        )
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT task_origin FROM manual_tasks WHERE id = ?", (tid,)
            ).fetchone()
        self.assertEqual(row["task_origin"], "internal")
        _cleanup_task(tid)

    def test_expires_at_remains_null(self):
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Expires test",
            task_link="https://t.me/exp",
            reward_nano=1_000_000,
            quantity=1,
        )
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT expires_at FROM manual_tasks WHERE id = ?", (tid,)
            ).fetchone()
        self.assertIsNone(row["expires_at"])
        _cleanup_task(tid)

    def test_balance_cents_unchanged(self):
        bal = _get_balance(self.uid)
        self.assertIsNotNone(bal)
        cents_before = bal["balance_cents"]
        tid = gb.create_advertiser_task(
            advertiser_id=self.uid,
            title="Cents test",
            task_link="https://t.me/cents",
            reward_nano=5_000_000,
            quantity=2,
        )
        bal_after = _get_balance(self.uid)
        self.assertEqual(bal_after["balance_cents"], cents_before)
        _cleanup_task(tid)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Admin task: reward_points remains EGP cents
# ══════════════════════════════════════════════════════════════════════════════
class TestAdminTaskStorage(unittest.TestCase):
    def test_admin_task_reward_points_is_egp_cents(self):
        """Admin-created task stores EGP cents in reward_points, reward_usd_nano is NULL."""
        tid = gb.create_manual_task(
            title="Admin task",
            task_link="https://t.me/admin",
            reward_points=5000,  # 50.00 EGP
            quantity=3,
        )
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT reward_points, reward_usd_nano FROM manual_tasks WHERE id = ?",
                (tid,),
            ).fetchone()
        self.assertEqual(row["reward_points"], 5000)
        self.assertIsNone(row["reward_usd_nano"])
        _cleanup_task(tid)

    def test_admin_task_no_advertiser_id(self):
        tid = gb.create_manual_task(
            title="Admin no adv",
            task_link="https://t.me/noadv",
            reward_points=1000,
            quantity=1,
        )
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT advertiser_id FROM manual_tasks WHERE id = ?", (tid,)
            ).fetchone()
        self.assertIsNone(row["advertiser_id"])
        _cleanup_task(tid)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Worker payout: claim_manual_task uses correct unit
# ══════════════════════════════════════════════════════════════════════════════
class TestWorkerPayout(unittest.TestCase):
    def setUp(self):
        self.worker = 92001
        self.adv = 92002
        _add_user(self.worker, balance_nano=0)
        _add_user(self.adv, balance_nano=100_000_000)

    def tearDown(self):
        _cleanup_user(self.worker)
        _cleanup_user(self.adv)
        _cleanup_completions(self.worker)

    @patch.object(gb, "is_subscribed", return_value=True)
    def test_advertiser_task_payout_uses_reward_usd_nano(self, _mock_sub):
        """Worker gets exactly reward_usd_nano — no EGP conversion applied."""
        reward = 5_000_000  # $0.005
        tid = gb.create_advertiser_task(
            advertiser_id=self.adv,
            title="Payout test",
            task_link="https://t.me/chan",
            reward_nano=reward,
            quantity=5,
            task_type="telegram_channel",
            target_reference="https://t.me/testchannel",
        )
        self.assertIsNotNone(tid)
        result = gb.claim_manual_task(tid, self.worker)
        self.assertEqual(result, "claimed")
        bal = _get_balance(self.worker)
        self.assertEqual(bal["balance_usd_nano"], reward)

    @patch.object(gb, "is_subscribed", return_value=True)
    def test_no_double_conversion(self, _mock_sub):
        """Prove 5,000,000 nano reward does NOT become 10 trillion via double conversion."""
        reward = 5_000_000
        tid = gb.create_advertiser_task(
            advertiser_id=self.adv,
            title="No double",
            task_link="https://t.me/nodbl",
            reward_nano=reward,
            quantity=1,
            task_type="telegram_channel",
            target_reference="https://t.me/nodblchannel",
        )
        gb.claim_manual_task(tid, self.worker)
        bal = _get_balance(self.worker)
        # Double conversion would produce: 5_000_000 * 10_000_000 / 50 = 1_000_000_000_000
        self.assertNotEqual(bal["balance_usd_nano"], 1_000_000_000_000)
        self.assertEqual(bal["balance_usd_nano"], reward)

    @patch.object(gb, "is_subscribed", return_value=True)
    def test_advertiser_task_worker_payout_exact(self, _mock_sub):
        """$0.005 reward = exactly 5,000,000 USD nano to worker."""
        reward = 5_000_000
        tid = gb.create_advertiser_task(
            advertiser_id=self.adv,
            title="Exact nano",
            task_link="https://t.me/exact",
            reward_nano=reward,
            quantity=1,
            task_type="telegram_channel",
            target_reference="https://t.me/exactchannel",
        )
        gb.claim_manual_task(tid, self.worker)
        bal = _get_balance(self.worker)
        self.assertEqual(bal["balance_usd_nano"], 5_000_000)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Admin task payout still converts EGP cents
# ══════════════════════════════════════════════════════════════════════════════
class TestAdminWorkerPayout(unittest.TestCase):
    def setUp(self):
        self.worker = 93001
        _add_user(self.worker, balance_nano=0)

    def tearDown(self):
        _cleanup_user(self.worker)
        _cleanup_completions(self.worker)

    @patch.object(gb, "is_subscribed", return_value=True)
    def test_admin_task_payout_converts_egp_cents(self, _mock_sub):
        """Admin task reward_points=5000 EGP cents → converted via egp_cents_to_wallet_nano."""
        tid = gb.create_manual_task(
            title="Admin payout",
            task_link="https://t.me/admpay",
            reward_points=5000,  # EGP cents
            quantity=1,
            task_type="telegram_channel",
            target_reference="https://t.me/admpaychannel",
        )
        expected_nano = gb.egp_cents_to_wallet_nano(5000)
        result = gb.claim_manual_task(tid, self.worker)
        self.assertEqual(result, "claimed")
        bal = _get_balance(self.worker)
        self.assertEqual(bal["balance_usd_nano"], expected_nano)
        # At EGP_PER_USD=50: 5000 * 10_000_000 / 50 = 1_000_000_000
        self.assertEqual(expected_nano, 1_000_000_000)

    @patch.object(gb, "is_subscribed", return_value=True)
    def test_admin_task_payout_different_egp_value(self, _mock_sub):
        """Admin task reward_points=100 EGP cents → correct conversion."""
        tid = gb.create_manual_task(
            title="Admin small",
            task_link="https://t.me/admsmall",
            reward_points=100,
            quantity=1,
            task_type="telegram_channel",
            target_reference="https://t.me/admsmallchannel",
        )
        expected_nano = gb.egp_cents_to_wallet_nano(100)
        result = gb.claim_manual_task(tid, self.worker)
        self.assertEqual(result, "claimed")
        bal = _get_balance(self.worker)
        self.assertEqual(bal["balance_usd_nano"], expected_nano)
        # At EGP_PER_USD=50: 100 * 10_000_000 / 50 = 20_000_000
        self.assertEqual(expected_nano, 20_000_000)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Display helper: _task_reward_display
# ══════════════════════════════════════════════════════════════════════════════
class TestDisplayHelper(unittest.TestCase):
    def test_advertiser_display_uses_reward_usd_nano(self):
        """Display for advertiser task uses reward_usd_nano directly."""
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT 5000000 AS reward_usd_nano, 0 AS reward_points"
            ).fetchone()
        display = gb._task_reward_display(row)
        # 5,000,000 USD nano = $0.005
        self.assertIn("0.005", display)

    def test_admin_display_converts_egp_cents(self):
        """Display for admin task converts reward_points from EGP cents."""
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT NULL AS reward_usd_nano, 100 AS reward_points"
            ).fetchone()
        display = gb._task_reward_display(row)
        # 100 EGP cents at 50 EGP/USD: 100/100/50 = $0.02
        self.assertIn("0.02", display)

    def test_callback_claim_manual_advertiser_display(self):
        """callback_claim_manual display is correct for advertiser task."""
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT 5000000 AS reward_usd_nano, 0 AS reward_points"
            ).fetchone()
        display = gb._task_reward_display(row)
        self.assertIn("0.005", display)

    def test_callback_claim_manual_admin_display(self):
        """callback_claim_manual display is correct for admin task."""
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT NULL AS reward_usd_nano, 100 AS reward_points"
            ).fetchone()
        display = gb._task_reward_display(row)
        # 100 EGP cents at 50: 100/100/50 = $0.02
        self.assertIn("0.02", display)


# ══════════════════════════════════════════════════════════════════════════════
# 7. _task_reward_nano helper
# ══════════════════════════════════════════════════════════════════════════════
class TestRewardNanoHelper(unittest.TestCase):
    def test_advertiser_returns_usd_nano_directly(self):
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT 5000000 AS reward_usd_nano, 0 AS reward_points"
            ).fetchone()
        self.assertEqual(gb._task_reward_nano(row), 5_000_000)

    def test_admin_converts_egp_cents(self):
        with gb.get_connection() as conn:
            row = conn.execute(
                "SELECT NULL AS reward_usd_nano, 5000 AS reward_points"
            ).fetchone()
        self.assertEqual(gb._task_reward_nano(row), gb.egp_cents_to_wallet_nano(5000))


# ══════════════════════════════════════════════════════════════════════════════
# 8. callback_claim_manual NameError regression (Micro-Order 10A)
# ══════════════════════════════════════════════════════════════════════════════
class TestCallbackClaimManualNameError(unittest.TestCase):
    """Prove callback_claim_manual no longer crashes with NameError on 'reward'."""

    _counter = 0

    def setUp(self):
        # Use unique IDs per test to avoid INSERT OR IGNORE collisions
        TestCallbackClaimManualNameError._counter += 1
        c = TestCallbackClaimManualNameError._counter
        self.worker = 94000 + c * 100 + 1
        self.adv = 94000 + c * 100 + 2
        _add_user(self.worker, balance_nano=0)
        _add_user(self.adv, balance_nano=100_000_000)

    def tearDown(self):
        _cleanup_user(self.worker)
        _cleanup_user(self.adv)
        _cleanup_completions(self.worker)

    def _make_call(self, user_id, task_id, message_id=88801):
        from unittest.mock import MagicMock
        mock = MagicMock()
        mock.from_user.id = user_id
        mock.id = "cb_claim_test"
        mock.message.message_id = message_id
        mock.message.chat.id = user_id
        mock.data = f"claim_manual_{task_id}"
        return mock

    @patch.object(gb, "require_active_account", return_value=True)
    @patch.object(gb, "account_access_allowed", return_value=True)
    @patch.object(gb, "is_subscribed", return_value=True)
    @patch.object(gb, "bot")
    def test_advertiser_claim_no_nameerror(self, mock_bot, _mock_sub, _mock_acc, _mock_active):
        """Successful non-proof advertiser claim must not crash with NameError."""
        reward = 5_000_000
        tid = gb.create_advertiser_task(
            advertiser_id=self.adv,
            title="NameError test",
            task_link="https://t.me/nerr",
            reward_nano=reward,
            quantity=3,
            task_type="telegram_channel",
            target_reference="@nerrchannel",
        )
        self.assertIsNotNone(tid)
        call = self._make_call(self.worker, tid)
        # If format_balance(reward) were still present, this would raise NameError
        gb.callback_claim_manual(call)
        # Worker should have received the reward
        bal = _get_balance(self.worker)
        self.assertEqual(bal["balance_usd_nano"], reward)
        # bot.send_message should have been called (success message)
        mock_bot.send_message.assert_called()
        # Verify the success message contains the correct reward display
        send_args = mock_bot.send_message.call_args
        msg_text = send_args[0][1] if send_args[0] else send_args[1].get("text", "")
        self.assertIn("0.005", msg_text)

    @patch.object(gb, "require_active_account", return_value=True)
    @patch.object(gb, "account_access_allowed", return_value=True)
    @patch.object(gb, "is_subscribed", return_value=True)
    @patch.object(gb, "bot")
    def test_admin_claim_no_nameerror(self, mock_bot, _mock_sub, _mock_acc, _mock_active):
        """Successful admin claim must also not crash."""
        tid = gb.create_manual_task(
            title="Admin NameError test",
            task_link="https://t.me/admnerr",
            reward_points=5000,  # EGP cents
            quantity=2,
            task_type="telegram_channel",
            target_reference="@admnerrchannel",
        )
        call = self._make_call(self.worker, tid)
        gb.callback_claim_manual(call)
        bal = _get_balance(self.worker)
        expected = gb.egp_cents_to_wallet_nano(5000)
        self.assertEqual(bal["balance_usd_nano"], expected)
        mock_bot.send_message.assert_called()
        send_args = mock_bot.send_message.call_args
        msg_text = send_args[0][1] if send_args[0] else send_args[1].get("text", "")
        # 5000 EGP cents at 50 EGP/USD = $1.00
        self.assertIn("1.0", msg_text)

    @patch.object(gb, "require_active_account", return_value=True)
    @patch.object(gb, "account_access_allowed", return_value=True)
    @patch.object(gb, "is_subscribed", return_value=False)
    @patch.object(gb, "bot")
    def test_failed_claim_no_payout(self, mock_bot, _mock_sub, _mock_acc, _mock_active):
        """A failed claim (not_subscribed) must not produce a payout."""
        reward = 5_000_000
        tid = gb.create_advertiser_task(
            advertiser_id=self.adv,
            title="Fail test",
            task_link="https://t.me/fail",
            reward_nano=reward,
            quantity=1,
            task_type="telegram_channel",
            target_reference="@failchannel",
        )
        call = self._make_call(self.worker, tid)
        gb.callback_claim_manual(call)
        bal = _get_balance(self.worker)
        self.assertEqual(bal["balance_usd_nano"], 0)
        # answer_callback_query should have been called with error message
        mock_bot.answer_callback_query.assert_called()

    @patch.object(gb, "require_active_account", return_value=True)
    @patch.object(gb, "account_access_allowed", return_value=True)
    @patch.object(gb, "is_subscribed", return_value=True)
    @patch.object(gb, "bot")
    def test_advertiser_display_from_reward_usd_nano(self, mock_bot, _mock_sub, _mock_acc, _mock_active):
        """Advertiser task success message shows reward from reward_usd_nano, not reward_points."""
        reward = 7_000_000  # $0.007
        tid = gb.create_advertiser_task(
            advertiser_id=self.adv,
            title="Display test",
            task_link="https://t.me/disp",
            reward_nano=reward,
            quantity=2,
            task_type="telegram_channel",
            target_reference="@dispchannel",
        )
        call = self._make_call(self.worker, tid)
        gb.callback_claim_manual(call)
        # Verify the callback query message shows correct reward
        cb_args = mock_bot.answer_callback_query.call_args
        cb_text = cb_args[0][1] if len(cb_args[0]) > 1 else cb_args[1].get("text", "")
        self.assertIn("0.007", cb_text)
        # Verify the success message also shows correct reward
        send_args = mock_bot.send_message.call_args
        msg_text = send_args[0][1] if send_args[0] else send_args[1].get("text", "")
        self.assertIn("0.007", msg_text)


if __name__ == "__main__":
    unittest.main()
