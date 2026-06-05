from .base import CallResult, TelephonyBackend


class SignalWireBackend(TelephonyBackend):
    """SignalWire PSTN backend — implemented in Step 10."""

    def place_call(self, target: str, scenario: dict) -> CallResult:
        raise NotImplementedError("SignalWireBackend not yet implemented. Use CALL_BACKEND=local.")
