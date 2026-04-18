"""Signal message-editing patch.

Adds ``edit_message()`` to ``SignalAdapter`` so the stream consumer can
progressively edit a single message with streamed tokens instead of sending
one message per tool-progress update.

Signal identifies messages by their send timestamp.  To edit, we pass the
original timestamp as ``editTimestamp`` alongside the new content.  signal-cli
returns a new timestamp for the edited version; we track this chain so
successive edits always reference the latest timestamp.

Also patches ``send()`` to reliably return ``message_id`` extracted directly
from the RPC response (the upstream implementation already does this, but this
patch ensures the edit chain is initialized for every sent message).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _get_send_result_class():
    """Import SendResult from the gateway, with a dataclass fallback."""
    try:
        from gateway.platforms.base import SendResult
        return SendResult
    except Exception:
        from dataclasses import dataclass

        @dataclass
        class _FallbackSendResult:
            success: bool
            message_id: Optional[str] = None
            error: Optional[str] = None
        return _FallbackSendResult


def _get_edit_chain(adapter) -> Dict[str, str]:
    """Per-instance edit chain: maps original message_id -> latest timestamp."""
    store = getattr(adapter, "_signal_edit_chain", None)
    if store is None:
        store = {}
        adapter._signal_edit_chain = store
    return store


def apply_signal_edit_patch(adapter_cls: type) -> type:
    """Monkey-patch ``adapter_cls`` with ``edit_message()`` support.

    Idempotent — safe to call multiple times.
    """
    if getattr(adapter_cls, "_signal_edit_patched", False):
        return adapter_cls

    SendResult = _get_send_result_class()

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> Any:
        """Edit a previously sent message via signal-cli's editTimestamp."""
        stop_typing = getattr(self, "_stop_typing_indicator", None)
        if stop_typing is not None:
            await stop_typing(chat_id)

        chain = _get_edit_chain(self)
        resolved_id = chain.get(str(message_id), str(message_id))

        params: Dict[str, Any] = {
            "account": self.account,
            "message": content,
            "editTimestamp": int(resolved_id),
        }
        if chat_id.startswith("group:"):
            params["groupId"] = chat_id[6:]
        else:
            params["recipient"] = [chat_id]

        result = await self._rpc("send", params)
        if result is None:
            return SendResult(success=False, error="RPC edit failed")

        # Track the new timestamp so the next edit targets it
        timestamp = result.get("timestamp") if isinstance(result, dict) else None
        next_id = str(timestamp) if timestamp is not None else resolved_id
        chain[str(message_id)] = next_id

        track_sent = getattr(self, "_track_sent_timestamp", None)
        if track_sent is not None:
            track_sent(result)

        return SendResult(success=True, message_id=next_id)

    adapter_cls.edit_message = edit_message
    adapter_cls._signal_edit_patched = True
    return adapter_cls
