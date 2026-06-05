from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CallResult:
    transcript: List[dict]          # [{"speaker": "ivr|caller", "text": "..."}]
    order_confirmed: bool
    escalated_to_agent: bool
    order_id: Optional[str]
    duration_seconds: float
    network_profile: str


class TelephonyBackend(ABC):
    @abstractmethod
    def place_call(self, target: str, scenario: dict) -> CallResult:
        """
        Place a call to `target` and run the scenario.

        target:   SIP URI (local) or E.164 phone number (PSTN backends)
        scenario: dict loaded from a scenarios/*.json file
        Returns:  CallResult with transcript and outcome flags
        """
