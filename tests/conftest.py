import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


def pytest_configure(config):
    """Block Twilio/SignalWire backends from running inside pytest.

    LocalBackend is free and instant. Real PSTN calls cost money, hit real
    phone numbers, and are far too slow for a test suite. Use call.py instead
    when you need end-to-end PSTN validation.
    """
    backend = os.environ.get("CALL_BACKEND", "local").lower()
    if backend != "local":
        pytest.exit(
            f"\n\n"
            f"  CALL_BACKEND={backend!r} — pytest only runs against the local backend.\n"
            f"  Real PSTN calls belong in call.py, not the test suite.\n\n"
            f"  To run tests:  unset CALL_BACKEND   (or set CALL_BACKEND=local)\n"
            f"  To make a real call:  python call.py\n",
            returncode=4,
        )


def pytest_sessionfinish(session, exitstatus):
    from harness.reporter import get_report_log
    from harness.html_reporter import generate_html_report

    log = get_report_log()
    if not log:
        return

    out = Path(__file__).parent.parent / "reports" / "report.html"
    generate_html_report(log, str(out))
    print(f"\n  HTML report → {out}")
