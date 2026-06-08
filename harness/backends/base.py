from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TurnDetail:
    turn_num: int
    state_before: str       # IVRState.value (string) before this caller turn
    scripted_text: str      # what the scenario said to say (or "[DTMF: X]")
    transcribed_text: str   # what Whisper heard
    response_key: str       # IVR prompt key chosen (e.g. "ask_delivery")
    state_after: str        # IVRState.value after processing this turn


@dataclass
class CallResult:
    transcript: List[dict]          # [{"speaker": "ivr|caller", "text": "..."}]
    order_confirmed: bool
    escalated_to_agent: bool
    order_id: Optional[str]
    duration_seconds: float
    network_profile: str
    scenario_name: str = ""
    final_state: str = ""
    turns_detail: List[TurnDetail] = field(default_factory=list)
    recording_path: Optional[str] = None  # WAV file saved alongside reports/report.html


class TelephonyBackend(ABC):
    @abstractmethod
    def place_call(self, target: str, scenario: dict) -> CallResult:
        """
        Place a call to `target` and run the scenario.

        target:   SIP URI (local) or E.164 phone number (PSTN backends)
        scenario: dict loaded from a scenarios/*.json file
        Returns:  CallResult with transcript and outcome flags
        """
