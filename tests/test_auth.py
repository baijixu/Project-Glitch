import json
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import FakeWS, main


class Authentication(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main._AUTH_FAILURES.clear()
        self.addCleanup(main._AUTH_FAILURES.clear)
        self.handled_ready = []

        async def fake_ready(ws, data):
            self.handled_ready.append(data)

        p = mock.patch.object(main, "_handle_ready", fake_ready)
        p.start()
        self.addCleanup(p.stop)

    async def auth(self, message, remote=("10.0.0.5", 1), token="s3cret"):
        ws = FakeWS(incoming=[message] if message is not None else [], remote=remote)
        return await main._authenticate(ws, token)

    async def test_correct_token_passes_and_ready_is_handled(self):
        self.assertTrue(await self.auth(json.dumps({"type": "ready", "token": "s3cret", "model": "x"})))
        self.assertEqual(len(self.handled_ready), 1)

    async def test_no_token_configured_skips_auth_entirely(self):
        ws = FakeWS()
        self.assertTrue(await main._authenticate(ws, None))
        self.assertTrue(await main._authenticate(ws, ""))

    async def test_everything_else_fails_closed(self):
        for message in (
            json.dumps({"type": "ready", "token": "wrong"}),
            json.dumps({"type": "ready"}),
            json.dumps({"type": "user_text", "token": "s3cret", "text": "hi"}),  # right token, wrong first message
            "not json",
            None,  # connection closes without sending
        ):
            main._AUTH_FAILURES.clear()
            self.assertFalse(await self.auth(message), message)
        self.assertEqual(self.handled_ready, [])

    async def test_repeated_failures_lock_the_ip_out_even_with_the_right_token(self):
        good = json.dumps({"type": "ready", "token": "s3cret"})
        for _ in range(main.AUTH_MAX_FAILURES):
            self.assertFalse(await self.auth(json.dumps({"type": "ready", "token": "nope"})))
        self.assertTrue(main._is_locked_out("10.0.0.5"))
        self.assertFalse(await self.auth(good))  # locked: doesn't even read the message
        self.assertEqual(self.handled_ready, [])

    async def test_lockout_is_per_ip(self):
        for _ in range(main.AUTH_MAX_FAILURES):
            await self.auth(json.dumps({"type": "ready", "token": "nope"}), remote=("10.0.0.5", 1))
        self.assertTrue(await self.auth(json.dumps({"type": "ready", "token": "s3cret"}), remote=("10.0.0.99", 1)))

    async def test_lockout_expires(self):
        for _ in range(main.AUTH_MAX_FAILURES):
            main._record_auth_failure("10.0.0.5")
        self.assertTrue(main._is_locked_out("10.0.0.5"))
        with mock.patch.object(main.time, "monotonic", return_value=main.time.monotonic() + main.AUTH_LOCKOUT_SEC + 1):
            self.assertFalse(main._is_locked_out("10.0.0.5"))

    async def test_success_clears_earlier_failures(self):
        for _ in range(main.AUTH_MAX_FAILURES - 1):
            await self.auth(json.dumps({"type": "ready", "token": "nope"}))
        self.assertTrue(await self.auth(json.dumps({"type": "ready", "token": "s3cret"})))
        self.assertNotIn("10.0.0.5", main._AUTH_FAILURES)


if __name__ == "__main__":
    unittest.main()
