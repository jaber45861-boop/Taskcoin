"""Regression tests: callback_check_channel activation flow (Step 5).

Proves the "تحقق" (check subscription) button:
  A. No longer calls require_active_account (the bug that blocked
     non-activated users from ever activating through this button).
  B. Checks actual membership in ALL mandatory channels directly via
     get_channels_status, regardless of prior subscription or rewards.
  C. Opens the account (unfreeze) when every mandatory channel is
     satisfied, showing the main keyboard — same semantics as the
     «تحقق من إتمام كافة الشروط» button.
  D. Keeps the user on the subscription screen when channels are missing.
  E. Does not touch is_subscribed or any other flow.
"""
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

import reward_api
reward_api._live_egp_per_usd = Decimal("50")

import inspect


def make_call(user_id, task_key):
    """Build a minimal fake Telegram callback object."""
    call = mock.MagicMock()
    call.from_user.id = user_id
    call.from_user.first_name = "Tester"
    call.data = f"check_channel_{task_key}"
    call.message.chat.id = user_id
    call.message.message_id = 4242
    return call


def patch_telegram():
    """Silence Telegram network calls; capture what the handler renders."""
    captured = {"edit": [], "answers": [], "sends": []}

    def fake_edit(text=None, **kwargs):
        captured["edit"].append((text, kwargs))
        return True

    def fake_answer(cid, text=None, show_alert=False):
        captured["answers"].append((text, show_alert))

    def fake_send(*args, **kwargs):
        captured["sends"].append((args, kwargs))

    p_edit = mock.patch.object(gb.bot, "edit_message_text", side_effect=fake_edit)
    p_answer = mock.patch.object(gb.bot, "answer_callback_query", side_effect=fake_answer)
    p_send = mock.patch.object(gb.bot, "send_message", side_effect=fake_send)
    return captured, p_edit, p_answer, p_send


def status_all(subscribed: bool, rewarded: bool = False):
    """Manual channel status without any Telegram network calls."""
    return [
        {**ch, "subscribed": subscribed, "rewarded": rewarded}
        for ch in gb.REQUIRED_CHANNELS
    ]


class _BotDBTestCase(unittest.TestCase):
    """Shared temp-DB bootstrap with child-first cleanup."""

    @classmethod
    def setUpClass(cls):
        fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        gb.DB_PATH = cls.DB_PATH
        gb.init_db()

    @classmethod
    def tearDownClass(cls):
        try:
            gb.get_connection().close()
        except Exception:
            pass
        os.unlink(cls.DB_PATH)

    def setUp(self):
        with gb.get_connection() as conn:
            # Children first to satisfy FK constraints.
            conn.execute("DELETE FROM task_completions")
            conn.execute("DELETE FROM channel_reward_ledger")
            conn.execute("DELETE FROM user_inquiries")
            conn.execute("DELETE FROM users")
            conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# A. HANDLER WIRING
# ══════════════════════════════════════════════════════════════════════════════


class TestHandlerWiring(unittest.TestCase):
    def test_no_require_active_account_precheck(self):
        """The handler must NOT gate the check behind require_active_account."""
        src = inspect.getsource(gb.callback_check_channel)
        self.assertNotIn("require_active_account", src)

    def test_no_is_subscribed_single_channel_check(self):
        """Must not rely on the single-channel is_subscribed() check anymore."""
        src = inspect.getsource(gb.callback_check_channel)
        self.assertNotIn("is_subscribed(", src)

    def test_checks_all_channels_via_channels_status(self):
        """Must call get_channels_status and branch on missing subscriptions."""
        src = inspect.getsource(gb.callback_check_channel)
        self.assertIn("get_channels_status(user_id)", src)
        self.assertIn("not_subbed", src)

    def test_all_subscribed_path_opens_account(self):
        """All channels satisfied: unfreeze + main_keyboard, like verify_activation."""
        src = inspect.getsource(gb.callback_check_channel)
        self.assertIn("reactivate_after_penalty(user_id)", src)
        self.assertIn("restore_channel_rewards(user_id)", src)
        self.assertIn("main_keyboard", src)


# ══════════════════════════════════════════════════════════════════════════════
# B. BEHAVIOR — ALL CHANNELS SATISFIED → ACCOUNT OPENS
# ══════════════════════════════════════════════════════════════════════════════


