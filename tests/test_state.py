import tempfile
import unittest
from pathlib import Path

from xiaowei_proxy_manager.core.proxy import parse_proxy
from xiaowei_proxy_manager.core.state import StateStore


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

    def test_proxy_pool_picks_random_unlocked_proxy_only(self):
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.json")
            first = parse_proxy("proxy1.example:8888:user:pass1")
            second = parse_proxy("proxy2.example:8888:user:pass2")
            state.set_proxy_pool([first, second])

            selected = state.next_proxy_from_pool(avoid_raw={first.raw}, mark_used_by="phone-1")
            self.assertEqual(selected.raw, second.raw)
            locked = state.next_proxy_from_pool(
                avoid_raw={first.raw, second.raw},
                mark_used_by="phone-2",
            )
            self.assertIsNone(locked)


if __name__ == "__main__":
    unittest.main()
