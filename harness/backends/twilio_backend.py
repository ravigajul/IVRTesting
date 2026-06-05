"""
TwilioBackend — real outbound PSTN calls via Twilio.

Simplified approach: pass TwiML inline to the call API.
No local server, no SSH tunnel, no audio hosting needed.

Call flow:
  1. Build TwiML with <Say> turns + <Pause> gaps for IVR to respond
  2. Initiate call — Twilio executes TwiML, records the call
  3. Poll until call completes
  4. Download recording → transcribe with Whisper → return CallResult
"""

import os
import re
import tempfile
import time
import xml.sax.saxutils as saxutils
from typing import Optional

from twilio.rest import Client

from .base import CallResult, TelephonyBackend
from ivr_server.audio_utils import mp3_to_wav

# How long to wait (seconds) for the IVR to finish speaking
GREETING_PAUSE = 25  # Papa John's greeting + full menu takes ~22s
RESPONSE_PAUSE = 10  # subsequent IVR responses

# Map edge-tts voice names → Twilio/Polly voices
VOICE_MAP = {
    "en-US-JennyNeural":  "Polly.Joanna",
    "en-US-GuyNeural":    "Polly.Matthew",
    "en-US-AriaNeural":   "Polly.Joanna",
    "en-GB-SoniaNeural":  "Polly.Amy",
    "en-IN-NeerjaNeural": "Polly.Aditi",
}


class TwilioBackend(TelephonyBackend):

    def __init__(self):
        self.account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        self.auth_token  = os.environ["TWILIO_AUTH_TOKEN"]
        self.from_number = os.environ["TWILIO_FROM_NUMBER"]
        self.client = Client(self.account_sid, self.auth_token)

    # ── Main entry point ─────────────────────────────────────────────────────

    def place_call(self, target: str, scenario: dict) -> CallResult:
        turns  = scenario.get("turns", [])
        voice  = scenario.get("customer_voice", "en-US-JennyNeural")
        t_voice = VOICE_MAP.get(voice, "Polly.Joanna")

        # 1. Build TwiML inline — no server or tunnel needed
        twiml = self._build_twiml(turns, t_voice)
        print(f"\n[Twilio] TwiML ready ({len(twiml)} chars)")
        print(f"[Twilio] Calling {target} from {self.from_number}...")

        start = time.monotonic()

        # 2. Initiate call
        call = self.client.calls.create(
            to=target,
            from_=self.from_number,
            twiml=twiml,
            record=True,
        )
        print(f"[Twilio] Call SID: {call.sid}")
        print("[Twilio] Call in progress — waiting for completion...")

        # 3. Poll until done
        status = self._poll_call(call.sid, timeout=300)
        duration = time.monotonic() - start
        print(f"[Twilio] Finished — status: {status}  duration: {duration:.0f}s")

        # 4. Get recording (Twilio needs ~30s to process it)
        print("[Twilio] Waiting for recording to be ready...")
        recording_url = self._get_recording(call.sid, timeout=90)

        # 5. Transcribe
        transcript = []
        if recording_url:
            print("[Twilio] Transcribing with Whisper...")
            transcript = self._transcribe(recording_url)
            print(f"[Twilio] {len(transcript)} segments transcribed")
        else:
            print("[Twilio] Warning: no recording found — call may have failed")

        full_text = " ".join(t["text"] for t in transcript).lower()
        return CallResult(
            transcript=transcript,
            order_confirmed=self._check_confirmed(full_text),
            escalated_to_agent=self._check_escalated(full_text),
            order_id=self._extract_order_id(full_text),
            duration_seconds=duration,
            network_profile=scenario.get("network_profile", "clean"),
        )

    # ── TwiML builder ────────────────────────────────────────────────────────

    @staticmethod
    def _build_twiml(turns: list, voice: str) -> str:
        """
        Each turn can have either:
          "press": "3"           → sends DTMF tone (for traditional button-press IVRs)
          "customer_says": "..." → speaks text (for voice AI bots)
        """
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<Response>"]
        lines.append(f'  <Pause length="{GREETING_PAUSE}"/>')
        for i, turn in enumerate(turns):
            if "press" in turn:
                lines.append(f'  <Play digits="{turn["press"]}"/>')
            else:
                text = saxutils.escape(turn.get("customer_says", ""))
                lines.append(f'  <Say voice="{voice}">{text}</Say>')
            if i < len(turns) - 1:
                lines.append(f'  <Pause length="{RESPONSE_PAUSE}"/>')
        lines.append(f'  <Pause length="20"/>')
        lines.append("</Response>")
        return "\n".join(lines)

    # ── Twilio polling ───────────────────────────────────────────────────────

    def _poll_call(self, call_sid: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.client.calls(call_sid).fetch().status
            print(f"  status: {status}")
            if status in ("completed", "failed", "busy", "no-answer", "canceled"):
                return status
            time.sleep(5)
        return "timeout"

    def _get_recording(self, call_sid: str, timeout: float) -> Optional[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            recs = self.client.recordings.list(call_sid=call_sid, limit=1)
            if recs:
                uri = recs[0].uri.replace(".json", ".mp3")
                return f"https://api.twilio.com{uri}"
            time.sleep(5)
        return None

    # ── Transcription ────────────────────────────────────────────────────────

    def _transcribe(self, recording_url: str) -> list:
        import requests
        from pathlib import Path
        from faster_whisper import WhisperModel

        # Twilio lists the recording before the media file is fully written — retry
        resp = None
        for attempt in range(6):
            r = requests.get(recording_url, auth=(self.account_sid, self.auth_token))
            if r.status_code == 200:
                resp = r
                break
            alt_url = recording_url.replace(".mp3", ".wav")
            r2 = requests.get(alt_url, auth=(self.account_sid, self.auth_token))
            if r2.status_code == 200:
                resp = r2
                recording_url = alt_url
                break
            print(f"  Recording not ready yet (attempt {attempt+1}/6) — waiting 10s...")
            time.sleep(10)
        if resp is None:
            raise RuntimeError(f"Could not download recording after retries: {recording_url}")
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(resp.content)
            mp3_path = f.name

        # Save recording to reports/ so you can replay it
        recordings_dir = Path(__file__).parent.parent.parent / "reports"
        recordings_dir.mkdir(exist_ok=True)
        saved_mp3 = recordings_dir / f"call_{int(time.time())}.mp3"
        import shutil
        with open(saved_mp3, "wb") as f:
            f.write(resp.content)
        print(f"[Twilio] Recording saved → {saved_mp3}")

        wav_path = str(saved_mp3).replace(".mp3", ".wav")
        try:
            mp3_to_wav(str(saved_mp3), wav_path, sample_rate=8000)
            model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, _ = model.transcribe(wav_path, language="en")
            return [
                {
                    "speaker": "call",
                    "text": s.text.strip(),
                    "start": round(s.start, 1),
                    "end":   round(s.end, 1),
                }
                for s in segments if s.text.strip()
            ]
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    # ── Result helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _check_confirmed(text: str) -> bool:
        return any(kw in text for kw in [
            "order confirmed", "order placed", "order number",
            "your order", "confirmation number",
        ])

    @staticmethod
    def _check_escalated(text: str) -> bool:
        return any(kw in text for kw in [
            "representative", "agent", "hold", "connecting",
            "transfer", "one moment", "specialist",
        ])

    @staticmethod
    def _extract_order_id(text: str) -> Optional[str]:
        m = re.search(r"order\s+(?:number\s+)?#?(\w{4,})", text, re.IGNORECASE)
        return m.group(1) if m else None
