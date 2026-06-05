# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Always activate the venv first — the project uses Python 3.14 with a local venv:

```bash
source .venv/bin/activate
```

```bash
# Run all tests
python -m pytest

# Run only fast unit tests (no audio I/O, ~instant)
python -m pytest tests/test_state_machine.py -v

# Run audio pipeline tests (TTS + Whisper, ~30s)
python -m pytest tests/test_audio.py -v

# Run full end-to-end call tests (slowest, ~60-120s)
python -m pytest tests/test_end_to_end.py -v

# Run a single test
python -m pytest tests/test_state_machine.py::TestEscalation::test_explicit_agent_request -v

# Play an IVR prompt to hear it
afplay audio/ivr_prompts/greeting.wav
```

## Architecture

This is a **black-box IVR voice bot testing framework**. It simulates a human customer calling an IVR, evaluates whether the bot handled the conversation correctly, and checks escalation logic. It is NOT a voice bot itself.

### The three-layer design

```
Test (pytest scenario)
      ↓
TelephonyBackend  ←  swap this one layer to go from free local to real PSTN
      ↓
IVR target  ←  MockIVR in-process (local) or real phone number (PSTN)
```

**`CALL_BACKEND` env var** controls which backend runs. The test code never changes:
- `local` (default) — `LocalBackend` + `MockIVR`, free, no network
- `twilio` — `TwilioBackend`, real PSTN via Twilio
- `signalwire` — `SignalWireBackend`, Twilio-compatible

### Local call flow

`LocalBackend.place_call()` runs two threads:

- **IVR thread** (`MockIVR.run_call`): plays prompts → waits for caller audio → transcribes → advances state machine → loops
- **Caller thread** (`_drive_caller`): waits for IVR audio → synthesizes next scripted turn → sends audio

They communicate via two `queue.Queue` objects. `None` on the IVR queue signals end of call.

### State machine (`ivr_server/state_machine.py`)

`next_state(session, caller_input)` is a **pure function** — it returns `(new_state, response_key)` but does NOT update `session.state`. The caller (`MockIVR.run_call`) must assign `session.state = new_state` after each call. Getting this wrong is a common mistake.

Escalation is checked globally before any state-specific logic, so it triggers from any state.

`DENY_TRIGGERS` is checked before `CONFIRM_TRIGGERS` in `ORDER_CONFIRM` to prevent ambiguous phrases like "no not right" from confirming the order.

### Audio pipeline

All audio is **8 kHz mono float32** — narrowband telephony quality (G.711 equivalent). This is intentional.

`audio_utils.run_async()` creates a **fresh event loop per call** for edge-tts synthesis. This avoids conflicts between threads that each need async I/O. Do not use `asyncio.run()` directly in this codebase.

`MockIVR._whisper_model` is a **class-level singleton** protected by `_whisper_lock`. Whisper loads once and is reused across all tests.

IVR prompts are pre-generated into `audio/ivr_prompts/*.wav` on first `MockIVR()` instantiation. **Delete the directory to regenerate prompts** (e.g. after changing `PROMPT_TEXTS` or `IVR_VOICE`).

`apply_audio_profile()` degrades audio by zeroing random 20ms RTP-frame chunks (packet loss) and adding Gaussian noise (low bandwidth). Network profiles are defined in `config.yaml`.

### Scenario format

Scenarios are dicts (inline in tests today, will move to `scenarios/*.json`). Required keys:

```python
{
    "name": str,
    "network_profile": str,        # key from config.yaml network_profiles
    "customer_voice": str,         # edge-tts voice name
    "turns": [{"customer_says": str}, ...],
    "expected_outcomes": {
        "order_confirmed": bool,
        "escalated_to_agent": bool,
    }
}
```

### What is not yet implemented

- `TwilioBackend` and `SignalWireBackend` — stub files exist, `place_call` raises `NotImplementedError`
- `ScenarioRunner` — scenarios are driven inline by `LocalBackend._drive_caller`
- LLM evaluation (`deepeval` + Ollama) — package installed, integration not wired
- `scenarios/*.json` files — scenarios are currently hardcoded in test files
- Network simulation via macOS `dnctl`/`pfctl` — `apply_audio_profile` handles audio-layer degradation only

## Key dependencies

- `audioop-lts` — required polyfill; `pyVoIP` depends on `audioop` which was removed in Python 3.13
- `faster-whisper` — local Whisper via CTranslate2; model downloads on first use to `~/.cache/huggingface`
- `edge-tts` — Microsoft Edge neural TTS, requires internet, no API key
- `ollama` — must be running locally (`ollama serve`); `llama3.2` model used for evaluation
