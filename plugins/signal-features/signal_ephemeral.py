"""Signal ephemeral progress-message cleanup.

Tool-progress messages (e.g. "⚡ terminal...", "🔧 execute_code:") are
useful during processing but clutter the chat history.  This module:

1. Adds ``delete_message()`` to ``SignalAdapter`` using signal-cli's
   ``remoteDelete`` RPC method.
2. Wraps ``send()`` to detect progress messages via a first-line regex
   heuristic and track their timestamps.
3. When a non-progress message is sent (the real reply), fires a
   background task to delete all accumulated progress messages.

Configuration is read from ``~/.hermes/config.yaml``::

    plugins:
      signal-ephemeral:
        enabled: true           # default true
        cleanup_mode: delete    # "delete" | "disappearing" | "off"
        cleanup_delay_seconds: 15   # only used when mode is "disappearing"
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

PLUGIN_NAME = "signal-ephemeral"
_PROGRESS_RE = re.compile(r"^[^\w\s]{1,3}\s+[a-zA-Z0-9_-]+(?:\.\.\.|:\s)")


def is_progress_message(content: str | None) -> bool:
    """Return True if *content* looks like a tool-progress status line."""
    if not content:
        return False
    first_line = content.splitlines()[0].strip()
    return bool(_PROGRESS_RE.match(first_line))


def load_plugin_config() -> Dict[str, Any]:
    """Load the top-level Hermes config from ``$HERMES_HOME/config.yaml``."""
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    config_path = hermes_home / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(config_path.read_text()) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # pragma: no cover
        logger.warning("signal-ephemeral: failed to load %s: %s", config_path, exc)
        return {}


def _plugin_settings(config_provider: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    config = config_provider() or {}
    plugins = config.get("plugins", {})
    settings = plugins.get(PLUGIN_NAME, {})
    return settings if isinstance(settings, dict) else {}


def _tracking_enabled(config_provider: Callable[[], Dict[str, Any]]) -> bool:
    settings = _plugin_settings(config_provider)
    return bool(settings.get("enabled", True)) and settings.get("cleanup_mode", "delete") != "off"


def _cleanup_delay(config_provider: Callable[[], Dict[str, Any]]) -> float:
    settings = _plugin_settings(config_provider)
    mode = settings.get("cleanup_mode", "delete")
    if mode != "disappearing":
        return 0.0
    try:
        return max(0.0, float(settings.get("cleanup_delay_seconds", 15)))
    except (TypeError, ValueError):
        return 15.0


def _get_progress_store(adapter: Any) -> Dict[str, List[str]]:
    """Per-instance dict mapping chat_id -> list of progress message IDs."""
    store = getattr(adapter, "_signal_ephemeral_progress", None)
    if store is None:
        store = {}
        adapter._signal_ephemeral_progress = store
    return store


async def _delete_after(adapter: Any, chat_id: str, message_ids: List[str], delay: float) -> None:
    """Background task: wait *delay* seconds then delete each message."""
    if delay > 0:
        await asyncio.sleep(delay)
    for message_id in message_ids:
        try:
            await adapter.delete_message(chat_id, message_id)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "signal-ephemeral: failed to delete progress message %s in %s: %s",
                message_id, chat_id, exc,
            )


def apply_signal_ephemeral_patch(
    adapter_cls: type,
    *,
    config_provider: Callable[[], Dict[str, Any]] | None = None,
) -> type:
    """Monkey-patch *adapter_cls* with delete_message and progress cleanup.

    Idempotent — safe to call multiple times.
    """
    if getattr(adapter_cls, "_signal_ephemeral_patched", False):
        return adapter_cls

    config_provider = config_provider or load_plugin_config

    original_send = adapter_cls.send

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a previously sent message via signal-cli's remoteDelete RPC."""
        params: Dict[str, Any] = {
            "account": self.account,
            "targetTimestamp": int(message_id),
        }
        if chat_id.startswith("group:"):
            params["groupId"] = chat_id[6:]
        else:
            params["recipient"] = [chat_id]
        result = await self._rpc("remoteDelete", params)
        return result is not None

    async def patched_send(self, chat_id, content, reply_to=None, metadata=None):
        result = await original_send(self, chat_id, content, reply_to=reply_to, metadata=metadata)

        if not _tracking_enabled(config_provider) or not getattr(result, "message_id", None):
            return result

        store = _get_progress_store(self)
        if is_progress_message(content):
            store.setdefault(chat_id, []).append(result.message_id)
            return result

        # Non-progress message → clean up accumulated progress messages
        pending = list(store.pop(chat_id, []))
        if pending:
            asyncio.create_task(_delete_after(self, chat_id, pending, _cleanup_delay(config_provider)))
        return result

    adapter_cls.send = patched_send
    adapter_cls.delete_message = delete_message
    adapter_cls._signal_ephemeral_patched = True
    return adapter_cls
