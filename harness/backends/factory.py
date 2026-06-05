import os
from .base import TelephonyBackend


def get_backend() -> TelephonyBackend:
    """
    Returns the correct backend based on CALL_BACKEND env var.

    CALL_BACKEND=local        → LocalBackend  (free, in-process, no SIP)
    CALL_BACKEND=twilio       → TwilioBackend  (real PSTN via Twilio)
    CALL_BACKEND=signalwire   → SignalWireBackend  (real PSTN via SignalWire)
    """
    backend = os.getenv("CALL_BACKEND", "local").lower()

    if backend == "local":
        from .local_backend import LocalBackend
        return LocalBackend()

    if backend == "twilio":
        from .twilio_backend import TwilioBackend
        return TwilioBackend()

    if backend == "signalwire":
        from .signalwire_backend import SignalWireBackend
        return SignalWireBackend()

    raise ValueError(
        f"Unknown CALL_BACKEND='{backend}'. "
        "Valid options: local, twilio, signalwire"
    )
