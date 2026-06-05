import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf


SAMPLE_RATE = 8000  # telephony narrowband


def run_async(coro):
    """Run an async coroutine in a fresh event loop (safe to call from any thread)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def mp3_to_wav(mp3_path: str, wav_path: str, sample_rate: int = SAMPLE_RATE):
    """Convert MP3 to mono WAV at telephony sample rate via ffmpeg."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", mp3_path,
            "-ar", str(sample_rate),
            "-ac", "1",
            "-sample_fmt", "s16",
            wav_path,
        ],
        capture_output=True,
        check=True,
    )


def synthesize_to_array(text: str, voice: str = "en-US-AriaNeural") -> np.ndarray:
    """Synthesize text to a float32 numpy audio array using edge-tts."""
    import edge_tts

    async def _gen():
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
        wav_path = mp3_path.replace(".mp3", ".wav")
        try:
            communicate = edge_tts.Communicate(text, voice=voice)
            await communicate.save(mp3_path)
            mp3_to_wav(mp3_path, wav_path)
            audio, _ = sf.read(wav_path, dtype="float32")
            return audio
        finally:
            if os.path.exists(mp3_path):
                os.unlink(mp3_path)
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    return run_async(_gen())


def synthesize_to_file(text: str, out_path: str, voice: str = "en-US-AriaNeural"):
    """Synthesize text to a WAV file."""
    import edge_tts

    async def _gen():
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            mp3_path = f.name
        try:
            communicate = edge_tts.Communicate(text, voice=voice)
            await communicate.save(mp3_path)
            mp3_to_wav(mp3_path, out_path)
        finally:
            if os.path.exists(mp3_path):
                os.unlink(mp3_path)

    run_async(_gen())


def array_to_wav(audio: np.ndarray, path: str, sample_rate: int = SAMPLE_RATE):
    sf.write(path, audio, sample_rate)


def wav_to_array(path: str) -> np.ndarray:
    audio, _ = sf.read(path, dtype="float32")
    return audio


def apply_audio_profile(audio: np.ndarray, profile: dict) -> np.ndarray:
    """
    Degrade audio to simulate network/device conditions.
    profile keys: latency_ms, bandwidth_kbps, packet_loss_pct
    """
    result = audio.copy()

    # Simulate packet loss by zeroing random chunks
    loss_pct = profile.get("packet_loss_pct", 0.0)
    if loss_pct > 0:
        chunk_size = int(SAMPLE_RATE * 0.02)  # 20ms chunks (typical RTP frame)
        n_chunks = len(result) // chunk_size
        for i in range(n_chunks):
            if np.random.random() < (loss_pct / 100.0):
                start = i * chunk_size
                result[start: start + chunk_size] = 0.0

    # Simulate bandwidth limitation via noise (poor quality codec)
    bw = profile.get("bandwidth_kbps", 0)
    if bw > 0 and bw < 64:
        noise_level = (64 - bw) / 64 * 0.05
        result += np.random.normal(0, noise_level, len(result)).astype(np.float32)
        result = np.clip(result, -1.0, 1.0)

    return result
