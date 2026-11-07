"""Shared JWT key persistence, startup lifecycle, and token compatibility tests."""

import os
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app import auth
from app import conversation_store as store


KEY = "a" * 64
OTHER_KEY = "b" * 64


def result(row):
    cursor = Mock()
    cursor.fetchone.return_value = row
    return cursor


class SigningKeyPersistenceTests(unittest.TestCase):
    def test_new_installation_generates_and_reads_persisted_key(self):
        connection = Mock()
        connection.execute.side_effect = [result(None), Mock(), result((KEY,))]

        with patch.dict(os.environ, {"JWT_SECRET": ""}), patch.object(
            store.secrets, "token_hex", return_value=KEY
        ) as generate:
            self.assertEqual(store._load_jwt_signing_key(connection), KEY)

        generate.assert_called_once_with(32)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1], ("jwt_signing_key", KEY)
        )

    def test_stored_key_survives_restart_and_ignores_changed_environment(self):
        connection = Mock()
        connection.execute.return_value = result((KEY,))

        with patch.dict(os.environ, {"JWT_SECRET": "invalid-old-setting"}), patch.object(
            store.secrets, "token_hex"
        ) as generate:
            self.assertEqual(store._load_jwt_signing_key(connection), KEY)
            self.assertEqual(store._load_jwt_signing_key(connection), KEY)

        generate.assert_not_called()
        self.assertTrue(
            all("INSERT" not in call.args[0] for call in connection.execute.call_args_list)
        )

    def test_existing_environment_key_is_migrated_without_rotation(self):
        connection = Mock()
        connection.execute.side_effect = [result(None), Mock(), result((KEY,))]

        with patch.dict(os.environ, {"JWT_SECRET": KEY}), patch.object(
            store.secrets, "token_hex"
        ) as generate:
            self.assertEqual(store._load_jwt_signing_key(connection), KEY)

        generate.assert_not_called()
        self.assertEqual(
            connection.execute.call_args_list[1].args[1], ("jwt_signing_key", KEY)
        )

    def test_short_migration_key_is_rejected_without_persisting(self):
        connection = Mock()
        connection.execute.return_value = result(None)

        with patch.dict(os.environ, {"JWT_SECRET": "too-short"}):
            with self.assertRaisesRegex(RuntimeError, "at least 32"):
                store._load_jwt_signing_key(connection)

        connection.execute.assert_called_once()

    def test_corrupt_stored_key_fails_without_generating_a_replacement(self):
        for value in (None, 123, "", "too-short"):
            with self.subTest(value=value):
                connection = Mock()
                connection.execute.return_value = result((value,))
                with patch.object(store.secrets, "token_hex") as generate:
                    with self.assertRaisesRegex(RuntimeError, "missing or invalid"):
                        store._load_jwt_signing_key(connection)
                generate.assert_not_called()
                connection.execute.assert_called_once()

    def test_insert_conflict_uses_database_winner_instead_of_local_candidate(self):
        connection = Mock()
        connection.execute.side_effect = [result(None), Mock(), result((OTHER_KEY,))]

        with patch.dict(os.environ, {"JWT_SECRET": ""}), patch.object(
            store.secrets, "token_hex", return_value=KEY
        ):
            self.assertEqual(store._load_jwt_signing_key(connection), OTHER_KEY)

        self.assertIn("ON CONFLICT", connection.execute.call_args_list[1].args[0])

    def test_missing_persisted_row_fails_instead_of_using_unstored_candidate(self):
        connection = Mock()
        connection.execute.side_effect = [result(None), Mock(), result(None)]

        with patch.dict(os.environ, {"JWT_SECRET": ""}), patch.object(
            store.secrets, "token_hex", return_value=KEY
        ):
            with self.assertRaisesRegex(RuntimeError, "missing or invalid"):
                store._load_jwt_signing_key(connection)


class SigningKeyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.pool = MagicMock()
        self.connection = self.pool.connection.return_value.__enter__.return_value
        for name, replacement in (
            ("db_pool", self.pool),
            ("checkpointer", Mock()),
            ("memory_store", Mock()),
            ("_jwt_signing_key", None),
        ):
            patcher = patch.object(store, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_initialization_holds_migration_lock_and_only_then_publishes_key(self):
        def load(connection):
            self.assertIs(connection, self.connection)
            statements = [call.args[0] for call in connection.execute.call_args_list]
            self.assertIn("SELECT pg_advisory_lock(728194205)", statements)
            self.assertNotIn("SELECT pg_advisory_unlock(728194205)", statements)
            self.assertTrue(
                any("CREATE TABLE IF NOT EXISTS app_secrets" in sql for sql in statements)
            )
            with self.assertRaises(RuntimeError):
                store.get_jwt_signing_key()
            return KEY

        with patch.object(store, "_load_jwt_signing_key", side_effect=load):
            store.open_store()

        self.pool.open.assert_called_once_with(wait=True)
        self.connection.execute.assert_called_with("SELECT pg_advisory_unlock(728194205)")
        self.assertEqual(store.get_jwt_signing_key(), KEY)

    def test_initialization_failure_clears_old_cache_and_releases_lock(self):
        store._jwt_signing_key = OTHER_KEY
        with patch.object(
            store, "_load_jwt_signing_key", side_effect=RuntimeError("stored key invalid")
        ):
            with self.assertRaisesRegex(RuntimeError, "stored key invalid"):
                store.open_store()

        with self.assertRaises(RuntimeError):
            store.get_jwt_signing_key()
        self.connection.execute.assert_called_with("SELECT pg_advisory_unlock(728194205)")

    def test_close_clears_key_and_closes_pool(self):
        store._jwt_signing_key = KEY
        store.close_store()

        self.pool.close.assert_called_once_with()
        with self.assertRaises(RuntimeError):
            store.get_jwt_signing_key()

    def test_token_issuance_before_initialization_fails_closed(self):
        with self.assertRaises(RuntimeError):
            auth.create_access_token(str(uuid4()))


class SharedKeyTokenTests(unittest.TestCase):
    def test_token_is_accepted_after_another_replica_loads_same_database_key(self):
        user_id = str(uuid4())
        user = {"id": user_id, "email": "user@example.com", "display_name": "User"}
        pool = MagicMock()
        connection = pool.connection.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = user
        database = Mock()
        database.execute.return_value = result((KEY,))

        with patch.object(store, "_jwt_signing_key", KEY), patch.object(auth, "db_pool", pool):
            token = auth.create_access_token(user_id)
            self.assertEqual(jwt.decode(token, KEY, algorithms=["HS256"])["sub"], user_id)
            store._jwt_signing_key = None
            store._jwt_signing_key = store._load_jwt_signing_key(database)
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            self.assertEqual(auth.get_current_user(credentials), user)

        self.assertEqual(cursor.execute.call_args.args[1], (UUID(user_id),))

    def test_token_signed_with_a_different_key_is_rejected_before_user_lookup(self):
        with patch.object(store, "_jwt_signing_key", OTHER_KEY):
            token = auth.create_access_token(str(uuid4()))

        with patch.object(store, "_jwt_signing_key", KEY), patch.object(auth, "db_pool") as pool:
            credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            with self.assertRaises(HTTPException) as raised:
                auth.get_current_user(credentials)

        self.assertEqual(raised.exception.status_code, 401)
        pool.connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
