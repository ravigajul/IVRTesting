"""Unit tests for the IVR state machine — no audio, no network, instant."""
import pytest
from ivr_server.state_machine import CallSession, IVRState, next_state


def advance(session, text):
    """Helper: call next_state and update session.state in one step."""
    session.state, key = next_state(session, text)
    return session.state, key


class TestHappyPath:
    def test_full_order_delivery(self):
        s = CallSession()
        assert advance(s, "I'd like to place a new order")[0] == IVRState.ORDER_TAKING
        assert advance(s, "large pepperoni pizza")[0] == IVRState.ORDER_CONFIRM
        assert s.order_details == "large pepperoni pizza"
        assert advance(s, "yes that's correct")[0] == IVRState.DELIVERY_PICKUP
        assert advance(s, "delivery please")[0] == IVRState.ORDER_COMPLETE
        assert s.order_id is not None
        assert s.delivery_type == "delivery"

    def test_full_order_pickup(self):
        s = CallSession()
        advance(s, "new order")
        advance(s, "two medium cheese pizzas")
        advance(s, "yes")
        assert advance(s, "I will pick up")[0] == IVRState.ORDER_COMPLETE
        assert s.delivery_type == "pickup"

    def test_order_correction(self):
        s = CallSession()
        advance(s, "order")
        advance(s, "small veggie pizza")
        assert s.order_details == "small veggie pizza"
        # Customer says no — goes back to ORDER_TAKING
        assert advance(s, "no that's wrong")[0] == IVRState.ORDER_TAKING
        assert s.order_details is None
        # Now place the correct order
        advance(s, "large veggie pizza")
        assert s.order_details == "large veggie pizza"


class TestEscalation:
    def test_explicit_agent_request(self):
        s = CallSession()
        state, _ = advance(s, "I want to speak to an agent")
        assert state == IVRState.ESCALATING
        assert s.escalated is True

    def test_frustration_trigger(self):
        s = CallSession()
        advance(s, "new order")
        advance(s, "pepperoni pizza")
        state, _ = advance(s, "this is absolutely ridiculous")
        assert state == IVRState.ESCALATING

    def test_escalation_mid_order(self):
        s = CallSession()
        advance(s, "new order")
        state, _ = advance(s, "I want to speak to a representative")
        assert state == IVRState.ESCALATING

    def test_max_retries_escalates(self):
        s = CallSession()
        # Three unrecognised inputs in GREETING → escalation
        for _ in range(3):
            advance(s, "hmm")
        assert s.state == IVRState.ESCALATING

    def test_no_false_escalation_on_mild_feedback(self):
        s = CallSession()
        advance(s, "new order")
        advance(s, "pepperoni pizza")
        # "not right" triggers DENY, not escalation
        state, _ = advance(s, "no not right")
        assert state == IVRState.ORDER_TAKING
        assert state != IVRState.ESCALATING


class TestEdgeCases:
    def test_empty_input_stays_in_state(self):
        s = CallSession()
        advance(s, "new order")
        state, _ = advance(s, "")  # empty input in ORDER_TAKING
        assert state == IVRState.ORDER_TAKING

    def test_order_id_generated(self):
        s = CallSession()
        advance(s, "order")
        advance(s, "hawaiian pizza")
        advance(s, "yes")
        advance(s, "delivery")
        assert s.order_id.startswith("ORD")
        assert len(s.order_id) == 7  # ORD + 4 digits

    def test_transcript_recorded(self):
        s = CallSession()
        s.add_turn("ivr", "Thank you for calling.")
        advance(s, "new order")
        s.add_turn("ivr", "What would you like?")
        assert len(s.transcript) == 2
        assert s.transcript[0]["speaker"] == "ivr"
