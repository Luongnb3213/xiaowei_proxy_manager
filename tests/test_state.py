import tempfile
import unittest
from pathlib import Path

from xiaowei_proxy_manager.state import StateStore


class StateStoreTests(unittest.TestCase):
    def test_rollback_to_no_proxy_is_distinct_from_no_history(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.json")
            self.assertIsNone(state.rollback_target("phone-1"))

            state.record_change("phone-1", before=None, after="host:1234", label="host:1234:u:***")
            self.assertEqual(state.rollback_target("phone-1"), "")

            state.save()
            reloaded = StateStore(Path(directory) / "state.json")
            self.assertEqual(reloaded.rollback_target("phone-1"), "")

    def test_state_does_not_store_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = StateStore(path)
            state.record_change(
                "phone-1",
                before=None,
                after="host:1234",
                label="host:1234:user:***",
            )
            state.save()
            self.assertNotIn("secret-password", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

