"""Quick integration test for the messaging system flow."""
import os
import sys
import tempfile
import unittest
from decimal import Decimal

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:TEST")
os.environ.setdefault("API_SECRET", "")
os.environ.setdefault("SESSION_SECRET", "")

import importlib.util as _ilu
from pathlib import Path

_BOT = Path(__file__).resolve().parent.parent / "ganaihat_bot.py"
spec = _ilu.spec_from_file_location("ganaihat_bot", str(_BOT))
mod = _ilu.module_from_spec(spec)
mod.EGP_PER_USD = Decimal("50")
spec.loader.exec_module(mod)

import reward_api
reward_api._live_egp_per_usd = Decimal("50")


class TestMessagingFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.DB_PATH = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        mod.DB_PATH = cls.DB_PATH
        mod.init_db()

    @classmethod
    def tearDownClass(cls):
        try:
            mod.get_connection().close()
        except Exception:
            pass
        os.unlink(cls.DB_PATH)

    def setUp(self):
        with mod.get_connection() as conn:
            conn.execute("DELETE FROM user_inquiries")
            conn.commit()

    def test_01_save_inquiry(self):
        iid = mod.save_user_inquiry(12345, "withdrawals", "عايز أسحب")
        self.assertGreater(iid, 0)

    def test_02_unread_counts(self):
        mod.save_user_inquiry(12345, "withdrawals", "test")
        counts = mod.get_unread_inquiry_counts()
        self.assertEqual(counts.get("withdrawals", 0), 1)

    def test_03_get_by_category(self):
        mod.save_user_inquiry(12345, "withdrawals", "msg1")
        mod.save_user_inquiry(12345, "topup", "msg2")
        inqs = mod.get_inquiries_by_category("withdrawals")
        self.assertEqual(len(inqs), 1)
        self.assertEqual(inqs[0]["message"], "msg1")

    def test_04_admin_reply(self):
        iid = mod.save_user_inquiry(12345, "general", "question")
        ok = mod.save_admin_reply(iid, "answer here")
        self.assertTrue(ok)
        inq = mod.get_inquiry_by_id(iid)
        self.assertEqual(inq["admin_reply"], "answer here")
        self.assertEqual(inq["is_read_by_user"], 0)

    def test_05_user_unread_reply_counts(self):
        iid = mod.save_user_inquiry(12345, "general", "q")
        mod.save_admin_reply(iid, "a")
        counts = mod.get_unread_reply_counts(12345)
        self.assertEqual(counts.get("general", 0), 1)

    def test_06_get_replies_for_user(self):
        iid = mod.save_user_inquiry(12345, "tasks", "help")
        mod.save_admin_reply(iid, "ok")
        replies = mod.get_replies_for_user(12345, "tasks")
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["admin_reply"], "ok")

    def test_07_mark_replies_read(self):
        iid = mod.save_user_inquiry(12345, "orders", "q")
        mod.save_admin_reply(iid, "a")
        mod.mark_replies_read_for_user(12345, "orders")
        counts = mod.get_unread_reply_counts(12345)
        self.assertEqual(counts.get("orders", 0), 0)

    def test_08_mark_inquiry_read(self):
        iid = mod.save_user_inquiry(12345, "withdrawals", "q")
        mod.mark_inquiry_read(iid)
        counts = mod.get_unread_inquiry_counts()
        self.assertEqual(counts.get("withdrawals", 0), 0)

    def test_09_multiple_categories(self):
        mod.save_user_inquiry(1, "withdrawals", "w1")
        mod.save_user_inquiry(2, "topup", "t1")
        mod.save_user_inquiry(3, "orders", "o1")
        counts = mod.get_unread_inquiry_counts()
        self.assertEqual(counts["withdrawals"], 1)
        self.assertEqual(counts["topup"], 1)
        self.assertEqual(counts["orders"], 1)

    def test_10_full_flow(self):
        # User sends message
        iid = mod.save_user_inquiry(111, "general", " عندي مشكلة")
        # Admin sees unread
        self.assertEqual(mod.get_unread_inquiry_counts().get("general"), 1)
        # Admin replies
        mod.save_admin_reply(iid, " هنحلها")
        # User sees unread reply
        self.assertEqual(mod.get_unread_reply_counts(111).get("general"), 1)
        # User reads reply
        mod.mark_replies_read_for_user(111, "general")
        self.assertEqual(mod.get_unread_reply_counts(111).get("general", 0), 0)
        # Admin marks inquiry read
        mod.mark_inquiry_read(iid)
        self.assertEqual(mod.get_unread_inquiry_counts().get("general", 0), 0)


if __name__ == "__main__":
    unittest.main()
