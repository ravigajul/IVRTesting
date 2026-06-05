# IVR Voice Bot Testing Framework — PoC Plan

## What We Are Building

A **generic, open-source, black-box IVR/voice bot testing framework** that:
- Simulates a real customer making a phone call to an IVR/voice bot
- Dynamically responds to what the bot says (not just plays static audio)
- Evaluates whether the bot handled the interaction correctly
- Tests escalation logic (bot → human agent handoff)
- Simulates real-world call conditions (Bluetooth, cellular, wired, bad signal)
- Can run free and locally, with a one-line switch to real PSTN calls when needed

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| No Docker | Native Mac installs via Homebrew + pip | Docker Desktop not free for enterprise |
| No paid APIs | All open-source tools | Minimize licensing cost for PoC |
| No PSTN for PoC | Local SIP loopback via Asterisk | Free, no per-call cost |
| Swappable telephony | Backend abstraction layer | Easy switch to real PSTN when ready |
| Network simulation | `comcast` tool (Mac dummynet wrapper) | NetEm is Linux-only |
| TTS | edge-tts | Neural quality, free, no API key, `pip install` |
| STT | faster-whisper | Open source, runs locally, good accuracy |
| LLM evaluation | Ollama + Llama3 | Runs natively on Mac (Apple Silicon optimized) |

## Architecture

```
ScenarioRunner (pytest)
      │
      ├── edge-tts          → generates customer voice audio per turn
      ├── faster-whisper    → transcribes IVR bot responses in real-time
      ├── Ollama (Llama3)   → decides next customer response if bot deviates
      ├── DeepEval          → LLM-as-Judge evaluation of full transcript
      │
      └── TelephonyBackend  ← swappable via CALL_BACKEND env var
              │
              ├── LocalSIPBackend    → pjsua2 → Asterisk (free, local)
              ├── TwilioBackend      → Twilio SDK → real PSTN
              └── SignalWireBackend  → SignalWire (Twilio-compatible, free trial)
```

### Call Flow

```
pytest scenario
    │
    ├── apply network profile (comcast)
    ├── synthesize customer turn (edge-tts)
    │
    ▼
TelephonyBackend.place_call()
    │
    ▼
Target IVR (Asterisk local / real number)   ← BLACK BOX
    │
    ├── bot response → faster-whisper → transcript
    ├── Ollama decides next customer turn
    └── repeat until call ends
    │
    ▼
Post-call Evaluation (DeepEval + Ollama)
    ├── Was order/intent captured correctly?
    ├── Was escalation triggered when it should have been?
    ├── Was escalation NOT triggered when it shouldn't have been?
    └── Was the conversation handled naturally?
```

## Tool Stack

| Layer | Tool | Install |
|---|---|---|
| Test IVR target (local) | Asterisk | `brew install asterisk` |
| LLM (evaluation + turn decisions) | Ollama + Llama3 | Ollama.app (native Mac) |
| TTS — customer voice | edge-tts | `pip install edge-tts` |
| STT — transcribe IVR | faster-whisper | `pip install faster-whisper` |
| SIP client + mock IVR | pyVoIP | `pip install pyVoIP` (pure Python, no brew needed) |
| Evaluation framework | DeepEval | `pip install deepeval` |
| Network simulation | macOS dnctl + pfctl | Built-in to macOS — no install needed |
| Audio manipulation | ffmpeg | Already installed |
| Test framework | pytest | `pip install pytest` |
| Load testing | sipp | `brew install sipp` (already installed) |
| PSTN fallback option A | Twilio | `pip install twilio` |
| PSTN fallback option B | SignalWire | `pip install signalwire` (Twilio-compatible) |

## Project Structure

```
ivr-test-framework/
├── PLAN.md                         # this file
├── README.md
├── requirements.txt
├── config.yaml                     # single place to switch backends/profiles
├── .env.example                    # env var template
├── supervisord.conf                # starts Asterisk + Ollama as background services
│
├── ivr_server/
│   ├── mock_ivr.py                 # pyVoIP-based mock IVR server (test target)
│   └── scenarios/                  # IVR response scripts (what the mock IVR says)
│
├── harness/
│   ├── backends/
│   │   ├── base.py                 # TelephonyBackend abstract class
│   │   ├── local_sip.py            # pjsua2 → Asterisk
│   │   ├── twilio_backend.py       # Twilio SDK → PSTN
│   │   ├── signalwire_backend.py   # SignalWire → PSTN
│   │   └── factory.py              # reads CALL_BACKEND, returns correct backend
│   │
│   ├── synthesizer.py              # edge-tts wrapper
│   ├── transcriber.py              # faster-whisper wrapper
│   ├── evaluator.py                # DeepEval + Ollama judge
│   ├── network_sim.py              # comcast wrapper (Mac)
│   └── runner.py                   # ScenarioRunner — orchestrates a full call
│
├── scenarios/
│   ├── happy_path.json             # simple successful order
│   ├── order_modification.json     # change item mid-order
│   ├── escalation_complaint.json   # complaint → should escalate
│   ├── escalation_explicit.json    # "speak to a person" → should escalate
│   ├── no_escalation.json          # minor comment → should NOT escalate
│   └── edge_cases.json             # hesitation, unclear speech, repeated misunderstanding
│
└── tests/
    └── test_ivr.py                 # pytest test cases (backend-agnostic)
```

