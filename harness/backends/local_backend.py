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
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from .base import CallResult, TelephonyBackend, TurnDetail
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

        # Build a recording path in reports/ alongside the HTML report
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = scenario.get("name", "call").replace(" ", "_")
        reports_dir = Path(__file__).parent.parent.parent / "reports"
        recording_path = str(reports_dir / f"call_{safe_name}_{ts}.wav")

        # Run IVR in background thread
        def _run_ivr():
            session = self._ivr.run_call(
                caller_audio_q=caller_q,
                ivr_audio_q=ivr_q,
                network_profile=network_profile,
                recording_path=recording_path,
            )
            session_box["session"] = session

        ivr_thread = threading.Thread(target=_run_ivr, daemon=True)
        ivr_thread.start()

        # Drive the caller side — returns list of scripted texts in turn order
        scripted_turns = self._drive_caller(scenario, caller_q, ivr_q)

        ivr_thread.join(timeout=120)
        duration = time.monotonic() - start

        session = session_box.get("session")
        saved_recording = recording_path if Path(recording_path).exists() else None

        if session is None:
            return CallResult(
                transcript=[],
                order_confirmed=False,
                escalated_to_agent=False,
                order_id=None,
                duration_seconds=duration,
                network_profile=scenario.get("network_profile", "clean"),
                scenario_name=scenario.get("name", ""),
                final_state="unknown",
                recording_path=saved_recording,
            )

        turns_detail = self._build_turns_detail(scripted_turns, session)
        return CallResult(
            transcript=session.transcript,
            order_confirmed=session.state == IVRState.ORDER_COMPLETE,
            escalated_to_agent=session.escalated or session.state == IVRState.ESCALATING,
            order_id=session.order_id,
            duration_seconds=duration,
            network_profile=scenario.get("network_profile", "clean"),
            scenario_name=scenario.get("name", ""),
            final_state=session.state.value,
            turns_detail=turns_detail,
            recording_path=saved_recording,
        )

    # ── Caller driver ────────────────────────────────────────────────────────

    def _drive_caller(
        self,
        scenario: dict,
        caller_q: queue.Queue,
        ivr_q: queue.Queue,
    ) -> list:
        """
        For each scripted turn:
          1. Wait for IVR to finish speaking
          2. Synthesize the customer's line via edge-tts (or send silence for DTMF)
          3. Put the audio into caller_q for the IVR to hear

        Returns the ordered list of scripted texts that were sent, so they can
        be correlated with the IVR's transcription log for reporting.
        """
        turns = scenario.get("turns", [])
        voice = scenario.get("customer_voice", "en-US-JennyNeural")
        turn_index = 0
        scripted: list = []

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

            turn = turns[turn_index]
            turn_index += 1

            if "press" in turn:
                dtmf = turn["press"]
                scripted.append(f"[DTMF: {dtmf}]")
                # DTMF not yet wired in LocalBackend — send silence as placeholder
                caller_q.put(np.zeros(8000, dtype=np.float32))
            else:
                customer_text = turn.get("customer_says", "")
                scripted.append(customer_text)
                audio = synthesize_to_array(customer_text, voice=voice)
                caller_q.put(audio)

        return scripted

    @staticmethod
    def _build_turns_detail(scripted_turns: list, session) -> list:
        details = []
        for i, t in enumerate(session.transitions):
            scripted = scripted_turns[i] if i < len(scripted_turns) else ""
            details.append(TurnDetail(
                turn_num=t["turn"],
                state_before=t["state_before"],
                scripted_text=scripted,
                transcribed_text=t["caller_text"],
                response_key=t["response_key"],
                state_after=t["state_after"],
            ))
        return details

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
