"""Signal features plugin — message editing and progress-message cleanup.

Patches ``SignalAdapter`` at startup to add:
- ``edit_message()`` — edit sent messages via signal-cli's ``editTimestamp`` RPC
  parameter, enabling the stream consumer's progressive-edit mode.
- ``delete_message()`` — delete messages via signal-cli's ``remoteDelete`` RPC,
  used by the ephemeral cleanup system.
- Automatic cleanup of tool-progress messages after the real reply is sent.
"""

from __future__ import annotations

import logging

from .signal_edit import apply_signal_edit_patch
from .signal_ephemeral import apply_signal_ephemeral_patch

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    try:
        from gateway.platforms.signal import SignalAdapter
    except Exception as exc:  # pragma: no cover - runtime safety path
        logger.warning("signal-features: unable to import Signal adapter: %s", exc)
        return

    apply_signal_edit_patch(SignalAdapter)
    apply_signal_ephemeral_patch(SignalAdapter)
    logger.info("signal-features: Signal adapter patches applied (edit_message, delete_message, ephemeral cleanup)")
