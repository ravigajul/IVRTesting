"""Tests for audio synthesis and transcription pipeline."""
import os
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ivr_server.audio_utils import synthesize_to_array, synthesize_to_file, apply_audio_profile


class TestSynthesis:
    def test_synthesize_to_array_returns_audio(self):
        audio = synthesize_to_array("Hello, this is a test.", voice="en-US-JennyNeural")
        assert isinstance(audio, np.ndarray)
        assert len(audio) > 0
        assert audio.dtype == np.float32

    def test_synthesize_to_file_creates_wav(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        try:
            synthesize_to_file("Testing audio output.", path)
            assert os.path.exists(path)
            audio, sr = sf.read(path)
            assert sr == 8000
            assert len(audio) > 0
        finally:
            os.unlink(path)

    def test_different_voices_produce_audio(self):
        voices = ["en-US-JennyNeural", "en-US-GuyNeural", "en-GB-SoniaNeural"]
        for voice in voices:
            audio = synthesize_to_array("I'd like a pizza please.", voice=voice)
            assert len(audio) > 0, f"No audio produced for {voice}"

    def test_longer_text_produces_more_audio(self):
        short = synthesize_to_array("Hi.")
        long = synthesize_to_array(
            "Thank you for calling. To place a new order say new order. "
            "To speak with an agent say agent."
        )
        assert len(long) > len(short)


class TestNetworkProfiles:
    def test_packet_loss_zeroes_chunks(self):
        audio = np.ones(8000 * 3, dtype=np.float32)
        profile = {"packet_loss_pct": 50.0, "bandwidth_kbps": 0}
        degraded = apply_audio_profile(audio, profile)
        # With 50% loss some chunks should be zeroed
        assert np.any(degraded == 0.0)

    def test_clean_profile_unchanged(self):
        audio = synthesize_to_array("Hello world.")
        degraded = apply_audio_profile(audio, {"packet_loss_pct": 0.0, "bandwidth_kbps": 0})
        np.testing.assert_array_equal(audio, degraded)

    def test_low_bandwidth_adds_noise(self):
        audio = np.zeros(8000 * 2, dtype=np.float32)
        degraded = apply_audio_profile(audio, {"packet_loss_pct": 0.0, "bandwidth_kbps": 16})
        # Noise should make the silent audio non-zero
        assert not np.all(degraded == 0.0)


class TestTranscription:
    def test_transcribe_synthesized_speech(self):
        """Synthesize a clear phrase and verify Whisper transcribes it recognisably."""
        from faster_whisper import WhisperModel
        import tempfile

        model = WhisperModel("base", device="cpu", compute_type="int8")
        audio = synthesize_to_array("I would like to place a new order please.")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio, 8000)
            segments, _ = model.transcribe(f.name, language="en", beam_size=1)
            transcript = " ".join(s.text.strip() for s in segments).lower()
            os.unlink(f.name)

        assert "order" in transcript, f"Expected 'order' in transcript, got: '{transcript}'"

    def test_transcribe_escalation_phrase(self):
        from faster_whisper import WhisperModel
        import tempfile

        model = WhisperModel("base", device="cpu", compute_type="int8")
        audio = synthesize_to_array("I want to speak to an agent please.")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio, 8000)
            segments, _ = model.transcribe(f.name, language="en", beam_size=1)
            transcript = " ".join(s.text.strip() for s in segments).lower()
            os.unlink(f.name)

        assert "agent" in transcript, f"Expected 'agent' in transcript, got: '{transcript}'"
