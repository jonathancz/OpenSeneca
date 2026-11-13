"""Helpers for the Discord channel: reply chunking and voice messages.

Everything here is pure (no network, no Discord client) so it can be unit
tested offline. Audio work uses PyAV, whose wheels bundle libopus, so no
ffmpeg binary is needed.

A Discord voice message is a message with ``flags = 8192``
(``IS_VOICE_MESSAGE``) and exactly one OGG Opus attachment that carries
``duration_secs`` and a base64 ``waveform``; it may not have text content,
embeds or other files.
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from typing import Any, Dict, List

DISCORD_MESSAGE_LIMIT = 2000
VOICE_MESSAGE_FLAG = 1 << 13  # 8192, IS_VOICE_MESSAGE
VOICE_SAMPLE_RATE = 48000
VOICE_BITRATE = 32000
VOICE_FILENAME = "voice-message.ogg"
WAVEFORM_POINTS = 256

_OPUS_FRAME = 960  # 20 ms at 48 kHz
_FENCE = "```"


# ---------------------------------------------------------------------------
# Text chunking
# ---------------------------------------------------------------------------


def _best_cut(window: str, limit: int) -> int:
    """Return where to cut *window* (at most ``limit`` chars) for one chunk.

    Prefers paragraph breaks, then line breaks, then sentence ends, then
    spaces; falls back to a hard cut. A boundary must leave the chunk at
    least a third full so a tiny leading paragraph does not waste a message.
    """
    floor = limit // 3
    for sep in ("\n\n", "\n"):
        idx = window.rfind(sep, 0, limit)
        if idx >= floor:
            return idx
    for match in reversed(list(re.finditer(r"[.!?](\s)", window[:limit]))):
        if match.start(1) >= floor:
            return match.start(1)
    idx = window.rfind(" ", 0, limit)
    if idx >= floor:
        return idx
    return limit


def chunk_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> List[str]:
    """Split *text* into Discord-sized chunks on paragraph boundaries.

    Every chunk is at most ``limit`` characters. A code fence left open at a
    cut is closed in that chunk and reopened in the next, so code blocks keep
    rendering.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    # Room for a closing "\n```" and a reopening "```lang\n" on each chunk.
    budget = limit - 24
    chunks: List[str] = []
    remaining = text
    reopen = ""
    while remaining:
        body = reopen + remaining
        if len(body) <= limit:
            chunks.append(body)
            break
        cut = _best_cut(body, budget)
        if reopen and cut <= len(reopen):
            cut = budget
        chunk = body[:cut].rstrip()
        rest = body[cut:].lstrip("\n").lstrip(" ")
        reopen = ""
        fences = re.findall(r"^```(\S*)", chunk, flags=re.MULTILINE)
        if len(fences) % 2 == 1:
            chunk = chunk + "\n" + _FENCE
            reopen = _FENCE + fences[-1] + "\n"
        chunks.append(chunk)
        remaining = rest
    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# Speech text
# ---------------------------------------------------------------------------


def text_for_speech(text: str) -> str:
    """Strip markdown that sounds wrong when read aloud."""
    out = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.DOTALL)
    out = re.sub(r"`([^`]*)`", r"\1", out)
    out = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", out)
    out = re.sub(r"<?https?://\S+>?", "link", out)
    out = re.sub(r"^\s{0,3}#{1,6}\s*", "", out, flags=re.MULTILINE)
    out = re.sub(r"^\s*[-*+]\s+", "", out, flags=re.MULTILINE)
    out = re.sub(r"(\*\*|__|\*|_|~~)", "", out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# ---------------------------------------------------------------------------
# Audio: decode, encode, waveform
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceClip:
    """An encoded Discord voice message."""

    ogg: bytes
    duration_secs: float
    waveform: str  # base64 of up to 256 bytes, 0..255


def decode_to_pcm(audio: bytes, rate: int = VOICE_SAMPLE_RATE) -> Any:
    """Decode any audio PyAV can read into mono float32 PCM at *rate*."""
    import av
    import numpy as np

    parts = []
    with av.open(io.BytesIO(audio)) as container:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32, copy=False)


def probe_duration(audio: bytes) -> float:
    """Return the duration of *audio* in seconds, or 0.0 if unknown."""
    import av

    try:
        with av.open(io.BytesIO(audio)) as container:
            if container.duration:
                return container.duration / av.time_base
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except Exception:
        return 0.0
    return 0.0


