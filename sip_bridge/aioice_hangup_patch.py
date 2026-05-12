# Author: Štefan Gajdošík <xgajdo30@stud.fit.vut.cz>
"""
Optional one-shot patch for aioice: STUN retransmit after ICE UDP transport is closed.

Without this, hangup/teardown can log:
  AttributeError: 'NoneType' object has no attribute 'sendto'
from Transaction.__retry() (harmless but noisy).

Call ``install_aioice_stun_hangup_patch()`` once at process startup in long-running daemons
(Matrix reverse bridge, Asterisk bridge). Not wired into MediaBridge.start() so library
use of MediaBridge stays free of global aioice monkey-patching.
"""

from __future__ import annotations

try:
    import aioice.stun as _stun
except ImportError:
    _stun = None


def install_aioice_stun_hangup_patch() -> None:
    if _stun is None:
        return
    if getattr(_stun.Transaction, "_dp_hangup_patch_installed", False):
        return
    _orig = _stun.Transaction._Transaction__retry

    def _safe_retry(self) -> None:
        proto = self._Transaction__protocol
        if getattr(proto, "transport", None) is None:
            h = self._Transaction__timeout_handle
            if h is not None:
                try:
                    h.cancel()
                except Exception:
                    pass
                self._Transaction__timeout_handle = None
            fut = self._Transaction__future
            if not fut.done():
                fut.set_exception(_stun.TransactionTimeout())
            return
        return _orig(self)

    _stun.Transaction._Transaction__retry = _safe_retry  # type: ignore[assignment]
    setattr(_stun.Transaction, "_dp_hangup_patch_installed", True)
