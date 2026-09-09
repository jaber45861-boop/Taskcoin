"""Regression tests: callback_anti_bot success flow.

Proves that after a successful anti-bot answer the handler:
  A. No longer calls account_access_allowed as a gate — the user proceeds
     into the mandatory subscription / activation flow.
  B. Checks actual membership in ALL mandatory channels first via
     get_channels_status.
  C. If channels are missing: stays on the activation/subscription gate.
  D. If subscribed to everything:
       - first activation (activate_user: bonus + referral release) then
         main_keyboard;
       - a penalized (frozen) account is unfrozen without an activation
         bonus;
       - an already-active account opens the main keyboard unchanged.
  E. The wrong-answer branch still re-renders the anti-bot challenge.
"""
import os
import sys
import tempfile
import time
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


def make_antibot_call(user_id, choice):
    """Build a minimal fake Telegram callback object for an antibot_ press."""
    call = mock.MagicMock()
    call.from_user.id = user_id
    call.from_user.first_name = "Tester"
    call.data = f"antibot_{choice}"
    call.message.chat.id = user_id
    call.message.message_id = 4242
    return call


def seed_session(user_id, answer=7):
    """Seed an active anti-bot session without generating a challenge."""
    gb.anti_bot_sessions[user_id] = {
        "answer": answer,
        "expires_at": time.time() + 60,
        "attempts": 0,
        "question": "3+4",
        "options": [6, 7, 8, 9],
    }


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


def markup_callback_texts(kwargs):
    """Extract callback_data strings from a captured reply_markup kwarg."""
    markup = kwargs.get("reply_markup")
    return [
        getattr(b, "callback_data", None)
        for row in getattr(markup, "keyboard", [])
        for b in row
    ]


def assert_has_main_keyboard(self, edit_kwargs):
    """main_keyboard() has no callback_data — verify by structure, not markers."""
    texts = markup_callback_texts(edit_kwargs)
    self.assertTrue(texts)
    self.assertTrue(all(t not in (None, "verify_activation") for t in texts))
    self.assertNotIn("verify_activation", texts)


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
        gb.anti_bot_sessions.clear()
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


class TestAntiBotWiring(unittest.TestCase):
    def test_no_account_access_allowed_gate(self):
        """The handler must NOT use account_access_allowed as a gate."""
        src = inspect.getsource(gb.callback_anti_bot)
        self.assertNotIn("account_access_allowed", src)

    def test_success_path_enters_activation_flow(self):
        """Success path checks all channels and reaches activation/main_keyboard."""
        src = inspect.getsource(gb.callback_anti_bot)
        self.assertIn("mark_user_verified", src)
        self.assertIn("get_channels_status(user_id)", src)
        self.assertIn("not_subbed", src)
        self.assertIn("show_activation_gate", src)
        self.assertIn("activate_user(user_id)", src)
        self.assertIn("main_keyboard", src)


# ══════════════════════════════════════════════════════════════════════════════
# B. BEHAVIOR — SUBSCRIBED TO EVERYTHING → ACTIVATION → MAIN KEYBOARD
# ══════════════════════════════════════════════════════════════════════════════