## Scenario Format

Each scenario is a JSON file defining:

```json
{
  "name": "happy_path_order",
  "description": "Customer places a simple order successfully",
  "network_profile": "clean",
  "customer_voice": "en-US-JennyNeural",
  "turns": [
    {
      "trigger": "opening_greeting",
      "customer_says": "I'd like to place an order please"
    },
    {
      "trigger": "ask_for_item",
      "customer_says": "I want a large pepperoni pizza"
    },
    {
      "trigger": "ask_delivery_or_pickup",
      "customer_says": "Delivery please"
    }
  ],
  "expected_outcomes": {
    "order_confirmed": true,
    "escalated_to_agent": false,
    "evaluation_checks": [
      "Was the item captured correctly?",
      "Was delivery vs pickup correctly identified?",
      "Did the bot confirm the order before ending the call?"
    ]
  }
}
```

## Network Simulation Profiles (Mac via comcast)

| Profile | Latency | Bandwidth | Packet Loss | Represents |
|---|---|---|---|---|
| `clean` | 0ms | unlimited | 0% | VoIP desktop / wired LAN |
| `wired` | 20ms | 1Mbps | 0.1% | Landline / ethernet |
| `wifi_good` | 40ms | 512kbps | 0.5% | Good WiFi |
| `cellular_lte` | 120ms | 128kbps | 2% | LTE mobile |
| `cellular_poor` | 200ms | 64kbps | 5% | Weak cellular signal |
| `bluetooth` | 60ms | 256kbps | 1% | Bluetooth headset |
| `degraded` | 300ms | 32kbps | 10% | Edge case / bad signal |

## Switching to Real PSTN Calls

The entire test suite runs unchanged. Only one thing changes:

```bash
# Local (free)
export CALL_BACKEND=local
export TARGET="sip:1000@127.0.0.1"

# Real PSTN via Twilio
export CALL_BACKEND=twilio
export TWILIO_ACCOUNT_SID=ACxxxxxxx
export TWILIO_AUTH_TOKEN=xxxxxxx
export TWILIO_FROM_NUMBER=+1xxxxxxxxxx
export TARGET=+1xxxxxxxxxx          # real IVR phone number

# Real PSTN via SignalWire (Twilio-compatible, more free credit)
export CALL_BACKEND=signalwire
export SW_PROJECT_ID=xxxxxxx
export SW_TOKEN=xxxxxxx
export SW_SPACE_URL=yourspace.signalwire.com
export SW_FROM_NUMBER=+1xxxxxxxxxx
export TARGET=+1xxxxxxxxxx
```

## Evaluation Criteria (LLM-as-Judge via DeepEval + Ollama)

Every call is scored on:

| Check | Pass Threshold |
|---|---|
| Intent/order captured correctly | ≥ 0.85 |
| Escalation triggered when required | ≥ 0.90 |
| No false escalations | ≥ 0.95 |
| Clarification asked appropriately | ≥ 0.80 |
| Conversation handled naturally (no loops/crashes) | ≥ 0.85 |

## Order / Intent Verification

Since we are in PoC phase and the verification method is not yet defined, two options are supported:

1. **Transcript parsing** (default) — parse the bot's verbal confirmation from the transcript using regex patterns (`"order number"`, `"confirmed"`, `"your order has been placed"`)
2. **Email confirmation** — read confirmation email from test inbox using Python's built-in `imaplib` (no external tool needed)

## What Is NOT In Scope (PoC)

- Building the IVR/voice bot itself (we test a black box)
- Full CI/CD pipeline integration (can be added post-PoC)
- Load/stress testing (SIPp available but deferred — coordinate concurrent call limits with the IVR owner first)
- Real-time dashboards (results written to JSON, can be visualized post-PoC)

## Build Order

1. **Environment setup** — Homebrew installs, Python venv, Asterisk config, Ollama + model pull
2. **Asterisk dialplan** — generic ordering IVR (the test target for local runs)
3. **TelephonyBackend abstraction** — base class + LocalSIPBackend first
4. **Synthesizer + Transcriber** — edge-tts + faster-whisper
5. **ScenarioRunner** — orchestrates a full call end-to-end
6. **Evaluator** — DeepEval + Ollama judge
7. **First passing test** — happy path, local SIP, clean network
8. **Scenario library** — escalation, edge cases, modifications
9. **Network simulation** — comcast profiles wired into ScenarioRunner
10. **Twilio + SignalWire backends** — for PSTN switch