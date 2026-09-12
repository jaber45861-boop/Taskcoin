"""Tests for /api/profile endpoint."""

import hashlib
import hmac
import sqlite3
import tempfile
import time
import unittest


class TestApiProfile(unittest.TestCase):
    """Verify /api/profile returns the same data as callback_profile."""

    @classmethod
    def setUpClass(cls):
        cls.db_path = tempfile.mktemp(suffix=".db")
        conn = sqlite3.connect(cls.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                points INTEGER DEFAULT 0,
                balance_cents INTEGER DEFAULT 0,
                balance_usd_nano INTEGER DEFAULT 0,
                activation_status INTEGER DEFAULT 0,
                activation_date TEXT,
                referred_by INTEGER,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                balance_usd_nano_rate TEXT,
                balance_usd_nano_migrated_at TEXT,
                migration_meta_json TEXT
            );
            CREATE TABLE referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                reward_status TEXT DEFAULT 'pending'
            );
            CREATE TABLE smm_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                service_key TEXT,
                smm_order_id TEXT,
                link TEXT,
                quantity INTEGER,
                amount_cents INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE ad_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                file_id TEXT,
                reward_cents INTEGER,
                status TEXT,
                reviewed_at TEXT
            );
            CREATE TABLE processed_transactions (
                idempotency_key TEXT PRIMARY KEY,
                user_id INTEGER,
                amount_cents INTEGER
            );
            CREATE TABLE manual_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                task_link TEXT,
                task_type TEXT,
                target_reference TEXT,
                task_instructions TEXT,
                reward_points INTEGER DEFAULT 0,
                quantity_requested INTEGER DEFAULT 0,
                quantity_remaining INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT,
                task_state TEXT DEFAULT 'AVAILABLE',
                task_origin TEXT DEFAULT 'internal',
                advertiser_id INTEGER,
                total_cost_nano INTEGER
            );
            CREATE TABLE referral_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_id INTEGER,
                referral_link TEXT,
                quantity_requested INTEGER DEFAULT 0,
                quantity_remaining INTEGER DEFAULT 0,
                points_spent INTEGER DEFAULT 0,
                amount_cents INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT,
                task_state TEXT DEFAULT 'AVAILABLE',
                task_origin TEXT DEFAULT 'internal'
            );
            CREATE TABLE smm_services (
                service_key TEXT PRIMARY KEY,
                smm_service_id INTEGER,
                name TEXT,
                category TEXT,
                emoji TEXT,
                platform TEXT,
                base_cost INTEGER DEFAULT 0,
                default_quantity INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            );
            CREATE TABLE service_price_settings (
                service_key TEXT PRIMARY KEY,
                price_cents INTEGER DEFAULT 0,
                quantity INTEGER DEFAULT 0
            );
            CREATE TABLE cpalead_conversions (
                subid TEXT,
                lead_id TEXT UNIQUE,
                campaign_id TEXT,
                campaign_name TEXT,
                payout REAL
            );
        """)
        conn.execute(
            "INSERT INTO users "
            "(user_id, first_name, last_name, username, balance_usd_nano, "
            " activation_status, joined_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (12345, "John", "Doe", "johndoe", 50_000_000_000, 1, "2025-01-01"),
        )
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status) "
            "VALUES (12345, 111, 'rewarded')"
        )
        conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, reward_status) "
            "VALUES (12345, 222, 'rewarded')"
        )
        conn.execute(
            "INSERT INTO smm_orders (user_id, service_key) VALUES (12345, 'ig_followers')"
        )
        conn.execute(
            "INSERT INTO smm_orders (user_id, service_key) VALUES (12345, 'yt_views')"
        )
        conn.execute(
            "INSERT INTO smm_orders (user_id, service_key) VALUES (12345, 'tw_followers')"
        )
        conn.commit()
        conn.close()

        # Build session token
        cls.session_secret = "test_secret"
        payload = f"12345:{int(time.time())}"
        sig = hmac.new(
            cls.session_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        cls.token = f"{payload}:{sig}"

        # Register the Flask app
        from flask import Flask
        from reward_api import register_reward_api

        cls.app = Flask(__name__)

        def get_conn():
            c = sqlite3.connect(cls.db_path)
            c.row_factory = sqlite3.Row
            return c

        def get_user(uid):
            c = sqlite3.connect(cls.db_path)
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT * FROM users WHERE user_id = ?", (uid,)
            ).fetchone()
            c.close()
            return row

        def get_referral_count(uid):
            c = sqlite3.connect(cls.db_path)
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT COUNT(*) AS cnt FROM referrals "
                "WHERE referrer_id = ? AND reward_status = 'rewarded'",
                (uid,),
            ).fetchone()
            c.close()
            return row["cnt"] if row else 0

        def get_user_orders(uid, limit=5):
            c = sqlite3.connect(cls.db_path)
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT * FROM smm_orders WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (uid, limit),
            ).fetchall()
            c.close()
            return rows

        register_reward_api(
            cls.app,
            get_connection=get_conn,
            get_user=get_user,
            get_ad_reward=lambda: 50,
            account_access_allowed=lambda uid: True,
            bot_token="test:fake",
            api_secret="test_api",
            session_secret=cls.session_secret,
            db_path=cls.db_path,
            monetag_zone_id="",
            allowed_origins="*",
            egp_per_usd=50.0,
            get_referral_count=get_referral_count,
            get_user_orders=get_user_orders,
        )

    @classmethod
    def tearDownClass(cls):
        import os
        if os.path.exists(cls.db_path):
            os.unlink(cls.db_path)

    def test_unauthenticated_returns_401(self):
        r = self.app.test_client().get("/api/profile")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json()["error"], "unauthorized")

    def test_profile_fields(self):
        r = self.app.test_client().get(
            "/api/profile",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()

        self.assertEqual(data["user_id"], 12345)
        self.assertEqual(data["first_name"], "John")
        self.assertEqual(data["last_name"], "Doe")
        self.assertEqual(data["username"], "johndoe")
        self.assertEqual(data["full_name"], "John Doe")
        self.assertEqual(data["balance_usd_nano"], 50_000_000_000)
        self.assertIsInstance(data["balance_usd"], float)
        self.assertAlmostEqual(data["balance_usd"], 50.0, places=9)
        self.assertEqual(data["referral_count"], 2)
        self.assertEqual(data["orders_count"], 3)
        self.assertIsNotNone(data["joined_at"])

    def test_profile_no_last_name(self):
        """User with no last_name should have null last_name and first_name only."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO users "
            "(user_id, first_name, username, balance_usd_nano, activation_status) "
            "VALUES (99999, 'Alice', 'alice', 10000000, 1)"
        )
        conn.commit()
        conn.close()

        # Create token for user 99999
        payload = f"99999:{int(time.time())}"
        sig = hmac.new(
            self.session_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        token = f"{payload}:{sig}"

        r = self.app.test_client().get(
            "/api/profile",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["full_name"], "Alice")
        self.assertIsNone(data["last_name"])
        self.assertEqual(data["referral_count"], 0)
        self.assertEqual(data["orders_count"], 0)


if __name__ == "__main__":
    unittest.main()