class TestAntiBotSuccessActivates(_BotDBTestCase):
    def test_new_user_subscribed_everywhere_gets_first_activation(self):
        """Fresh verified user, member of every channel → activate_user + main keyboard."""
        gb.add_user(9101, "T", None, None)
        self.assertFalse(gb.is_account_active(9101))
        balance_before = gb.get_balance_usd_nano(9101)
        seed_session(9101, answer=7)

        call = make_antibot_call(9101, choice=7)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_anti_bot(call)

        self.assertTrue(gb.is_user_verified(9101))
        self.assertTrue(gb.is_account_active(9101))
        self.assertEqual(
            gb.get_balance_usd_nano(9101) - balance_before,
            gb.ACTIVATION_REWARD_USD_NANO,
        )
        self.assertTrue(captured["edit"])
        assert_has_main_keyboard(self, captured["edit"][0][1])
        # Activation bonus announced in the callback answer.
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("تفعيل", joined)

    def test_already_active_user_opens_main_keyboard_without_bonus(self):
        """Already-active account: main keyboard, balance untouched."""
        gb.add_user(9102, "T", None, None)
        with gb.get_connection() as conn:
            conn.execute(
                "UPDATE users SET activation_status = 1 WHERE user_id = 9102"
            )
            conn.commit()
        balance_before = gb.get_balance_usd_nano(9102)
        seed_session(9102, answer=7)

        call = make_antibot_call(9102, choice=7)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_anti_bot(call)

        self.assertTrue(gb.is_account_active(9102))
        self.assertEqual(gb.get_balance_usd_nano(9102), balance_before)
        self.assertTrue(captured["edit"])
        assert_has_main_keyboard(self, captured["edit"][0][1])

    def test_penalized_user_gets_unfrozen_without_activation_bonus(self):
        """Frozen (penalized) user who re-subscribed: unfrozen, no first-activation bonus."""
        gb.add_user(9103, "T", None, None)
        # Go through the real penalty path so a deducted ledger row exists.
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=False, rewarded=True)):
            gb.enforce_channel_subscriptions(9103)
        with gb.get_connection() as conn:
            conn.execute("UPDATE users SET activation_status = 0 WHERE user_id = 9103")
            conn.commit()
        self.assertFalse(gb.is_account_active(9103))
        balance_before = gb.get_balance_usd_nano(9103)
        seed_session(9103, answer=7)

        call = make_antibot_call(9103, choice=7)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_anti_bot(call)

        self.assertTrue(gb.is_account_active(9103))
        # No first-activation bonus for penalized re-activations.
        self.assertEqual(gb.get_balance_usd_nano(9103), balance_before)
        self.assertTrue(captured["edit"])


# ══════════════════════════════════════════════════════════════════════════════
# C. BEHAVIOR — MISSING CHANNELS → STAY ON GATE
# ══════════════════════════════════════════════════════════════════════════════


class TestAntiBotStaysOnGate(_BotDBTestCase):
    def test_missing_channel_keeps_gate_and_account_inactive(self):
        """Channels missing: activation gate re-rendered, no activation."""
        gb.add_user(9201, "T", None, None)
        seed_session(9201, answer=7)

        call = make_antibot_call(9201, choice=7)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=False)):
            with p_edit, p_answer, p_send:
                gb.callback_anti_bot(call)

        self.assertTrue(gb.is_user_verified(9201))
        self.assertFalse(gb.is_account_active(9201))
        self.assertTrue(captured["edit"])
        texts = markup_callback_texts(captured["edit"][0][1])
        self.assertIn("verify_activation", texts)

    def test_unknown_user_passes_antibot_then_hits_gate(self):
        """No DB row: the handler degrades gracefully to the activation gate."""
        seed_session(9301, answer=7)

        call = make_antibot_call(9301, choice=7)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=False)):
            with p_edit, p_answer, p_send:
                gb.callback_anti_bot(call)

        # No DB row was invented; the handler did not crash and the
        # activation/subscription gate rendered in place of the challenge.
        self.assertIsNone(gb.get_user(9301))
        self.assertTrue(captured["edit"])
        texts = markup_callback_texts(captured["edit"][0][1])
        self.assertIn("verify_activation", texts)
        self.assertFalse(gb.is_account_active(9301))


# ══════════════════════════════════════════════════════════════════════════════
# E. WRONG ANSWER — CHALLENGE FLOW UNCHANGED
# ══════════════════════════════════════════════════════════════════════════════


class TestAntiBotWrongAnswerUnchanged(_BotDBTestCase):
    def test_wrong_answer_re_renders_challenge_not_gate(self):
        """A wrong choice keeps the anti-bot challenge; no gate, no main keyboard."""
        gb.add_user(9401, "T", None, None)
        seed_session(9401, answer=7)

        call = make_antibot_call(9401, choice=6)  # wrong
        captured, p_edit, p_answer, p_send = patch_telegram()
        with p_edit, p_answer, p_send:
            gb.callback_anti_bot(call)

        self.assertFalse(gb.is_account_active(9401))
        self.assertTrue(captured["edit"])
        texts = markup_callback_texts(captured["edit"][0][1])
        self.assertTrue(all(t and t.startswith("antibot_") for t in texts))
        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("إجابة خاطئة", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
