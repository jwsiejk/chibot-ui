import json
import unittest

from app.db.admin_settings import AdminSettingsStore


class _FakeCursor:
    def __init__(self, row):
        self._row = row
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


class AdminSettingsStoreTest(unittest.TestCase):
    def test_get_reads_value_jsonb_column(self) -> None:
        payload = json.dumps({"enabled": True})
        cursor = _FakeCursor((payload,))
        connection = _FakeConnection(cursor)

        store = AdminSettingsStore(conn_factory=lambda: connection)
        value = store.get("audio_guardrails")

        self.assertEqual(connection.closed, True)
        self.assertTrue(cursor.executed, "expected a SELECT to run")
        query, params = cursor.executed[0]
        self.assertIn("value_jsonb", query.lower())
        self.assertNotIn("settings_value", query.lower())
        self.assertNotIn(" value ", query.lower().replace("value_jsonb", ""))
        self.assertEqual(params, ("audio_guardrails",))
        self.assertEqual(value, {"enabled": True})


if __name__ == "__main__":  # pragma: no cover - unittest entrypoint
    unittest.main()
