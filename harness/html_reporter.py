"""
Generate a self-contained HTML report from collected IVR call results.

Called automatically by conftest.py after each test session.
Output: reports/report.html  (no external dependencies)
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from harness.backends.base import CallResult, TurnDetail
from harness.reporter import _normalize, _keyword_check_lines


def generate_html_report(log: list[dict], output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    total = len(log)
    passed = sum(1 for e in log if e["passed"])
    failed = total - passed
    total_dur = sum(e["result"].duration_seconds for e in log)

    cards = "\n".join(_render_card(entry) for entry in log)

    page = _TEMPLATE.format(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total=total,
        passed=passed,
        failed=failed,
        duration=f"{total_dur:.1f}",
        has_failures="has-failures" if failed else "",
        cards=cards,
    )

    Path(output_path).write_text(page, encoding="utf-8")


# ── Card rendering ────────────────────────────────────────────────────────────


def _render_card(entry: dict) -> str:
    result: CallResult = entry["result"]
    scenario: dict = entry["scenario"]
    passed: bool = entry["passed"]
    failures: list[str] = entry["failures"]

    name = html.escape(result.scenario_name or scenario.get("name", "unknown"))
    vclass = "pass" if passed else "fail"
    vlabel = "PASS ✓" if passed else "FAIL ✗"
    dur = f"{result.duration_seconds:.1f}s"
    net = html.escape(result.network_profile)
    open_attr = "" if passed else " open"

    turns_html = _render_turns(result)
    outcomes_html = _render_outcomes(result, scenario)
    diagnosis_html = "" if passed else _render_diagnosis(result, failures)
    audio_html = _render_audio(result)

    return f"""
  <div class="card {vclass}">
    <div class="card-header">
      <span class="verdict {vclass}">{vlabel}</span>
      <span class="card-name">{name}</span>
      <span class="card-meta">{dur} &middot; {net} network</span>
    </div>
    {audio_html}
    <details{open_attr}>
      <summary>Call flow &mdash; {len(result.turns_detail)} turn(s)</summary>
      <div class="turns">
        {turns_html}
      </div>
      {outcomes_html}
    </details>
    {diagnosis_html}
  </div>"""


def _render_audio(result: CallResult) -> str:
    if not result.recording_path:
        return ""
    # Both report.html and the WAV sit in the same reports/ directory
    filename = html.escape(Path(result.recording_path).name)
    return f"""
    <div class="audio-row">
      <span class="audio-label">Recording</span>
      <audio controls>
        <source src="{filename}" type="audio/wav">
        <a href="{filename}">Download {filename}</a>
      </audio>
    </div>"""


def _render_turns(result: CallResult) -> str:
    if not result.turns_detail:
        return '<p class="no-turns">No turns recorded — call may have failed before any exchange.</p>'

    rows = []
    for t in result.turns_detail:
        stuck = t.state_before == t.state_after
        terminal = t.state_after in ("order_complete", "escalating", "done")
        asr_diff = _normalize(t.scripted_text) != _normalize(t.transcribed_text)

        row_class = "stuck" if stuck else ("terminal" if terminal else "")
        arrow = "→"

        if stuck:
            next_label = f'<span class="state-label stuck-label">{t.state_after}</span> <span class="stuck-marker">← STUCK</span>'
        elif terminal:
            next_label = f'<span class="state-label terminal-label">{t.state_after} ✓</span>'
        else:
            next_label = f'<span class="state-label">{t.state_after}</span>'

        heard_class = "asr-diff" if asr_diff else ""
        heard_note = ' <span class="diff-note">← ASR mismatch</span>' if asr_diff else ""

        rows.append(f"""
      <div class="turn {row_class}">
        <div class="turn-header">
          <span class="turn-num">Turn {t.turn_num}</span>
          <span class="state-label from-state">{t.state_before}</span>
          <span class="arrow">{arrow}</span>
          {next_label}
          <span class="response-key">({t.response_key})</span>
        </div>
        <div class="turn-body">
          <div class="line">
            <span class="lbl">Script</span>
            <code class="utterance">{html.escape(t.scripted_text)}</code>
          </div>
          <div class="line">
            <span class="lbl">Heard</span>
            <code class="utterance {heard_class}">{html.escape(t.transcribed_text)}</code>{heard_note}
          </div>
        </div>
      </div>""")

    return "\n".join(rows)


def _render_outcomes(result: CallResult, scenario: dict) -> str:
    expected = scenario.get("expected_outcomes", {})
    chips = []

    for key, actual in [
        ("order_confirmed", result.order_confirmed),
        ("escalated_to_agent", result.escalated_to_agent),
    ]:
        mark = "✓" if actual else "✗"
        chip_class = "pass" if actual else "fail"
        exp = expected.get(key)
        if exp is not None and actual != exp:
            exp_mark = "✓" if exp else "✗"
            chips.append(
                f'<span class="chip mismatch">{key}={mark} (expected {exp_mark})</span>'
            )
        else:
            chips.append(f'<span class="chip {chip_class}">{key}={mark}</span>')

    if result.order_id:
        chips.append(f'<span class="chip neutral">order_id={html.escape(result.order_id)}</span>')
    if result.final_state:
        chips.append(f'<span class="chip neutral">final_state={html.escape(result.final_state)}</span>')

    return f'<div class="outcomes">{"".join(chips)}</div>'


def _render_diagnosis(result: CallResult, failures: list[str]) -> str:
    items = []

    stuck_turns = [t for t in result.turns_detail if t.state_before == t.state_after]
    if stuck_turns:
        for t in stuck_turns:
            check_lines = _keyword_check_lines(t)
            check_html = "".join(f"<li>{html.escape(ln)}</li>" for ln in check_lines)
            items.append(f"""
      <div class="diag-item">
        <strong>Turn {t.turn_num} stuck in {html.escape(t.state_before)}:</strong>
        <ul>{check_html}</ul>
      </div>""")
    elif result.final_state not in ("order_complete", "escalating", "done"):
        fs = html.escape(repr(result.final_state))
        items.append(f"""
      <div class="diag-item">
        <strong>Call ended in state {fs} without completing.</strong>
        <ul>
          <li>Possible causes: caller or IVR timed out, or no scripted turns remained.</li>
        </ul>
      </div>""")

    failure_items = "".join(f"<li>{html.escape(f)}</li>" for f in failures)
    items.append(f'<div class="diag-item"><strong>Failed assertions:</strong><ul>{failure_items}</ul></div>')

    return f'<div class="diagnosis"><h4>Diagnosis</h4>{"".join(items)}</div>'


# ── HTML template ─────────────────────────────────────────────────────────────

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>IVR Test Report &mdash; {timestamp}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 14px;
      background: #f1f5f9;
      color: #1e293b;
      line-height: 1.5;
    }}

    /* ── Header ── */
    header {{
      background: #0f172a;
      color: #f8fafc;
      padding: 24px 32px;
    }}
    header h1 {{
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.3px;
      margin-bottom: 12px;
    }}
    .summary {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }}
    .summary .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 12px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 600;
    }}
    .badge.pass {{ background: #15803d; color: #dcfce7; }}
    .badge.fail {{ background: #b91c1c; color: #fee2e2; }}
    .badge.neutral {{ background: #334155; color: #cbd5e1; }}
    .summary .ts {{
      margin-left: auto;
      font-size: 12px;
      color: #94a3b8;
    }}

    /* ── Main ── */
    main {{
      max-width: 900px;
      margin: 28px auto;
      padding: 0 20px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}

    /* ── Card ── */
    .card {{
      background: #ffffff;
      border-radius: 10px;
      border: 1px solid #e2e8f0;
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,.06);
    }}
    .card.fail {{ border-left: 4px solid #ef4444; }}
    .card.pass {{ border-left: 4px solid #22c55e; }}

    .card-header {{
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px 20px;
      background: #f8fafc;
      border-bottom: 1px solid #e2e8f0;
    }}
    .verdict {{
      font-size: 13px;
      font-weight: 700;
      padding: 2px 10px;
      border-radius: 999px;
      white-space: nowrap;
    }}
    .verdict.pass {{ background: #dcfce7; color: #15803d; }}
    .verdict.fail {{ background: #fee2e2; color: #b91c1c; }}
    .card-name {{ font-weight: 600; font-size: 15px; flex: 1; }}
    .card-meta {{ font-size: 12px; color: #64748b; white-space: nowrap; }}

    /* ── Details / summary ── */
    details {{ padding: 0 20px; }}
    details summary {{
      cursor: pointer;
      padding: 10px 0;
      font-size: 13px;
      font-weight: 500;
      color: #475569;
      user-select: none;
      list-style: none;
    }}
    details summary::before {{
      content: "▶ ";
      font-size: 11px;
    }}
    details[open] summary::before {{ content: "▼ "; }}

    /* ── Turns ── */
    .turns {{ display: flex; flex-direction: column; gap: 10px; padding-bottom: 4px; }}
    .no-turns {{ color: #94a3b8; font-style: italic; padding: 8px 0; }}

    .turn {{
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      overflow: hidden;
    }}
    .turn.stuck {{ border-color: #fca5a5; background: #fff5f5; }}
    .turn.terminal {{ border-color: #86efac; }}

    .turn-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 14px;
      background: #f8fafc;
      border-bottom: 1px solid #e2e8f0;
      flex-wrap: wrap;
      font-size: 13px;
    }}
    .turn.stuck .turn-header {{ background: #fef2f2; border-bottom-color: #fca5a5; }}
    .turn.terminal .turn-header {{ background: #f0fdf4; border-bottom-color: #86efac; }}

    .turn-num {{ font-weight: 600; color: #475569; white-space: nowrap; }}
    .arrow {{ color: #94a3b8; }}
    .state-label {{
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.4px;
      padding: 2px 8px;
      border-radius: 4px;
      background: #e0e7ff;
      color: #3730a3;
      text-transform: uppercase;
    }}
    .from-state {{ background: #ede9fe; color: #5b21b6; }}
    .stuck-label {{ background: #fee2e2; color: #b91c1c; }}
    .terminal-label {{ background: #dcfce7; color: #15803d; }}
    .stuck-marker {{ font-size: 12px; color: #dc2626; font-weight: 600; }}
    .response-key {{ font-size: 11px; color: #94a3b8; margin-left: auto; }}

    .turn-body {{ padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; }}
    .line {{ display: flex; align-items: baseline; gap: 10px; }}
    .lbl {{
      font-size: 11px;
      font-weight: 600;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      min-width: 40px;
    }}
    .utterance {{
      font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
      font-size: 13px;
      background: #f1f5f9;
      padding: 2px 8px;
      border-radius: 4px;
      color: #1e293b;
    }}
    .utterance.asr-diff {{
      background: #fef9c3;
      color: #713f12;
      outline: 1px solid #fde047;
    }}
    .diff-note {{ font-size: 12px; color: #d97706; font-weight: 500; }}

    /* ── Outcomes ── */
    .outcomes {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 12px 0 16px;
    }}
    .chip {{
      font-size: 12px;
      font-weight: 500;
      padding: 3px 10px;
      border-radius: 999px;
      border: 1px solid transparent;
    }}
    .chip.pass {{ background: #dcfce7; color: #15803d; border-color: #86efac; }}
    .chip.fail {{ background: #fee2e2; color: #b91c1c; border-color: #fca5a5; }}
    .chip.mismatch {{ background: #fff7ed; color: #c2410c; border-color: #fdba74; font-weight: 700; }}
    .chip.neutral {{ background: #f1f5f9; color: #475569; border-color: #cbd5e1; }}

    /* ── Diagnosis ── */
    .diagnosis {{
      margin: 0 20px 16px;
      border: 1px solid #fca5a5;
      border-radius: 8px;
      background: #fff5f5;
      overflow: hidden;
    }}
    .diagnosis h4 {{
      background: #fee2e2;
      color: #b91c1c;
      padding: 8px 14px;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.3px;
      border-bottom: 1px solid #fca5a5;
    }}
    .diag-item {{
      padding: 10px 14px;
      font-size: 13px;
      border-bottom: 1px solid #ffe4e6;
    }}
    .diag-item:last-child {{ border-bottom: none; }}
    .diag-item strong {{ color: #991b1b; }}
    .diag-item ul {{ margin: 6px 0 0 20px; color: #7f1d1d; line-height: 1.8; }}

    /* ── Audio player ── */
    .audio-row {{
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 20px;
      background: #f8fafc;
      border-bottom: 1px solid #e2e8f0;
    }}
    .audio-label {{
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: #64748b;
      white-space: nowrap;
    }}
    audio {{
      height: 32px;
      flex: 1;
      max-width: 480px;
    }}

    /* ── Footer ── */
    footer {{
      text-align: center;
      padding: 24px;
      font-size: 12px;
      color: #94a3b8;
    }}
  </style>
</head>
<body>
  <header>
    <h1>IVR Test Report</h1>
    <div class="summary">
      <span class="badge pass">{passed} passed</span>
      <span class="badge fail {has_failures}">{failed} failed</span>
      <span class="badge neutral">{total} total &mdash; {duration}s</span>
      <span class="ts">{timestamp}</span>
    </div>
  </header>

  <main>
    {cards}
  </main>

  <footer>Generated by IVR Testing Framework</footer>
</body>
</html>
"""
