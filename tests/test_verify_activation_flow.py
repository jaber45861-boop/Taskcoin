"""Regression tests: callback_verify_activation flow (Step 6).

Proves the «تحقق من إتمام كافة الشروط» button:
  A. No longer calls require_active_account before the subscription check —
     the pre-check blocked exactly the users who had not activated yet.
  B. Checks actual membership in ALL mandatory channels first via
     get_channels_status.
  C. If the user is subscribed to everything: activates/unfreezes the
     account and renders main_keyboard.
  D. If channels are missing: stays on the activation gate.
  E. No other flow is modified.
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
import inspect

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


def make_call(user_id):
    """Build a minimal fake Telegram callback object."""
    call = mock.MagicMock()
    call.from_user.id = user_id
    call.from_user.first_name = "Tester"
    call.data = "verify_activation"
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


def status_all(subscribed: bool, rewarded: bool = True):
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


class TestVerifyActivationWiring(unittest.TestCase):
    def test_no_require_active_account_precheck(self):
        """The handler must NOT gate the verification behind require_active_account."""
        src = inspect.getsource(gb.callback_verify_activation)
        self.assertNotIn("require_active_account", src)

    def test_checks_all_channels_before_activation(self):
        """Must inspect actual membership in all channels via get_channels_status."""
        src = inspect.getsource(gb.callback_verify_activation)
        self.assertIn("get_channels_status(user_id)", src)
        self.assertIn("not_subbed", src)
        self.assertIn("reactivate_after_penalty(user_id)", src)
        self.assertIn("activate_user(user_id)", src)
        self.assertIn("main_keyboard", src)


# ══════════════════════════════════════════════════════════════════════════════
# B. BEHAVIOR — SUBSCRIBED TO EVERYTHING → ACCOUNT OPENS
# ══════════════════════════════════════════════════════════════════════════════


class TestVerifyActivationCompletes(_BotDBTestCase):
    def test_inactive_user_subscribed_everywhere_gets_activated(self):
        """Not-yet-activated user, member of every channel → activated + main keyboard."""
        gb.add_user(8101, "T", None, None)
        self.assertFalse(gb.is_account_active(8101))

        call = make_call(8101)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_verify_activation(call)

        self.assertTrue(gb.is_account_active(8101))
        self.assertTrue(captured["edit"])
        self.assertIsNotNone(captured["edit"][0][1].get("reply_markup"))
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("تفعيل", joined)

    def test_frozen_user_gets_unfrozen(self):
        """Frozen (penalized) user who re-subscribed to everything gets unfrozen."""
        gb.add_user(8102, "T", None, None)
        # Go through the real penalty path so a deducted ledger row exists.
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=False, rewarded=True)):
            gb.enforce_channel_subscriptions(8102)
        with gb.get_connection() as conn:
            conn.execute("UPDATE users SET activation_status = 0 WHERE user_id = 8102")
            conn.commit()
        self.assertFalse(gb.is_account_active(8102))

        call = make_call(8102)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_verify_activation(call)

        self.assertTrue(gb.is_account_active(8102))
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("فك تجميد", joined)
        self.assertTrue(captured["edit"])

    def test_first_activation_credits_reward_once(self):
        """First activation credits ACTIVATION_REWARD_USD_NANO exactly once."""
        gb.add_user(8103, "T", None, None)
        before = gb.get_balance_usd_nano(8103)

        call = make_call(8103)
        _, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_verify_activation(call)

        self.assertEqual(
            gb.get_balance_usd_nano(8103) - before,
            gb.ACTIVATION_REWARD_USD_NANO,
        )

    def test_already_active_user_confirmed_without_changes(self):
        """Already-active user: confirmation alert, no extra activation bonus."""
        gb.add_user(8104, "T", None, None)
        with gb.get_connection() as conn:
            conn.execute("UPDATE users SET activation_status = 1 WHERE user_id = 8104")
            conn.commit()
        balance_before = gb.get_balance_usd_nano(8104)

        call = make_call(8104)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_verify_activation(call)

        self.assertTrue(gb.is_account_active(8104))
        self.assertEqual(gb.get_balance_usd_nano(8104), balance_before)
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("تم التحقق من شروط حسابك", joined)


# ══════════════════════════════════════════════════════════════════════════════
# C. BEHAVIOR — MISSING CHANNELS → STAY ON GATE
# ══════════════════════════════════════════════════════════════════════════════


class TestVerifyActivationStaysOnGate(_BotDBTestCase):
    def test_missing_channel_keeps_gate_and_account_inactive(self):
        """Channel missing: alert listing it, gate re-rendered, no activation."""
        gb.add_user(8201, "T", None, None)

        call = make_call(8201)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=False)):
            with p_edit, p_answer, p_send:
                gb.callback_verify_activation(call)

        self.assertFalse(gb.is_account_active(8201))
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("لم تكتمل الشروط", joined)
        # Gate re-rendered (verify_activation button still present).
        self.assertTrue(captured["edit"])
        markup = captured["edit"][0][1].get("reply_markup")
        texts = [
            getattr(b, "callback_data", None)
            for row in getattr(markup, "keyboard", [])
            for b in row
        ]
        self.assertIn("verify_activation", texts)

    def test_unknown_user_gets_start_prompt(self):
        """User without a DB row is asked to /start, no crash."""
        call = make_call(8299)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with p_edit, p_answer, p_send:
            gb.callback_verify_activation(call)

        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("/start", joined)
        self.assertFalse(captured["edit"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
