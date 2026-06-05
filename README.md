# IVR Testing Framework

A generic, open-source framework for automated black-box testing of IVR (Interactive Voice Response) and voice bot systems. Simulates a customer calling an IVR, evaluates whether the bot handles interactions correctly, and verifies escalation logic — without any paid SaaS tooling.

## What It Does

- Places automated outbound calls to an IVR (locally or via real PSTN)
- Drives the caller side with synthesized customer audio (Microsoft Edge TTS)
- Transcribes IVR responses locally using Whisper
- Evaluates conversation quality using a local LLM (Ollama)
- Simulates real-world call conditions: cellular, Bluetooth, packet loss, bandwidth limits
- Switches between free local testing and live PSTN calls by changing one environment variable

## Architecture

```
Test (pytest scenario)
        │
        ▼
TelephonyBackend          ← swap via CALL_BACKEND env var
        │
        ├── LocalBackend      free, in-process, no network
        ├── TwilioBackend     real PSTN via Twilio
        └── SignalWireBackend real PSTN via SignalWire (Twilio-compatible)
        │
        ▼
IVR Target (black box)    ← MockIVR locally, or any real phone number
```

### Local call flow

Two threads run concurrently and exchange audio via Python queues:

- **IVR thread** — `MockIVR` plays prompts, transcribes caller audio with Whisper, advances a state machine, loops
- **Caller thread** — synthesizes each scripted turn with edge-tts, sends audio to the IVR thread

For PSTN calls, `TwilioBackend` builds inline TwiML with `<Say>` or `<Play digits>` turns, initiates an outbound call, records it, then transcribes the recording with Whisper.

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.11+ (tested on 3.14)
- [Ollama](https://ollama.com) running locally with `llama3.2` pulled
- `ffmpeg` (`brew install ffmpeg`)
- `sipp` for load testing (`brew install sipp`)
- Internet access for edge-tts (Microsoft neural TTS, no API key needed)

## Setup

```bash
# Clone and enter the project
git clone https://github.com/ravigajul/IVRTesting.git
cd IVRTesting

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env
# Edit .env — for local testing, defaults work as-is

# Pull the LLM model (if not already pulled)
ollama pull llama3.2
```

## Running Tests

```bash
source .venv/bin/activate

# All tests
python -m pytest

# Fast unit tests only (no audio I/O, ~instant)
python -m pytest tests/test_state_machine.py -v

# Audio pipeline tests (TTS + Whisper, ~30s)
python -m pytest tests/test_audio.py -v

# Full end-to-end call tests (~60-120s)
python -m pytest tests/test_end_to_end.py -v

# Single test
python -m pytest tests/test_state_machine.py::TestEscalation::test_explicit_agent_request -v
```

## Making a Real PSTN Call

1. Create a [Twilio](https://twilio.com) account and buy a phone number
2. Fill in `.env`:

```env
CALL_BACKEND=twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_FROM_NUMBER=+1xxxxxxxxxx
TARGET_IVR_NUMBER=+1xxxxxxxxxx
```

3. Run the call script:

```bash
python call.py
```

The recording is saved to `reports/` and played back automatically after the call. Whisper transcribes both sides.

## Switching Backends

The entire test suite runs unchanged. Only `.env` changes:

| `CALL_BACKEND` | What happens |
|---|---|
| `local` | In-process MockIVR, free, no network |
| `twilio` | Real PSTN via Twilio SDK |
| `signalwire` | Real PSTN via SignalWire (Twilio-compatible) |

## Scenario Format

Scenarios are JSON-like dicts defining the customer side of a conversation:

```python
{
    "name": "happy_path_order",
    "network_profile": "clean",        # clean | wired | wifi_good | cellular_lte | cellular_poor | bluetooth | degraded
    "customer_voice": "en-US-JennyNeural",
    "turns": [
        {"press": "3"},                # DTMF — for traditional button-press IVRs
        {"customer_says": "Large pepperoni pizza please"},  # Speech — for voice AI bots
    ]
}
```

## Network Simulation Profiles

Applied to audio to simulate real-world conditions (macOS `dnctl`/`pfctl` or audio-layer degradation):

| Profile | Latency | Bandwidth | Packet Loss | Represents |
|---|---|---|---|---|
| `clean` | 0ms | unlimited | 0% | VoIP / wired LAN |
| `wired` | 20ms | 1Mbps | 0.1% | Landline |
| `wifi_good` | 40ms | 512kbps | 0.5% | Good WiFi |
| `cellular_lte` | 120ms | 128kbps | 2% | LTE mobile |
| `cellular_poor` | 200ms | 64kbps | 5% | Weak signal |
| `bluetooth` | 60ms | 256kbps | 1% | Bluetooth headset |
| `degraded` | 300ms | 32kbps | 10% | Edge case |

## Project Structure

```
ivr_server/
  state_machine.py   IVR state machine (pure function, 7 states)
  mock_ivr.py        In-process mock IVR server
  audio_utils.py     TTS synthesis, audio conversion, network degradation

harness/
  backends/
    base.py            TelephonyBackend ABC + CallResult dataclass
    local_backend.py   In-process backend (no SIP/PSTN)
    twilio_backend.py  Twilio PSTN backend
    factory.py         Reads CALL_BACKEND, returns correct backend

tests/
  test_state_machine.py   Unit tests — instant, no audio
  test_audio.py           TTS + Whisper pipeline tests
  test_end_to_end.py      Full call loop via LocalBackend

scenarios/             JSON scenario files (in progress)
reports/               Call recordings — gitignored
audio/ivr_prompts/     Cached IVR WAV prompts — gitignored, regenerated on first run
```

## Key Notes

- `audio/ivr_prompts/` is gitignored — prompts are generated on first `MockIVR()` instantiation via edge-tts and cached locally
- `reports/` is gitignored — call recordings may contain sensitive conversation data
- `audioop-lts` polyfill is required because `pyVoIP` depends on `audioop` which was removed in Python 3.13+
- `next_state()` in `state_machine.py` is a pure function — the caller must update `session.state` after each call
- All audio is 8kHz mono (narrowband telephony quality) — intentional, matches real phone calls

## Stack

| Component | Tool |
|---|---|
| TTS | [edge-tts](https://github.com/rany2/edge-tts) — Microsoft neural voices, no API key |
| STT | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — local, no API |
| LLM evaluation | [Ollama](https://ollama.com) + Llama 3.2 — local, no API |
| Evaluation framework | [DeepEval](https://github.com/confident-ai/deepeval) |
| Audio processing | ffmpeg + soundfile + numpy |
| PSTN calls | Twilio / SignalWire |
| Load testing | [SIPp](https://sipp.sourceforge.net) |