def encode_ogg_opus(pcm: Any, *, bitrate: int = VOICE_BITRATE) -> bytes:
    """Encode mono float32 PCM at 48 kHz as OGG Opus."""
    import av
    import numpy as np

    pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=VOICE_SAMPLE_RATE, layout="mono")
        stream.bit_rate = bitrate
        for start in range(0, len(pcm), _OPUS_FRAME):
            block = pcm[start : start + _OPUS_FRAME]
            if len(block) < _OPUS_FRAME:
                block = np.pad(block, (0, _OPUS_FRAME - len(block)))
            frame = av.AudioFrame.from_ndarray(
                block.reshape(1, -1), format="flt", layout="mono"
            )
            frame.sample_rate = VOICE_SAMPLE_RATE
            frame.pts = start
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def compute_waveform(pcm: Any, points: int = WAVEFORM_POINTS) -> str:
    """Return the base64 waveform Discord draws for a voice message."""
    import numpy as np

    samples = np.abs(np.asarray(pcm, dtype=np.float32).reshape(-1))
    if samples.size == 0:
        return base64.b64encode(bytes(points)).decode("ascii")
    if samples.size < points:
        samples = np.pad(samples, (0, points - samples.size))
    peaks = np.array([b.max() for b in np.array_split(samples, points)])
    top = float(peaks.max())
    if top <= 0:
        scaled = np.zeros(points, dtype=np.uint8)
    else:
        scaled = np.clip(peaks / top * 255, 0, 255).astype(np.uint8)
    return base64.b64encode(scaled.tobytes()).decode("ascii")


LIMITER_THRESHOLD = 0.9


def apply_gain(pcm: Any, gain: float) -> Any:
    """Scale *pcm* by *gain*, then soft-limit peaks so they never clip.

    Samples below ``LIMITER_THRESHOLD`` are scaled linearly; anything above
    is compressed smoothly (tanh knee) towards, but never past, full scale,
    so a louder voice does not distort.
    """
    import numpy as np

    out = np.asarray(pcm, dtype=np.float32) * np.float32(gain)
    if gain == 1.0:
        return out
    mag = np.abs(out)
    over = mag > LIMITER_THRESHOLD
    if np.any(over):
        headroom = 1.0 - LIMITER_THRESHOLD
        limited = LIMITER_THRESHOLD + headroom * np.tanh(
            (mag[over] - LIMITER_THRESHOLD) / headroom
        )
        out[over] = np.sign(out[over]) * limited
    return out.astype(np.float32, copy=False)


def build_voice_clip(audio: bytes, *, gain: float = 1.0) -> VoiceClip:
    """Turn TTS output (WAV, MP3, ...) into a Discord voice message clip.

    ``gain`` scales the voice (1.2 is about +1.6 dB) behind a soft limiter.
    """
    pcm = decode_to_pcm(audio)
    if pcm.size == 0:
        raise ValueError("TTS produced no audio")
    if gain != 1.0:
        pcm = apply_gain(pcm, gain)
    return VoiceClip(
        ogg=encode_ogg_opus(pcm),
        duration_secs=round(len(pcm) / VOICE_SAMPLE_RATE, 2),
        waveform=compute_waveform(pcm),
    )


# ---------------------------------------------------------------------------
# Discord REST payloads
# ---------------------------------------------------------------------------


def upload_slot_payload(clip: VoiceClip) -> Dict[str, Any]:
    """Body for ``POST /channels/{id}/attachments``."""
    return {
        "files": [{"id": "0", "filename": VOICE_FILENAME, "file_size": len(clip.ogg)}]
    }


def voice_message_payload(upload_filename: str, clip: VoiceClip) -> Dict[str, Any]:
    """Body for ``POST /channels/{id}/messages`` that posts a voice message."""
    return {
        "flags": VOICE_MESSAGE_FLAG,
        "attachments": [
            {
                "id": "0",
                "filename": VOICE_FILENAME,
                "uploaded_filename": upload_filename,
                "duration_secs": clip.duration_secs,
                "waveform": clip.waveform,
            }
        ],
    }


__all__ = [
    "DISCORD_MESSAGE_LIMIT",
    "VOICE_FILENAME",
    "VOICE_MESSAGE_FLAG",
    "VoiceClip",
    "apply_gain",
    "build_voice_clip",
    "chunk_message",
    "compute_waveform",
    "decode_to_pcm",
    "encode_ogg_opus",
    "probe_duration",
    "text_for_speech",
    "upload_slot_payload",
    "voice_message_payload",
]
