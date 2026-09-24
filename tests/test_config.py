"""D2: uji konfigurasi dan lifecycle tanpa koneksi MongoDB asli."""

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config import DatabaseConfig
from main import Transaction, app, lifespan


class DatabaseConfigTests(unittest.TestCase):
    def test_missing_or_blank_settings_fail_clearly(self):
        for env in ({}, {"MONGODB_URI": " ", "MONGODB_DATABASE": "bootcamp"},
                    {"MONGODB_URI": "mongodb://localhost:27017"}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, "Environment variable wajib diisi"):
                    DatabaseConfig.from_env()

    def test_atlas_and_self_hosted_use_same_configuration(self):
        for uri in ("mongodb+srv://user:example@cluster.example.net/",
                    "mongodb://mongo:27017/?authSource=admin"):
            with self.subTest(uri=uri), patch.dict(os.environ, {
                "MONGODB_URI": uri, "MONGODB_DATABASE": "database_pilihan",
            }, clear=True):
                settings = DatabaseConfig.from_env()
                self.assertEqual(settings.uri, uri)
                self.assertEqual(settings.database, "database_pilihan")
                self.assertNotIn(uri, repr(settings))

    def test_invalid_uri_error_does_not_echo_credentials(self):
        with patch.dict(os.environ, {
            "MONGODB_URI": "https://user:private-password@host",
            "MONGODB_DATABASE": "bootcamp",
        }, clear=True):
            with self.assertRaises(RuntimeError) as error:
                DatabaseConfig.from_env()
            self.assertNotIn("private-password", str(error.exception))


class DatabaseLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_selection_and_connection_cleanup(self):
        for fails in (False, True):
            with self.subTest(startup_fails=fails):
                client = MagicMock()
                client.close = AsyncMock()
                init = AsyncMock(side_effect=RuntimeError("test failure") if fails else None)
                with patch.dict(os.environ, {
                    "MONGODB_URI": "mongodb://localhost:27017",
                    "MONGODB_DATABASE": "database_pilihan",
                }, clear=True), patch("main.AsyncMongoClient", return_value=client) as factory, \
                        patch("main.init_beanie", init):
                    if fails:
                        with self.assertRaisesRegex(RuntimeError, "test failure"):
                            async with lifespan(app):
                                self.fail("Startup should fail before serving requests")
                    else:
                        async with lifespan(app):
                            client.close.assert_not_awaited()
                    factory.assert_called_once_with(
                        "mongodb://localhost:27017", serverSelectionTimeoutMS=10000)
                    client.__getitem__.assert_called_once_with("database_pilihan")
                    init.assert_awaited_once_with(
                        database=client.__getitem__.return_value, document_models=[Transaction])
                    client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
