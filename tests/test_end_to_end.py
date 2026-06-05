"""
End-to-end call tests using LocalBackend.
These run a full synthesize → call → transcribe → state-machine loop.
Slower than unit tests (~10-30s per test) due to TTS + Whisper.
"""
import pytest
from harness.backends.local_backend import LocalBackend
from ivr_server.state_machine import IVRState


@pytest.fixture(scope="module")
def backend():
    return LocalBackend(whisper_model="base")


HAPPY_PATH_SCENARIO = {
    "name": "happy_path_delivery",
    "network_profile": "clean",
    "customer_voice": "en-US-JennyNeural",
    "turns": [
        {"customer_says": "I'd like to place a new order"},
        {"customer_says": "I want a large pepperoni pizza"},
        {"customer_says": "Yes that's correct"},
        {"customer_says": "Delivery please"},
    ],
    "expected_outcomes": {
        "order_confirmed": True,
        "escalated_to_agent": False,
    },
}

ESCALATION_SCENARIO = {
    "name": "escalation_agent_request",
    "network_profile": "clean",
    "customer_voice": "en-US-GuyNeural",
    "turns": [
        {"customer_says": "I want to speak to a customer service agent"},
    ],
    "expected_outcomes": {
        "order_confirmed": False,
        "escalated_to_agent": True,
    },
}

ESCALATION_MID_ORDER_SCENARIO = {
    "name": "escalation_mid_order",
    "network_profile": "clean",
    "customer_voice": "en-US-JennyNeural",
    "turns": [
        {"customer_says": "I'd like to order a pizza"},
        {"customer_says": "This is absolutely ridiculous I want a manager"},
    ],
    "expected_outcomes": {
        "order_confirmed": False,
        "escalated_to_agent": True,
    },
}


class TestEndToEnd:
    def test_happy_path_order_confirmed(self, backend):
        result = backend.place_call(target="local", scenario=HAPPY_PATH_SCENARIO)

        assert result.order_confirmed, (
            f"Order not confirmed.\nTranscript:\n"
            + "\n".join(f"  [{t['speaker']}] {t['text']}" for t in result.transcript)
        )
        assert not result.escalated_to_agent
        assert result.order_id is not None
        assert result.order_id.startswith("ORD")

    def test_escalation_agent_request(self, backend):
        result = backend.place_call(target="local", scenario=ESCALATION_SCENARIO)

        assert result.escalated_to_agent, (
            f"Expected escalation.\nTranscript:\n"
            + "\n".join(f"  [{t['speaker']}] {t['text']}" for t in result.transcript)
        )
        assert not result.order_confirmed

    def test_escalation_mid_order(self, backend):
        result = backend.place_call(target="local", scenario=ESCALATION_MID_ORDER_SCENARIO)

        assert result.escalated_to_agent, (
            f"Expected mid-order escalation.\nTranscript:\n"
            + "\n".join(f"  [{t['speaker']}] {t['text']}" for t in result.transcript)
        )

    def test_transcript_has_both_speakers(self, backend):
        result = backend.place_call(target="local", scenario=HAPPY_PATH_SCENARIO)

        speakers = {t["speaker"] for t in result.transcript}
        assert "ivr" in speakers
        assert "caller" in speakers

    def test_call_duration_recorded(self, backend):
        result = backend.place_call(target="local", scenario=HAPPY_PATH_SCENARIO)
        assert result.duration_seconds > 0

    def test_network_profile_recorded(self, backend):
        result = backend.place_call(target="local", scenario=HAPPY_PATH_SCENARIO)
        assert result.network_profile == "clean"
