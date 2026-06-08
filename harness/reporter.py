"""
IVR call reporter — formats test results for human consumption.

Usage in tests:
    result = backend.place_call(target="local", scenario=MY_SCENARIO)
    report_and_assert(result, MY_SCENARIO)

On pass  : prints a turn-by-turn flow to stdout (visible with pytest -s).
On fail  : raises AssertionError whose message IS the full diagnostic report,
           so pytest always shows it without any extra flags.
"""

from __future__ import annotations

from harness.backends.base import CallResult, TurnDetail
from ivr_server.state_machine import (
    CONFIRM_TRIGGERS,
    DELIVERY_TRIGGERS,
    DENY_TRIGGERS,
    ESCALATION_TRIGGERS,
    ORDER_TRIGGERS,
    PICKUP_TRIGGERS,
)

_SEP = "─" * 68

# Accumulates every report_and_assert() call so conftest can build the HTML report.
_report_log: list[dict] = []


def get_report_log() -> list[dict]:
    return _report_log

# Keyword sets checked per state, for the stuck-turn diagnosis
_STATE_TRIGGERS: dict[str, list[tuple[str, list[str]]]] = {
    "greeting": [("ORDER_TRIGGERS", ORDER_TRIGGERS), ("ESCALATION_TRIGGERS", ESCALATION_TRIGGERS)],
    "order_taking": [],  # any non-empty text advances
    "order_confirm": [
        ("DENY_TRIGGERS", DENY_TRIGGERS),
        ("CONFIRM_TRIGGERS", CONFIRM_TRIGGERS),
        ("ESCALATION_TRIGGERS", ESCALATION_TRIGGERS),
    ],
    "delivery_pickup": [
        ("DELIVERY_TRIGGERS", DELIVERY_TRIGGERS),
        ("PICKUP_TRIGGERS", PICKUP_TRIGGERS),
        ("ESCALATION_TRIGGERS", ESCALATION_TRIGGERS),
    ],
}


# ── Public API ────────────────────────────────────────────────────────────────


def report_and_assert(result: CallResult, scenario: dict) -> None:
    """
    Check expected_outcomes from the scenario against the call result.

    Pass → print the call flow report to stdout (visible with pytest -s).
    Fail → raise AssertionError whose message is the full diagnostic report.
    """
    failures = _check_outcomes(result, scenario)
    passed = not failures
    report = _format_report(result, scenario, passed, failures)

    _report_log.append({
        "result": result,
        "scenario": scenario,
        "passed": passed,
        "failures": failures,
    })

    if passed:
        print(report)
    else:
        raise AssertionError("\n" + report)


# ── Outcome checking ──────────────────────────────────────────────────────────


def _check_outcomes(result: CallResult, scenario: dict) -> list[str]:
    failures = []
    expected = scenario.get("expected_outcomes", {})
    for key, actual in [
        ("order_confirmed", result.order_confirmed),
        ("escalated_to_agent", result.escalated_to_agent),
    ]:
        if key in expected and actual != expected[key]:
            exp = expected[key]
            failures.append(f"{key}: expected {exp}, got {actual}")
    return failures


# ── Report formatting ─────────────────────────────────────────────────────────


def _format_report(
    result: CallResult,
    scenario: dict,
    passed: bool,
    failures: list[str],
) -> str:
    name = result.scenario_name or scenario.get("name", "unknown")
    verdict = "PASS ✓" if passed else "FAIL ✗"
    dur = f"{result.duration_seconds:.1f}s"
    net = result.network_profile

    lines: list[str] = [
        _SEP,
        f"  {verdict}  {name}    {dur} · {net} network",
        _SEP,
        "",
    ]

    if result.turns_detail:
        for t in result.turns_detail:
            lines.extend(_format_turn(t))
    else:
        lines.append("  (no turns recorded — call may have failed before any exchange)")

    lines += ["", _format_outcomes_line(result, scenario), ""]

    if not passed:
        lines += _format_diagnosis(result, failures)

    lines.append(_SEP)
    return "\n".join(lines)


