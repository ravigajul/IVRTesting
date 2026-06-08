"""
Mock IVR server — runs in-process for local testing.

Architecture:
  - Caller and IVR exchange audio via Python queues (no SIP/RTP for local mode)
  - IVR uses a state machine (state_machine.py) to drive conversation flow
  - Each IVR turn: synthesize prompt → queue audio → wait for caller → transcribe → advance state
  - Prompts are pre-generated on first run and cached in audio/ivr_prompts/
"""

import os
import queue
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

from .audio_utils import apply_audio_profile, synthesize_to_file, wav_to_array
from .state_machine import TERMINAL_STATES, CallSession, IVRState, next_state

PROMPTS_DIR = Path(__file__).parent.parent / "audio" / "ivr_prompts"
IVR_VOICE = "en-US-AriaNeural"

# Static prompt texts — dynamic prompts (with {placeholders}) are generated at runtime
PROMPT_TEXTS = {
    "greeting": (
        "Thank you for calling. "
        "To place a new order, say new order. "
        "To speak with an agent, say agent."
    ),
    "ask_item": "What would you like to order today?",
    "ask_delivery": "Would you like delivery or pickup?",
    "order_placed": "Your order has been placed. Thank you for calling, goodbye.",
    "escalating": (
        "I'll connect you with a customer service representative right away. "
        "Please hold."
    ),
    "not_understood": "I'm sorry, I didn't catch that. Could you please repeat?",
    "goodbye": "Thank you for calling. Goodbye.",
}

# Prompts that require session data — built dynamically, not pre-cached
DYNAMIC_PROMPTS = {"confirm_order"}


class MockIVR:
    """
    In-process mock IVR. Designed to be called from LocalBackend.run_call().

    Usage:
        ivr = MockIVR()
        session = ivr.run_call(caller_q, ivr_q, scenario)
    """

    _whisper_lock = threading.Lock()  # one transcription at a time
    _whisper_model: Optional[WhisperModel] = None

    def __init__(self, whisper_model: str = "base"):
        self._whisper_model_name = whisper_model
        self._ensure_prompts_cached()

    # ── Startup ─────────────────────────────────────────────────────────────

    def _ensure_prompts_cached(self):
        PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        for key, text in PROMPT_TEXTS.items():
            path = PROMPTS_DIR / f"{key}.wav"
            if not path.exists():
                print(f"[MockIVR] Generating prompt: {key}")
                synthesize_to_file(text, str(path), voice=IVR_VOICE)

    def _get_whisper(self) -> WhisperModel:
        with self._whisper_lock:
            if MockIVR._whisper_model is None:
                MockIVR._whisper_model = WhisperModel(
                    self._whisper_model_name, device="cpu", compute_type="int8"
                )
            return MockIVR._whisper_model

    # ── Main call loop ───────────────────────────────────────────────────────

    def run_call(
        self,
        caller_audio_q: queue.Queue,
        ivr_audio_q: queue.Queue,
        network_profile: Optional[dict] = None,
        on_state_change: Optional[Callable[[CallSession], None]] = None,
        turn_timeout: float = 20.0,
        recording_path: Optional[str] = None,
    ) -> CallSession:
        """
        Drive a full IVR conversation.

        caller_audio_q: caller puts numpy audio arrays here
        ivr_audio_q:    IVR puts numpy audio arrays here; None signals end of call
        recording_path: if set, save a WAV of the full call (IVR + caller interleaved)
        Returns the completed CallSession with full transcript.
        """
        session = CallSession()
        audio_log: list = []  # ordered audio chunks for recording

        # Play opening greeting
        greeting_audio = self._play(session, ivr_audio_q, "greeting", network_profile)
        audio_log.append(greeting_audio)

        while session.state not in TERMINAL_STATES:
            # Wait for caller's next audio turn
            caller_audio = self._wait(caller_audio_q, timeout=turn_timeout)
            if caller_audio is None:
                break  # caller hung up or timed out

            if network_profile:
                caller_audio = apply_audio_profile(caller_audio, network_profile)

            audio_log.append(caller_audio)

            caller_text = self._transcribe(caller_audio)
            session.add_turn("caller", caller_text)

            state_before = session.state
            new_state, response_key = next_state(session, caller_text)
            session.state = new_state

            session.transitions.append({
                "turn": len(session.transitions) + 1,
                "state_before": state_before.value,
                "caller_text": caller_text,
                "response_key": response_key,
                "state_after": new_state.value,
            })

            if on_state_change:
                on_state_change(session)

            ivr_text = self._build_prompt_text(response_key, session)
            session.add_turn("ivr", ivr_text)
            ivr_audio = self._synthesize_prompt(response_key, ivr_text, network_profile)
            audio_log.append(ivr_audio)
            ivr_audio_q.put(ivr_audio)

        ivr_audio_q.put(None)  # signal end of call to caller

        if recording_path:
            self._save_recording(audio_log, recording_path)

        return session

    # ── Audio helpers ────────────────────────────────────────────────────────

    def _play(
        self,
        session: CallSession,
        ivr_audio_q: queue.Queue,
        key: str,
        network_profile: Optional[dict],
    ) -> np.ndarray:
        text = PROMPT_TEXTS[key]
        session.add_turn("ivr", text)
        audio = self._load_cached(key)
        if network_profile:
            audio = apply_audio_profile(audio, network_profile)
        ivr_audio_q.put(audio)
        return audio

    @staticmethod
    def _save_recording(audio_log: list, recording_path: str) -> None:
        """Concatenate all audio chunks with short gaps and write a WAV file."""
        gap = np.zeros(int(8000 * 0.35), dtype=np.float32)  # 350ms silence between turns
        chunks = []
        for i, chunk in enumerate(audio_log):
            chunks.append(chunk.astype(np.float32))
            if i < len(audio_log) - 1:
                chunks.append(gap)
        mixed = np.concatenate(chunks)
        Path(recording_path).parent.mkdir(parents=True, exist_ok=True)
        sf.write(recording_path, mixed, samplerate=8000)

    def _load_cached(self, key: str) -> np.ndarray:
        return wav_to_array(str(PROMPTS_DIR / f"{key}.wav"))

    def _build_prompt_text(self, key: str, session: CallSession) -> str:
        if key == "confirm_order":
            order = session.order_details or "your order"
            return f"I heard you'd like {order}. Is that correct? Say yes to confirm or no to change."
        if key == "order_placed":
            return (
                f"Your order number {session.order_id} has been placed successfully. "
                "Thank you for calling, goodbye."
            )
        return PROMPT_TEXTS.get(key, "")

    def _synthesize_prompt(
        self, key: str, text: str, network_profile: Optional[dict]
    ) -> np.ndarray:
        if key in DYNAMIC_PROMPTS or key == "order_placed":
            # Generate on the fly — dynamic content
            from .audio_utils import synthesize_to_array
            audio = synthesize_to_array(text, voice=IVR_VOICE)
        else:
            audio = self._load_cached(key)

        if network_profile:
            audio = apply_audio_profile(audio, network_profile)
        return audio

    def _transcribe(self, audio: np.ndarray) -> str:
        whisper = self._get_whisper()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        try:
            sf.write(tmp, audio, 8000)
            segments, _ = whisper.transcribe(tmp, language="en", beam_size=1)
            return " ".join(s.text.strip() for s in segments).strip()
        finally:
            os.unlink(tmp)

    @staticmethod
    def _wait(q: queue.Queue, timeout: float) -> Optional[np.ndarray]:
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None
