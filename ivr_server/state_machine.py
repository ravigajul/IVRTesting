from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class IVRState(Enum):
    GREETING = "greeting"
    ORDER_TAKING = "order_taking"
    ORDER_CONFIRM = "order_confirm"
    DELIVERY_PICKUP = "delivery_pickup"
    ORDER_COMPLETE = "order_complete"
    ESCALATING = "escalating"
    DONE = "done"


TERMINAL_STATES = {IVRState.DONE, IVRState.ORDER_COMPLETE, IVRState.ESCALATING}

ESCALATION_TRIGGERS = [
    "agent", "representative", "human", "operator", "person",
    "speak to someone", "real person", "customer service", "manager",
    "frustrated", "angry", "ridiculous", "unacceptable", "terrible",
    "this is not working", "useless",
]

ORDER_TRIGGERS = ["order", "new order", "pizza", "food", "want", "like to", "place"]
CONFIRM_TRIGGERS = ["yes", "yeah", "yep", "correct", "sure", "ok", "okay", "that's right", "that is right"]
DENY_TRIGGERS = ["no", "nope", "wrong", "incorrect", "change", "different", "not right"]
DELIVERY_TRIGGERS = ["delivery", "deliver", "bring it", "drop off", "send it"]
PICKUP_TRIGGERS = ["pickup", "pick up", "collect", "i'll come", "i will come", "grab it"]


@dataclass
class CallSession:
    state: IVRState = IVRState.GREETING
    retry_count: int = 0
    max_retries: int = 3
    order_details: Optional[str] = None
    delivery_type: Optional[str] = None
    order_id: Optional[str] = None
    escalated: bool = False
    transcript: List[dict] = field(default_factory=list)
    # Each entry: {turn, state_before, caller_text, response_key, state_after}
    transitions: List[dict] = field(default_factory=list)

    def add_turn(self, speaker: str, text: str):
        self.transcript.append({"speaker": speaker, "text": text})


def _contains(text: str, keywords: List[str]) -> bool:
    t = text.lower()
    return any(kw in t for kw in keywords)


def next_state(session: CallSession, caller_input: str) -> Tuple[IVRState, str]:
    """
    Given the current session state and caller input, return (new_state, response_key).
    response_key maps to a prompt in MockIVR.PROMPTS.
    """
    text = caller_input.strip()

    # Global escalation check applies from any state
    if _contains(text, ESCALATION_TRIGGERS):
        session.escalated = True
        return IVRState.ESCALATING, "escalating"

    if session.state == IVRState.GREETING:
        if _contains(text, ORDER_TRIGGERS):
            session.retry_count = 0
            return IVRState.ORDER_TAKING, "ask_item"
        session.retry_count += 1
        if session.retry_count >= session.max_retries:
            session.escalated = True
            return IVRState.ESCALATING, "escalating"
        return IVRState.GREETING, "not_understood"

    if session.state == IVRState.ORDER_TAKING:
        if text:
            session.order_details = text
            session.retry_count = 0
            return IVRState.ORDER_CONFIRM, "confirm_order"
        return IVRState.ORDER_TAKING, "ask_item"

    if session.state == IVRState.ORDER_CONFIRM:
        # Check deny before confirm — "no that's right" should be treated as denial
        if _contains(text, DENY_TRIGGERS):
            session.order_details = None
            return IVRState.ORDER_TAKING, "ask_item"
        if _contains(text, CONFIRM_TRIGGERS):
            return IVRState.DELIVERY_PICKUP, "ask_delivery"
        return IVRState.ORDER_CONFIRM, "confirm_order"

    if session.state == IVRState.DELIVERY_PICKUP:
        if _contains(text, DELIVERY_TRIGGERS):
            session.delivery_type = "delivery"
            session.order_id = f"ORD{abs(hash(session.order_details)) % 9000 + 1000}"
            return IVRState.ORDER_COMPLETE, "order_placed"
        if _contains(text, PICKUP_TRIGGERS):
            session.delivery_type = "pickup"
            session.order_id = f"ORD{abs(hash(session.order_details)) % 9000 + 1000}"
            return IVRState.ORDER_COMPLETE, "order_placed"
        return IVRState.DELIVERY_PICKUP, "ask_delivery"

    return IVRState.DONE, "goodbye"
