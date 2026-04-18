"""Tests for the signal-ephemeral monkey-patch."""

import asyncio
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from signal_ephemeral import (
    apply_signal_ephemeral_patch,
    is_progress_message,
)


@dataclass
class FakeSendResult:
    success: bool
    message_id: str | None = None
    error: str | None = None


class FakeSignalAdapter:
    def __init__(self):
        self.account = "+15551234567"
        self._timestamps = iter(range(1001, 2000))
        self.rpc_calls: list = []
        self.deleted: list = []

    async def _rpc(self, method, params, rpc_id=None):
        self.rpc_calls.append((method, dict(params), rpc_id))
        if method == "send":
            return {"timestamp": next(self._timestamps)}
        if method == "remoteDelete":
            return {}
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        result = await self._rpc("send", {"account": self.account, "message": content})
        if result is not None:
            ts = result.get("timestamp")
            return FakeSendResult(success=True, message_id=str(ts) if ts else None)
        return FakeSendResult(success=False, error="RPC send failed")


class TestProgressDetection(unittest.TestCase):

    def test_emoji_tool_progress(self):
        self.assertTrue(is_progress_message("⚡ terminal..."))
        self.assertTrue(is_progress_message("🔧 execute_code: running"))
        self.assertTrue(is_progress_message("📝 write_file..."))

    def test_plain_text_not_progress(self):
        self.assertFalse(is_progress_message("Here is your answer."))
        self.assertFalse(is_progress_message("The result is 42."))
        self.assertFalse(is_progress_message(None))
        self.assertFalse(is_progress_message(""))


class TestEphemeralPatch(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        class PatchableAdapter(FakeSignalAdapter):
            pass
        self.adapter_cls = PatchableAdapter

    def patch(self, *, config=None):
        cfg = config or {"plugins": {"signal-ephemeral": {"enabled": True, "cleanup_mode": "delete"}}}
        apply_signal_ephemeral_patch(self.adapter_cls, config_provider=lambda: cfg)

    async def test_idempotent(self):
        self.patch()
        first_send = self.adapter_cls.send
        self.patch()
        self.assertIs(first_send, self.adapter_cls.send)

    async def test_delete_message_calls_remote_delete(self):
        self.patch()
        adapter = self.adapter_cls()
        ok = await adapter.delete_message("chat-1", "1001")
        self.assertTrue(ok)
        method, params, _ = adapter.rpc_calls[-1]
        self.assertEqual("remoteDelete", method)
        self.assertEqual(1001, params["targetTimestamp"])
        self.assertEqual(["chat-1"], params["recipient"])

    async def test_delete_message_group(self):
        self.patch()
        adapter = self.adapter_cls()
        await adapter.delete_message("group:abc123", "1001")
        _, params, _ = adapter.rpc_calls[-1]
        self.assertEqual("abc123", params["groupId"])
        self.assertNotIn("recipient", params)

    async def test_progress_messages_deleted_on_real_reply(self):
        self.patch()
        adapter = self.adapter_cls()

        # Send progress messages
        await adapter.send("chat-1", "⚡ terminal...")
        await adapter.send("chat-1", "🔧 execute_code: running")

        # Send real reply — triggers cleanup
        await adapter.send("chat-1", "Here is your answer.")

        # Let the cleanup task run
        await asyncio.sleep(0.05)

        # Verify remoteDelete was called for both progress messages
        delete_calls = [c for c in adapter.rpc_calls if c[0] == "remoteDelete"]
        self.assertEqual(2, len(delete_calls))
        deleted_timestamps = {c[1]["targetTimestamp"] for c in delete_calls}
        self.assertEqual({1001, 1002}, deleted_timestamps)

    async def test_no_cleanup_when_disabled(self):
        self.patch(config={"plugins": {"signal-ephemeral": {"cleanup_mode": "off"}}})
        adapter = self.adapter_cls()

        await adapter.send("chat-1", "⚡ terminal...")
        await adapter.send("chat-1", "Real reply.")
        await asyncio.sleep(0.05)

        delete_calls = [c for c in adapter.rpc_calls if c[0] == "remoteDelete"]
        self.assertEqual(0, len(delete_calls))


if __name__ == "__main__":
    unittest.main()