class TestActivationOnCompleteSubscriptions(_BotDBTestCase):
    def test_inactive_user_with_all_channels_gets_activated(self):
        """Not-yet-activated user, member of every channel → account opens."""
        gb.add_user(7001, "T", None, None)
        self.assertFalse(gb.is_account_active(7001))

        task_key = gb.BASE_REQUIRED_CHANNELS[0]["task_key"]
        call = make_call(7001, task_key)

        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True, rewarded=True)):
            with p_edit, p_answer, p_send:
                gb.callback_check_channel(call)

        self.assertTrue(gb.is_account_active(7001))
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("مستوفاة", joined)
        self.assertTrue(captured["edit"])
        self.assertIsNotNone(captured["edit"][0][1].get("reply_markup"))

    def test_frozen_user_gets_unfrozen(self):
        """A frozen (penalized) user who re-subscribed gets unfrozen."""
        gb.add_user(7003, "T", None, None)
        with gb.get_connection() as conn:
            conn.execute("UPDATE users SET activation_status = 0 WHERE user_id = 7003")
            conn.commit()
        self.assertFalse(gb.is_account_active(7003))

        task_key = gb.BASE_REQUIRED_CHANNELS[0]["task_key"]
        call = make_call(7003, task_key)

        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True, rewarded=True)):
            with p_edit, p_answer, p_send:
                gb.callback_check_channel(call)

        self.assertTrue(gb.is_account_active(7003))
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("مستوفاة", joined)

    def test_already_rewarded_channel_not_double_credited(self):
        """Active user, reward already granted: no double credit, tasks screen."""
        gb.add_user(7004, "T", None, None)
        with gb.get_connection() as conn:
            conn.execute("UPDATE users SET activation_status = 1 WHERE user_id = 7004")
            conn.commit()
        channel = gb.REQUIRED_CHANNELS[0]
        gb.grant_channel_reward(7004, channel)
        balance_after_grant = gb.get_balance_usd_nano(7004)

        call = make_call(7004, channel["task_key"])

        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True, rewarded=True)):
            with p_edit, p_answer, p_send:
                gb.callback_check_channel(call)

        self.assertTrue(gb.is_account_active(7004))
        self.assertEqual(gb.get_balance_usd_nano(7004), balance_after_grant)
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("مسبقاً", joined)


# ══════════════════════════════════════════════════════════════════════════════
# C. BEHAVIOR — MISSING CHANNELS → STAY ON GATE
# ══════════════════════════════════════════════════════════════════════════════


class TestMissingChannelsStaysOnGate(_BotDBTestCase):
    def test_target_channel_not_subscribed_keeps_gate(self):
        """Target channel itself not subscribed: alert, no activation."""
        gb.add_user(7101, "T", None, None)

        task_key = gb.BASE_REQUIRED_CHANNELS[0]["task_key"]
        call = make_call(7101, task_key)

        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=False)):
            with p_edit, p_answer, p_send:
                gb.callback_check_channel(call)

        self.assertFalse(gb.is_account_active(7101))
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("لم تشترك", joined)

    def test_other_channel_missing_keeps_gate_but_grants_target_reward(self):
        """Target subscribed but another missing: reward granted, stays on gate."""
        gb.add_user(7102, "T", None, None)

        task_key = gb.BASE_REQUIRED_CHANNELS[0]["task_key"]
        call = make_call(7102, task_key)

        # Simulate a second mandatory channel so "another channel missing" exists.
        two_channels = list(gb.REQUIRED_CHANNELS) + [
            {"username": "@second_req", "name": "قناة إلزامية ثانية",
             "reward": 30, "task_key": "channel_second_req"},
        ]

        def mixed_status(uid):
            return [
                {**ch, "subscribed": ch["task_key"] == task_key, "rewarded": False}
                for ch in two_channels
            ]

        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "refresh_required_channels", lambda: None), \
             mock.patch.object(gb, "REQUIRED_CHANNELS", two_channels), \
             mock.patch.object(gb, "get_channels_status", side_effect=mixed_status):
            with p_edit, p_answer, p_send:
                gb.callback_check_channel(call)

        self.assertFalse(gb.is_account_active(7102))
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("تم تأكيد الاشتراك", joined)
        # Channel reward actually credited (50 EGP cents @ rate 50 → 10M nano).
        self.assertEqual(
            gb.get_balance_usd_nano(7102),
            gb.egp_cents_to_wallet_nano(int(gb.REQUIRED_CHANNELS[0]["reward"])),
        )
        # Activation gate screen re-rendered (contains verify_activation button).
        self.assertTrue(captured["edit"])
        markup = captured["edit"][0][1].get("reply_markup")
        texts = [
            getattr(b, "callback_data", None)
            for row in getattr(markup, "keyboard", [])
            for b in row
        ]
        self.assertIn("verify_activation", texts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