def _format_turn(t: TurnDetail) -> list[str]:
    stuck = t.state_before == t.state_after
    terminal = t.state_after in ("order_complete", "escalating", "done")

    if stuck:
        header = f"  Turn {t.turn_num}  [{t.state_before} → {t.state_after}]  ← STUCK"
    elif terminal:
        header = f"  Turn {t.turn_num}  [{t.state_before} → {t.state_after} ✓]"
    else:
        header = f"  Turn {t.turn_num}  [{t.state_before} → {t.state_after}]"

    lines = [header]

    scripted = t.scripted_text
    heard = t.transcribed_text
    # Strip trailing punctuation Whisper adds before comparing (e.g. "hello." vs "hello")
    asr_diff = _normalize(scripted) != _normalize(heard)

    lines.append(f"    Script: \"{scripted}\"")
    if asr_diff:
        lines.append(f"    Heard:  \"{heard}\"  ← ASR mismatch")
    else:
        lines.append(f"    Heard:  \"{heard}\"")

    lines.append("")
    return lines


def _format_outcomes_line(result: CallResult, scenario: dict) -> str:
    expected = scenario.get("expected_outcomes", {})
    parts: list[str] = []

    for key, actual in [
        ("order_confirmed", result.order_confirmed),
        ("escalated_to_agent", result.escalated_to_agent),
    ]:
        mark = "✓" if actual else "✗"
        exp = expected.get(key)
        if exp is not None and actual != exp:
            exp_mark = "✓" if exp else "✗"
            parts.append(f"{key}={mark} (expected {exp_mark})")
        else:
            parts.append(f"{key}={mark}")

    if result.order_id:
        parts.append(f"order_id={result.order_id}")
    if result.final_state:
        parts.append(f"final_state={result.final_state}")

    return "  Outcomes:  " + "   ".join(parts)


def _format_diagnosis(result: CallResult, failures: list[str]) -> list[str]:
    lines = ["  ── Diagnosis " + "─" * 54, ""]

    stuck_turns = [t for t in result.turns_detail if t.state_before == t.state_after]
    if stuck_turns:
        for t in stuck_turns:
            lines.append(f"  Turn {t.turn_num} stuck in {t.state_before}:")
            for msg in _keyword_check_lines(t):
                lines.append(f"    {msg}")
            lines.append("")
    elif result.final_state not in ("order_complete", "escalating", "done"):
        lines.append(f"  No stuck turns detected — call ended in {result.final_state!r}.")
        lines.append("  Possible causes: caller or IVR timed out, or no scripted turns remained.")
        lines.append("")

    lines.append("  Failed assertions:")
    for f in failures:
        lines.append(f"    · {f}")
    lines.append("")

    return lines


# ── Keyword diagnosis ─────────────────────────────────────────────────────────


def _keyword_check_lines(t: TurnDetail) -> list[str]:
    """Return per-trigger diagnostic lines explaining why a stuck turn didn't advance."""
    state = t.state_before
    heard = t.transcribed_text.lower()
    scripted = t.scripted_text.lower()

    if state == "order_taking":
        if heard.strip():
            return [
                "Any non-empty input should advance ORDER_TAKING — non-empty text was heard ✓.",
                "Possible cause: IVR audio queue timing issue or session ended before processing.",
            ]
        return ["Transcription was empty — TTS synthesis failure or audio queue not received by IVR."]

    triggers = _STATE_TRIGGERS.get(state)
    if not triggers:
        return [f"No keyword map defined for state {state!r}."]

    lines: list[str] = []
    any_found = False
    for name, keywords in triggers:
        found_in_heard = any(kw in heard for kw in keywords)
        found_in_scripted = any(kw in scripted for kw in keywords)
        if found_in_heard:
            kw = _first_match(heard, keywords)
            lines.append(f"{name}: \"{kw}\" found in heard ✓ — trigger matched, check state machine logic")
            any_found = True
        elif found_in_scripted:
            kw = _first_match(scripted, keywords)
            lines.append(f"{name}: \"{kw}\" in script but NOT in heard ← ASR dropped the trigger word")
            any_found = True
        # Skip groups with no match in either — pure noise when other groups explain the issue

    if not any_found:
        lines.append("No trigger keywords found in script or heard text.")
        lines.append(f"Expected one of: {', '.join(n for n, _ in triggers)}")

    return lines if lines else ["No applicable keyword triggers for this state."]


def _first_match(text: str, keywords: list[str]) -> str:
    for kw in keywords:
        if kw in text:
            return kw
    return ""


def _normalize(text: str) -> str:
    """Strip trailing punctuation and whitespace for ASR comparison."""
    return text.strip().rstrip(".,!?;:").strip().lower()
