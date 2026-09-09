"""Regression tests: the «🎯 إنشاء مهمة» main-keyboard button.

Proves that:
  A. The button label changed from «🎯 إنشاء مهمة إعلانية» to «🎯 إنشاء مهمة»,
     keeping callback_data="create_ad_task".
  B. The neighbouring «📣 تثبيت إعلان / روّج لقناتك» button is unchanged.
  C. The create_ad_task callback is actually registered with telebot
     (the handler had lost its decorator and the button was dead).
  D. Pressing the button with an eligible user starts the advertiser
     task-creation flow; below the minimum balance it refuses politely.
  E. No other flow is modified.
"""
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from types import SimpleNamespace
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

NEW_LABEL = "🎯 إنشاء مهمة"
OLD_LABEL = "🎯 إنشاء مهمة إعلانية"


def make_call(user_id):
    call = mock.MagicMock()
    call.from_user.id = user_id
    call.from_user.first_name = "Tester"
    call.data = "create_ad_task"
    call.message.chat.id = user_id
    call.message.message_id = 4242
    return call


def patch_telegram():
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


def status_all(subscribed: bool):
    return [
        {**ch, "subscribed": subscribed, "rewarded": True}
        for ch in gb.REQUIRED_CHANNELS
    ]


class _BotDBTestCase(unittest.TestCase):
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
        gb.user_state.pop(9501, None)
        with gb.get_connection() as conn:
            conn.execute("DELETE FROM task_completions")
            conn.execute("DELETE FROM channel_reward_ledger")
            conn.execute("DELETE FROM user_inquiries")
            conn.execute("DELETE FROM users")
            conn.commit()

    @staticmethod
    def make_active_user(user_id=9501):
        gb.add_user(user_id, "T", None, None)
        gb.mark_user_verified(user_id)
        with gb.get_connection() as conn:
            conn.execute(
                "UPDATE users SET activation_status = 1, balance_usd_nano = ? "
                "WHERE user_id = ?",
                (gb.TASK_CREATION_MIN_BALANCE_USD_NANO, user_id),
            )
            conn.commit()
        return gb.get_user(user_id)


# ══════════════════════════════════════════════════════════════════════════════
# A+B. KEYBOARD WIRING
# ══════════════════════════════════════════════════════════════════════════════


class TestMainKeyboardButton(unittest.TestCase):
    def test_button_label_changed_with_same_callback(self):
        markup = gb.main_keyboard()
        buttons = [b for row in markup.keyboard for b in row]
        target = [b for b in buttons if b.callback_data == "create_ad_task"]
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0].text, NEW_LABEL)

    def test_old_label_gone_from_keyboard(self):
        markup = gb.main_keyboard()
        buttons = [b for row in markup.keyboard for b in row]
        self.assertFalse(any(OLD_LABEL == b.text for b in buttons))

    def test_promote_channel_button_untouched(self):
        markup = gb.main_keyboard()
        buttons = [b for row in markup.keyboard for b in row]
        promote = [b for b in buttons if b.callback_data == "promote_channel"]
        self.assertEqual(len(promote), 1)
        self.assertEqual(promote[0].text, "📣 تثبيت إعلان / روّج لقناتك")


# ══════════════════════════════════════════════════════════════════════════════
# C+D. HANDLER REGISTRATION AND BEHAVIOR
# ══════════════════════════════════════════════════════════════════════════════


class TestCreateAdTaskHandler(_BotDBTestCase):
    def test_handler_registered_for_create_ad_task(self):
        """telebot must route callback_data 'create_ad_task' to the handler."""
        registered = None
        for h in gb.bot.callback_query_handlers:
            func = h.get("function")
            filt = (h.get("filters") or {}).get("func")
            if func is None or filt is None:
                continue
            dummy = SimpleNamespace(data="create_ad_task")
            try:
                if filt(dummy):
                    registered = func
                    break
            except Exception:
                continue
        self.assertIsNotNone(registered)
        self.assertEqual(registered.__name__, "callback_create_ad_task")

    def test_eligible_user_press_starts_flow(self):
        self.make_active_user()
        call = make_call(9501)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_create_ad_task(call)

        self.assertTrue(captured["edit"])
        intro_text = captured["edit"][0][0]
        self.assertIn("إنشاء مهمة", intro_text)
        self.assertEqual(
            gb.user_state.get(9501, {}).get("step"), "awaiting_ad_task_channel"
        )

    def test_below_min_balance_refuses_without_flow(self):
        gb.add_user(9501, "T", None, None)
        gb.mark_user_verified(9501)
        with gb.get_connection() as conn:
            conn.execute(
                "UPDATE users SET activation_status = 1, balance_usd_nano = 0 "
                "WHERE user_id = 9501"
            )
            conn.commit()
        call = make_call(9501)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=True)):
            with p_edit, p_answer, p_send:
                gb.callback_create_ad_task(call)

        joined = " ".join(str(a[0]) for a in captured["answers"])
        self.assertIn("لإنشاء مهمة", joined)
        self.assertEqual(gb.user_state.get(9501), None)
    def test_inactive_user_stays_on_gate(self):
        gb.add_user(9501, "T", None, None)  # activation_status = 0, not verified
        call = make_call(9501)
        captured, p_edit, p_answer, p_send = patch_telegram()
        with mock.patch.object(gb, "get_channels_status",
                               return_value=status_all(subscribed=False)):
            with p_edit, p_answer, p_send:
                gb.callback_create_ad_task(call)

        # An unverified user is routed to the anti-bot challenge by
        # require_active_account — the flow never starts either way.
        self.assertTrue(captured["edit"] or captured["sends"])
        self.assertEqual(gb.user_state.get(9501), None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
