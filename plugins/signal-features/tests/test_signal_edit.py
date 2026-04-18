"""Tests for the signal-edit monkey-patch."""

import asyncio
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

# Ensure the plugin package is importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from signal_edit import apply_signal_edit_patch


@dataclass
class FakeSendResult:
    success: bool
    message_id: str | None = None
    error: str | None = None


class FakeSignalAdapter:
    """Minimal stub replicating the surface area used by the patch."""

    def __init__(self):
        self.account = "+15551234567"
        self._timestamps = iter(range(1001, 2000))
        self.rpc_calls: list = []
        self.typing_stopped_for: list = []
        self.force_rpc_failure = False

    async def _stop_typing_indicator(self, chat_id):
        self.typing_stopped_for.append(chat_id)

    async def _rpc(self, method, params, rpc_id=None):
        self.rpc_calls.append((method, dict(params), rpc_id))
        if method == "send" and not self.force_rpc_failure:
            return {"timestamp": next(self._timestamps)}
        if self.force_rpc_failure:
            return None
        return None

    def _track_sent_timestamp(self, rpc_result):
        pass  # no-op for tests

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        result = await self._rpc("send", {"account": self.account, "message": content})
        if result is not None:
            ts = result.get("timestamp")
            return FakeSendResult(success=True, message_id=str(ts) if ts else None)
        return FakeSendResult(success=False, error="RPC send failed")


class TestSignalEditPatch(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        class PatchableAdapter(FakeSignalAdapter):
            pass
        self.adapter_cls = PatchableAdapter

    def patch(self):
        apply_signal_edit_patch(self.adapter_cls)

    async def test_idempotent(self):
        self.patch()
        first_edit = self.adapter_cls.edit_message
        self.patch()
        self.assertIs(first_edit, self.adapter_cls.edit_message)

    async def test_edit_sends_edit_timestamp_for_dm(self):
        self.patch()
        adapter = self.adapter_cls()
        result = await adapter.edit_message("chat-1", "1001", "updated text")

        self.assertTrue(result.success)
        method, params, _ = adapter.rpc_calls[-1]
        self.assertEqual("send", method)
        self.assertEqual(1001, params["editTimestamp"])
        self.assertEqual("updated text", params["message"])
        self.assertEqual(["chat-1"], params["recipient"])
        self.assertNotIn("groupId", params)

    async def test_edit_sends_group_id_for_group_chat(self):
        self.patch()
        adapter = self.adapter_cls()
        result = await adapter.edit_message("group:abc123", "1001", "group update")

        self.assertTrue(result.success)
        _, params, _ = adapter.rpc_calls[-1]
        self.assertEqual("abc123", params["groupId"])
        self.assertNotIn("recipient", params)

    async def test_successive_edits_chain(self):
        self.patch()
        adapter = self.adapter_cls()

        # First edit targets the original timestamp
        r1 = await adapter.edit_message("chat-1", "1001", "step 1")
        self.assertEqual("1001", r1.message_id)

        # Second edit of same original message targets the new timestamp
        r2 = await adapter.edit_message("chat-1", "1001", "step 2")
        self.assertEqual("1002", r2.message_id)

        # Verify the editTimestamp values
        self.assertEqual(1001, adapter.rpc_calls[0][1]["editTimestamp"])
        self.assertEqual(1001, adapter.rpc_calls[1][1]["editTimestamp"])

    async def test_edit_failure_returns_error(self):
        self.patch()
        adapter = self.adapter_cls()
        adapter.force_rpc_failure = True

        result = await adapter.edit_message("chat-1", "1001", "will fail")
        self.assertFalse(result.success)
        self.assertEqual("RPC edit failed", result.error)

    async def test_edit_stops_typing(self):
        self.patch()
        adapter = self.adapter_cls()
        await adapter.edit_message("chat-1", "1001", "text")
        self.assertEqual(["chat-1"], adapter.typing_stopped_for)


if __name__ == "__main__":
    unittest.main()
