"""
LocalBackend — in-process IVR testing with no SIP/PSTN.

How it works:
  1. Starts MockIVR in a background thread
  2. Runs the scenario's caller turns (synthesizing customer audio via edge-tts)
  3. Each turn: wait for IVR audio → send next customer audio
  4. Collects the full transcript and returns CallResult

Switching to PSTN: replace LocalBackend with TwilioBackend or SignalWireBackend.
The test code never changes.
"""

import os
import queue
import tempfile
import threading
import time
from typing import Optional

import numpy as np
import soundfile as sf

from .base import CallResult, TelephonyBackend
from ivr_server.mock_ivr import MockIVR
from ivr_server.state_machine import IVRState
from ivr_server.audio_utils import synthesize_to_array


class LocalBackend(TelephonyBackend):

    def __init__(self, whisper_model: str = "base"):
        self._ivr = MockIVR(whisper_model=whisper_model)

    def place_call(self, target: str, scenario: dict) -> CallResult:
        caller_q: queue.Queue = queue.Queue()
        ivr_q: queue.Queue = queue.Queue()

        network_profile = self._resolve_network_profile(scenario)
        session_box: dict = {}
        start = time.monotonic()

        # Run IVR in background thread
        def _run_ivr():
            session = self._ivr.run_call(
                caller_audio_q=caller_q,
                ivr_audio_q=ivr_q,
                network_profile=network_profile,
            )
            session_box["session"] = session

        ivr_thread = threading.Thread(target=_run_ivr, daemon=True)
        ivr_thread.start()

        # Drive the caller side from scenario turns
        self._drive_caller(scenario, caller_q, ivr_q)

        ivr_thread.join(timeout=120)
        duration = time.monotonic() - start

        session = session_box.get("session")
        if session is None:
            return CallResult(
                transcript=[],
                order_confirmed=False,
                escalated_to_agent=False,
                order_id=None,
                duration_seconds=duration,
                network_profile=scenario.get("network_profile", "clean"),
            )

        return CallResult(
            transcript=session.transcript,
            order_confirmed=session.state == IVRState.ORDER_COMPLETE,
            escalated_to_agent=session.escalated or session.state == IVRState.ESCALATING,
            order_id=session.order_id,
            duration_seconds=duration,
            network_profile=scenario.get("network_profile", "clean"),
        )

    # ── Caller driver ────────────────────────────────────────────────────────

    def _drive_caller(
        self,
        scenario: dict,
        caller_q: queue.Queue,
        ivr_q: queue.Queue,
    ):
        """
        For each scripted turn:
          1. Wait for IVR to finish speaking
          2. Synthesize the customer's line via edge-tts
          3. Put the audio into caller_q for the IVR to hear
        """
        turns = scenario.get("turns", [])
        voice = scenario.get("customer_voice", "en-US-JennyNeural")
        turn_index = 0

        while True:
            # Block until IVR plays a prompt (or signals end)
            try:
                ivr_audio = ivr_q.get(timeout=30)
            except queue.Empty:
                break

            if ivr_audio is None:
                break  # IVR hung up

            if turn_index >= len(turns):
                # No more scripted turns — send silence so IVR times out cleanly
                caller_q.put(np.zeros(8000 * 3, dtype=np.float32))
                break

            customer_text = turns[turn_index].get("customer_says", "")
            turn_index += 1

            audio = synthesize_to_array(customer_text, voice=voice)
            caller_q.put(audio)

    # ── Network profile ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_network_profile(scenario: dict) -> Optional[dict]:
        """Load the network profile dict from config.yaml, or None for clean."""
        profile_name = scenario.get("network_profile", "clean")
        if profile_name == "clean":
            return None

        import yaml
        from pathlib import Path
        config_path = Path(__file__).parent.parent.parent / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return config.get("network_profiles", {}).get(profile_name)
