"""
Quick script to place a real call via Twilio.
Usage:  python call.py
"""
from dotenv import load_dotenv
load_dotenv()

from harness.backends.factory import get_backend

SCENARIO = {
    "name": "real_call_dtmf_test",
    "network_profile": "clean",
    "customer_voice": "en-US-JennyNeural",
    "turns": [
        {"press": "3"},   # "To place a new order, please press 3"
    ],
}

TARGET = __import__("os").environ["TARGET_IVR_NUMBER"]

if __name__ == "__main__":
    print(f"Backend: {__import__('os').environ.get('CALL_BACKEND', 'local')}")
    print(f"Target:  {TARGET}")
    print()

    backend = get_backend()
    result  = backend.place_call(TARGET, SCENARIO)

    print("\n── Transcript ──────────────────────────────────")
    for turn in result.transcript:
        print(f"  [{turn['speaker']}] {turn['text']}")

    print("\n── Result ──────────────────────────────────────")
    print(f"  Order confirmed:    {result.order_confirmed}")
    print(f"  Escalated to agent: {result.escalated_to_agent}")
    print(f"  Order ID:           {result.order_id}")
    print(f"  Duration:           {result.duration_seconds:.1f}s")
    print(f"  Network profile:    {result.network_profile}")

    # Play back the recording so you can hear the call
    import glob, subprocess
    recordings = sorted(glob.glob("reports/call_*.mp3"))
    if recordings:
        latest = recordings[-1]
        print(f"\n── Playing recording: {latest} ──")
        subprocess.run(["afplay", latest])
