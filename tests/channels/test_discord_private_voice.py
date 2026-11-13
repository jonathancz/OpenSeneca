"""Private DM-only Discord assistant: allowlist, chunking, voice in and out.

Everything is offline: Discord REST calls go to mocked ``httpx`` functions and
gateway messages are fake objects shaped like ``discord.Message``. Incoming
voice uses a recorded fixture (a public domain clip encoded as a Discord voice
note) plus the transcript faster-whisper ``small`` produced for it.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.channels._stubs import ChannelMessage
from openjarvis.channels.discord_channel import DiscordChannel, _parse_user_ids
from openjarvis.channels.discord_voice import (
    VOICE_MESSAGE_FLAG,
    VoiceClip,
    chunk_message,
    compute_waveform,
    text_for_speech,
    voice_message_payload,
)
from openjarvis.core.registry import ChannelRegistry
from openjarvis.speech._stubs import TranscriptionResult
from openjarvis.speech.tts import TTSResult

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "discord"
VOICE_NOTE = FIXTURES / "voice-note.ogg"
RECORDED = json.loads((FIXTURES / "voice-note.transcript.json").read_text())

OWNER = "111111111111111111"
STRANGER = "222222222222222222"
DM_CHANNEL = "333333333333333333"
GUILD_CHANNEL = "444444444444444444"


@pytest.fixture(autouse=True)
def _register_discord():
    if not ChannelRegistry.contains("discord"):
        ChannelRegistry.register_value("discord", DiscordChannel)


@pytest.fixture(autouse=True)
def _no_env_allowlist(monkeypatch):
    monkeypatch.delenv("DISCORD_ALLOWED_USER_IDS", raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resp(status: int = 200, body: Any = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "error" if status >= 300 else ""
    resp.json.return_value = body if body is not None else {}
    return resp


class FakeDiscordHTTP:
    """Records Discord REST calls and answers like the real API."""

    def __init__(
        self,
        *,
        slot_status: int = 200,
        put_status: int = 200,
        message_status: int = 200,
    ) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.slot_status = slot_status
        self.put_status = put_status
        self.message_status = message_status

    def post(self, url, *, json=None, headers=None, timeout=None, **_):
        self.calls.append(
            {"method": "POST", "url": url, "json": json, "headers": headers}
        )
        if url.endswith("/users/@me/channels"):
            return _resp(200, {"id": DM_CHANNEL, "type": 1})
        if url.endswith("/attachments"):
            return _resp(
                self.slot_status,
                {
                    "attachments": [
                        {
                            "id": 0,
                            "upload_url": "https://uploads.example/voice?sig=x",
                            "upload_filename": "uploads/abc/voice-message.ogg",
                        }
                    ]
                },
            )
        return _resp(self.message_status, {"id": "999"})

    def put(self, url, *, content=None, headers=None, timeout=None, **_):
        self.calls.append(
            {"method": "PUT", "url": url, "content": content, "headers": headers}
        )
        return _resp(self.put_status)

    def patched(self):
        return (
            patch("httpx.post", side_effect=self.post),
            patch("httpx.put", side_effect=self.put),
        )

    def message_posts(self) -> List[Dict[str, Any]]:
        return [
            c
            for c in self.calls
            if c["method"] == "POST" and c["url"].endswith("/messages")
        ]


def _clip(seconds: float = 2.5) -> VoiceClip:
    import numpy as np

    pcm = (0.4 * np.sin(np.linspace(0, 400, int(48000 * seconds)))).astype(np.float32)
    return VoiceClip(
        ogg=b"OggS-fake-opus", duration_secs=seconds, waveform=compute_waveform(pcm)
    )


def _wav_bytes(seconds: float = 1.0, rate: int = 24000) -> bytes:
    import numpy as np

    sf = pytest.importorskip("soundfile")
    t = np.arange(int(rate * seconds)) / rate
    buf = io.BytesIO()
    sf.write(
        buf, (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), rate, format="WAV"
    )
    return buf.getvalue()


def _message(
    *,
    author_id: str = OWNER,
    bot: bool = False,
    guild: Any = None,
    content: str = "hello",
    attachments: List[Any] | None = None,
    voice: bool = False,
    message_id: str = "555",
    channel_id: str = DM_CHANNEL,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=int(message_id),
        author=SimpleNamespace(id=int(author_id), bot=bot),
        guild=guild,
        channel=SimpleNamespace(id=int(channel_id)),
        content=content,
        attachments=attachments or [],
        flags=SimpleNamespace(voice=voice),
    )


class FakeAttachment:
    def __init__(self, data: bytes, *, size: int | None = None, duration=None):
        self._data = data
        self.size = len(data) if size is None else size
        self.duration = duration
        self.content_type = "audio/ogg"
        self.reads = 0

    async def read(self) -> bytes:
        self.reads += 1
        return self._data


class FakeTranscriber:
    """Plays back the recorded faster-whisper transcript for the fixture."""

    backend_id = "faster-whisper"

    def __init__(self, text: str = RECORDED["text"]) -> None:
        self.text = text
        self.calls: List[Dict[str, Any]] = []

    def transcribe(self, audio, *, format="wav", language=None):
        self.calls.append({"audio": audio, "format": format, "language": language})
        return TranscriptionResult(text=self.text, duration_seconds=11.0)


def _run(ch: DiscordChannel, message: Any) -> None:
    asyncio.run(ch._handle_gateway_message(message))


def _collecting(ch: DiscordChannel) -> List[ChannelMessage]:
    seen: List[ChannelMessage] = []
    ch.on_message(seen.append)
    return seen


# ---------------------------------------------------------------------------
# Allowlist and DM only filter
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_parse_string_and_list(self):
        assert _parse_user_ids(f"{OWNER}, {STRANGER}") == [OWNER, STRANGER]
        assert _parse_user_ids([int(OWNER), OWNER]) == [OWNER]
        assert _parse_user_ids("") == []

    def test_invalid_ids_ignored(self):
        assert _parse_user_ids(f"@captain,{OWNER},abc") == [OWNER]

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", OWNER)
        ch = DiscordChannel(bot_token="t")
        assert ch.owner_id == OWNER

    def test_config_overrides_env(self, monkeypatch):
        monkeypatch.setenv("DISCORD_ALLOWED_USER_IDS", STRANGER)
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        assert ch.is_allowed(OWNER) and not ch.is_allowed(STRANGER)

    def test_allowed_dm_reaches_handler(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        seen = _collecting(ch)
        _run(ch, _message(content="what's on today?"))
        assert len(seen) == 1
        cm = seen[0]
        assert cm.channel == "discord"
        assert cm.sender == OWNER
        assert cm.content == "what's on today?"
        assert cm.conversation_id == DM_CHANNEL
        assert cm.message_id == "555"
        assert cm.metadata == {"voice": False}

    def test_stranger_dropped_and_logged_without_content(self, caplog):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        seen = _collecting(ch)
        with caplog.at_level(
            logging.DEBUG, logger="openjarvis.channels.discord_channel"
        ):
            _run(ch, _message(author_id=STRANGER, content="secret plans"))
        assert seen == []
        assert "non-allowlisted" in caplog.text
        assert "secret plans" not in caplog.text

    def test_bot_author_dropped(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        seen = _collecting(ch)
        _run(ch, _message(bot=True))
        assert seen == []

    def test_own_messages_dropped(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        seen = _collecting(ch)
        msg = _message()
        asyncio.run(ch._handle_gateway_message(msg, msg.author))
        assert seen == []

    def test_guild_message_dropped_when_dm_only(self, caplog):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        seen = _collecting(ch)
        with caplog.at_level(
            logging.DEBUG, logger="openjarvis.channels.discord_channel"
        ):
            _run(
                ch,
                _message(
                    guild=SimpleNamespace(id=1),
                    channel_id=GUILD_CHANNEL,
                    content="in public",
                ),
            )
        assert seen == []
        assert "dm_only" in caplog.text
        assert "in public" not in caplog.text

    def test_guild_message_allowed_when_not_dm_only(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER, dm_only=False)
        seen = _collecting(ch)
        _run(ch, _message(guild=SimpleNamespace(id=1), channel_id=GUILD_CHANNEL))
        assert len(seen) == 1

    def test_empty_allowlist_fails_closed(self):
        ch = DiscordChannel(bot_token="t")
        seen = _collecting(ch)
        _run(ch, _message())
        assert seen == []

    def test_empty_text_message_ignored(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        seen = _collecting(ch)
        _run(ch, _message(content="   "))
        assert seen == []


class TestIntents:
    def _intents_for(self, **kwargs) -> SimpleNamespace:
        captured: Dict[str, Any] = {}

        class FakeClient:
            def __init__(self, *, intents):
                captured["intents"] = intents
                self.user = object()
                self._closed = False

            def event(self, handler):
                return handler

            async def login(self, token):
                raise RuntimeError("stop here")

            async def close(self):
                self._closed = True

            def is_closed(self):
                return self._closed

        intents = SimpleNamespace()
        discord = SimpleNamespace(
            Intents=SimpleNamespace(none=lambda: intents), Client=FakeClient
        )
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER, **kwargs)
        with patch.dict(sys.modules, {"discord": discord}):
            ch._gateway_loop()
        return captured["intents"]

    def test_dm_only_uses_no_privileged_intent(self):
        intents = self._intents_for()
        assert intents.dm_messages is True
        assert intents.guilds is True
        assert not hasattr(intents, "message_content")
        assert not hasattr(intents, "guild_messages")

    def test_guild_mode_adds_message_content(self):
        intents = self._intents_for(dm_only=False)
        assert intents.guild_messages is True
        assert intents.message_content is True


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


class TestChunking:
    def test_short_message_single_chunk(self):
        assert chunk_message("hi") == ["hi"]
        assert chunk_message("   ") == []

    def test_4500_chars_three_posts(self):
        paragraphs = [f"Paragraph {i}. " + "word " * 176 for i in range(5)]
        reply = "\n\n".join(paragraphs)
        assert 4400 <= len(reply) <= 4600
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            assert ch.send(DM_CHANNEL, reply, conversation_id="555") is True
        posts = http.message_posts()
        assert len(posts) == 3
        assert all(len(p["json"]["content"]) <= 2000 for p in posts)
        # Only the first chunk replies to the inbound message.
        assert posts[0]["json"]["message_reference"] == {"message_id": "555"}
        assert all("message_reference" not in p["json"] for p in posts[1:])
        # Cuts land on paragraph boundaries: every chunk starts a paragraph.
        assert all(p["json"]["content"].startswith("Paragraph") for p in posts)

    def test_unbroken_text_hard_cut(self):
        chunks = chunk_message("x" * 4500)
        assert [len(c) for c in chunks] and all(len(c) <= 2000 for c in chunks)
        assert "".join(chunks) == "x" * 4500

    def test_code_fence_reopened_across_chunks(self):
        code = "\n".join(f"value_{i} = {i}" for i in range(400))
        chunks = chunk_message(f"Here:\n\n```python\n{code}\n```\nDone.")
        assert len(chunks) >= 2
        assert all(len(c) <= 2000 for c in chunks)
        assert all(c.count("```") % 2 == 0 for c in chunks)
        assert chunks[1].startswith("```python\n")

    def test_failed_chunk_stops_and_reports_failure(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        http = FakeDiscordHTTP(message_status=500)
        post, put = http.patched()
        with post, put:
            assert ch.send(DM_CHANNEL, "a\n\n" * 1500) is False
        assert len(http.message_posts()) == 1


# ---------------------------------------------------------------------------
# Proactive send to the owner
# ---------------------------------------------------------------------------


class TestProactiveSend:
    def test_captain_alias_resolves_and_caches_dm(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            assert ch.send("captain", "Morning brief") is True
            assert ch.send("discord", "Second") is True
            assert ch.send(OWNER, "Third") is True
        lookups = [c for c in http.calls if c["url"].endswith("/users/@me/channels")]
        assert len(lookups) == 1
        assert lookups[0]["json"] == {"recipient_id": OWNER}
        assert all(DM_CHANNEL in p["url"] for p in http.message_posts())
        assert ch.list_channels() == ["discord", "captain", DM_CHANNEL]

    def test_list_channels_without_allowlist(self):
        assert DiscordChannel(bot_token="t").list_channels() == ["discord"]

    def test_captain_without_allowlist_fails(self):
        ch = DiscordChannel(bot_token="t")
        with patch("httpx.post") as post:
            assert ch.send("captain", "hi") is False
        post.assert_not_called()

    def test_dm_lookup_failure_returns_false(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        with patch("httpx.post", return_value=_resp(403)) as post:
            assert ch.send("captain", "hi") is False
        assert post.call_count == 1

    def test_warm_cache_on_ready(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            ch._warm_dm_cache()
        assert DM_CHANNEL in ch.list_channels()

    def test_bridge_routes_captain(self):
        from openjarvis.server.channel_bridge import ChannelBridge

        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        bridge = ChannelBridge(
            channels={"discord": ch}, session_store=MagicMock(), bus=MagicMock()
        )
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            assert bridge.send("captain", "Scheduled report") is True
        assert http.message_posts()[0]["json"]["content"] == "Scheduled report"


# ---------------------------------------------------------------------------
# Voice out
# ---------------------------------------------------------------------------


class TestVoiceOut:
    def _voice_channel(self, **kwargs) -> DiscordChannel:
        ch = DiscordChannel(bot_token="tok", allowed_user_ids=OWNER, **kwargs)
        ch.synthesize_voice = MagicMock(return_value=_clip(2.5))
        return ch

    def test_payload_shape(self):
        clip = _clip(3.0)
        payload = voice_message_payload("uploads/x/voice-message.ogg", clip)
        assert payload["flags"] == VOICE_MESSAGE_FLAG == 8192
        assert "content" not in payload
        (att,) = payload["attachments"]
        assert att["filename"] == "voice-message.ogg"
        assert att["uploaded_filename"] == "uploads/x/voice-message.ogg"
        assert abs(att["duration_secs"] - 3.0) < 0.1
        assert len(base64.b64decode(att["waveform"])) == 256

    def test_three_requests_in_order(self):
        ch = self._voice_channel()
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            assert ch.send(DM_CHANNEL, "Hello captain", metadata={"voice": True})
        methods = [(c["method"], c["url"].rsplit("/", 1)[-1]) for c in http.calls]
        assert methods == [
            ("POST", "attachments"),
            ("PUT", "voice?sig=x"),
            ("POST", "messages"),
        ]
        slot = http.calls[0]["json"]["files"][0]
        assert slot == {"id": "0", "filename": "voice-message.ogg", "file_size": 14}
        upload = http.calls[1]
        assert upload["content"] == b"OggS-fake-opus"
        assert upload["headers"] == {"Content-Type": "audio/ogg"}  # no bot token
        body = http.calls[2]["json"]
        assert body["flags"] == 8192
        assert "content" not in body and "embeds" not in body
        att = body["attachments"][0]
        assert att["uploaded_filename"] == "uploads/abc/voice-message.ogg"
        assert abs(att["duration_secs"] - 2.5) < 0.1
        assert len(base64.b64decode(att["waveform"])) == 256

    def test_slot_403_falls_back_to_one_text_post(self):
        ch = self._voice_channel()
        http = FakeDiscordHTTP(slot_status=403)
        post, put = http.patched()
        with post, put:
            assert ch.send(DM_CHANNEL, "Hello captain", metadata={"voice": True})
        assert [c["method"] for c in http.calls] == ["POST", "POST"]
        (text,) = http.message_posts()
        assert text["json"]["content"] == "Hello captain"

    def test_upload_failure_falls_back(self):
        ch = self._voice_channel()
        http = FakeDiscordHTTP(put_status=500)
        post, put = http.patched()
        with post, put:
            assert ch.send(DM_CHANNEL, "Hi", metadata={"voice": True})
        assert http.message_posts()[-1]["json"] == {"content": "Hi"}

    def test_tts_failure_falls_back(self):
        ch = DiscordChannel(bot_token="t", allowed_user_ids=OWNER)
        broken = MagicMock(backend_id="kokoro")
        broken.synthesize.side_effect = RuntimeError("espeak missing")
        ch._tts = broken
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            assert ch.send(DM_CHANNEL, "Hi", metadata={"voice": True})
        assert [c["url"].rsplit("/", 1)[-1] for c in http.calls] == ["messages"]

    def test_mirror_voice_in_voice_out_text_in_text_out(self):
        ch = self._voice_channel()
        ch._remember_voice("700")
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            ch.send(DM_CHANNEL, "voice reply", conversation_id="700")
            ch.send(DM_CHANNEL, "text reply", conversation_id="701")
            # A message id is mirrored once only.
            ch.send(DM_CHANNEL, "follow up", conversation_id="700")
        assert ch.synthesize_voice.call_count == 1
        posts = http.message_posts()
        assert posts[0]["json"]["flags"] == 8192
        assert posts[1]["json"]["content"] == "text reply"
        assert posts[2]["json"]["content"] == "follow up"

    @pytest.mark.parametrize(
        ("mode", "inbound_voice", "expect_voice"),
        [("always", False, True), ("never", True, False), ("mirror", False, False)],
    )
    def test_reply_modes(self, mode, inbound_voice, expect_voice):
        ch = self._voice_channel(voice_replies=mode)
        if inbound_voice:
            ch._remember_voice("800")
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            ch.send(DM_CHANNEL, "reply", conversation_id="800")
        assert ch.synthesize_voice.called is expect_voice

    def test_invalid_mode_defaults_to_mirror(self):
        assert DiscordChannel(voice_replies="loud")._voice_replies == "mirror"

    def test_synthesize_uses_cpu_kokoro_voice_and_encodes(self):
        pytest.importorskip("av")
        tts = MagicMock(backend_id="kokoro")
        tts.synthesize.return_value = TTSResult(audio=_wav_bytes(1.0), format="wav")
        ch = DiscordChannel(bot_token="t", voice_id="bm_george", tts=tts)
        clip = ch.synthesize_voice("**Hello** from `Seneca`")
        tts.synthesize.assert_called_once_with(
            "Hello from Seneca", voice_id="bm_george", speed=1.2, output_format="wav"
        )
        assert clip.ogg.startswith(b"OggS")
        assert abs(clip.duration_secs - 1.0) < 0.1
        assert len(base64.b64decode(clip.waveform)) == 256

    def test_voice_speed_is_configurable(self):
        tts = MagicMock(backend_id="kokoro")
        tts.synthesize.side_effect = RuntimeError("stop after the call")
        ch = DiscordChannel(bot_token="t", tts=tts, voice_speed=1.15)
        with pytest.raises(RuntimeError):
            ch.synthesize_voice("Hi")
        assert tts.synthesize.call_args.kwargs["speed"] == 1.15

    def test_faster_speech_allows_proportionally_longer_text(self):
        tts = MagicMock(backend_id="kokoro")
        tts.synthesize.side_effect = RuntimeError("reached TTS")
        # 10 s at 14 chars/s is 140 chars at speed 1.0; 168 at speed 1.2.
        ch = DiscordChannel(bot_token="t", tts=tts, max_voice_seconds=10)
        with pytest.raises(RuntimeError, match="reached TTS"):
            ch.synthesize_voice("x" * 160)

    def test_default_kokoro_backend_is_cpu(self):
        ch = DiscordChannel(bot_token="t")
        with patch("openjarvis.speech.kokoro_tts.KokoroTTSBackend") as backend_cls:
            ch._get_tts()
        backend_cls.assert_called_once_with(device="cpu")

    def test_too_long_reply_is_not_spoken(self):
        tts = MagicMock(backend_id="kokoro")
        ch = DiscordChannel(bot_token="t", tts=tts, max_voice_seconds=10)
        with pytest.raises(ValueError):
            ch.synthesize_voice("word " * 200)
        tts.synthesize.assert_not_called()

    def test_text_for_speech_strips_markdown(self):
        spoken = text_for_speech(
            "# Plan\n- **Buy** milk\nSee [docs](https://x.y)\n```py\nx = 1\n```"
        )
        assert "#" not in spoken and "*" not in spoken and "```" not in spoken
        assert "Buy milk" in spoken and "docs" in spoken
        assert "https" not in spoken


class TestAudioEncoding:
    def test_sine_probes_as_opus_48k_mono(self):
        av = pytest.importorskip("av")
        from openjarvis.channels.discord_voice import build_voice_clip

        clip = build_voice_clip(_wav_bytes(3.0))
        with av.open(io.BytesIO(clip.ogg)) as container:
            stream = container.streams.audio[0]
            assert container.format.name == "ogg"
            assert stream.codec_context.name == "opus"
            assert stream.rate == 48000
            assert stream.channels == 1
        assert abs(clip.duration_secs - 3.0) < 0.1

    def test_silence_waveform_is_zero(self):
        assert base64.b64decode(compute_waveform([0.0] * 1000)) == bytes(256)
        assert base64.b64decode(compute_waveform([])) == bytes(256)


# ---------------------------------------------------------------------------
# Voice in
# ---------------------------------------------------------------------------


class TestVoiceIn:
    def _channel(self, **kwargs):
        transcriber = FakeTranscriber()
        ch = DiscordChannel(
            bot_token="t",
            allowed_user_ids=OWNER,
            transcriber=transcriber,
            **kwargs,
        )
        return ch, transcriber

    def test_voice_note_transcribed_and_dispatched(self):
        ch, transcriber = self._channel()
        seen = _collecting(ch)
        audio = VOICE_NOTE.read_bytes()
        attachment = FakeAttachment(audio, duration=RECORDED["duration_seconds"])
        _run(ch, _message(content="", attachments=[attachment], voice=True))

        assert attachment.reads == 1
        assert len(transcriber.calls) == 1
        assert transcriber.calls[0]["audio"] == audio
        assert transcriber.calls[0]["format"] == "ogg"
        assert len(seen) == 1
        assert seen[0].content == RECORDED["text"]
        assert seen[0].metadata == {"voice": True}
        assert "555" in ch._voice_inbound

    def test_audio_attachment_without_flag_is_voice(self):
        ch, transcriber = self._channel()
        seen = _collecting(ch)
        attachment = FakeAttachment(VOICE_NOTE.read_bytes(), duration=11.0)
        _run(ch, _message(content="", attachments=[attachment], voice=False))
        assert len(transcriber.calls) == 1
        assert seen[0].metadata["voice"] is True

    def test_non_audio_attachment_is_text(self):
        ch, transcriber = self._channel()
        seen = _collecting(ch)
        image = SimpleNamespace(content_type="image/png", size=10)
        _run(ch, _message(content="look", attachments=[image]))
        assert transcriber.calls == []
        assert seen[0].content == "look"

    def test_stranger_voice_note_never_downloaded(self):
        ch, transcriber = self._channel()
        attachment = FakeAttachment(VOICE_NOTE.read_bytes(), duration=11.0)
        _run(ch, _message(author_id=STRANGER, attachments=[attachment], voice=True))
        assert attachment.reads == 0
        assert transcriber.calls == []

    def _refusal(self, ch, attachment) -> str:
        seen = _collecting(ch)
        http = FakeDiscordHTTP()
        post, put = http.patched()
        with post, put:
            _run(ch, _message(content="", attachments=[attachment], voice=True))
        assert seen == []
        (reply,) = http.message_posts()
        assert reply["json"]["message_reference"] == {"message_id": "555"}
        return reply["json"]["content"]

    def test_too_long_voice_note_refused_without_download(self):
        ch, transcriber = self._channel(max_voice_seconds=120)
        attachment = FakeAttachment(b"x", duration=300.0)
        text = self._refusal(ch, attachment)
        assert "120 seconds" in text
        assert attachment.reads == 0 and transcriber.calls == []

    def test_oversize_voice_note_refused_without_download(self):
        ch, transcriber = self._channel()
        attachment = FakeAttachment(b"x", size=26 * 1024 * 1024, duration=10.0)
        text = self._refusal(ch, attachment)
        assert "too large" in text
        assert attachment.reads == 0 and transcriber.calls == []

    def test_unknown_duration_probed_after_download(self):
        pytest.importorskip("av")
        ch, transcriber = self._channel(max_voice_seconds=5)
        attachment = FakeAttachment(VOICE_NOTE.read_bytes(), duration=None)
        text = self._refusal(ch, attachment)
        assert "11 seconds" in text
        assert attachment.reads == 1 and transcriber.calls == []

    def test_transcription_error_refused(self):
        ch, transcriber = self._channel()
        transcriber.transcribe = MagicMock(side_effect=RuntimeError("model load"))
        attachment = FakeAttachment(VOICE_NOTE.read_bytes(), duration=11.0)
        assert "could not transcribe" in self._refusal(ch, attachment)

    def test_silent_voice_note_refused(self):
        ch, transcriber = self._channel()
        transcriber.text = "  "
        attachment = FakeAttachment(VOICE_NOTE.read_bytes(), duration=11.0)
        assert "could not make out" in self._refusal(ch, attachment)

    def test_default_transcriber_is_cpu_small_int8(self):
        ch = DiscordChannel(bot_token="t", stt_language="en")
        with patch(
            "openjarvis.speech.faster_whisper.FasterWhisperBackend"
        ) as backend_cls:
            backend_cls.return_value.transcribe.return_value = TranscriptionResult(
                text=" hi "
            )
            assert ch.transcribe_voice(b"ogg") == "hi"
        backend_cls.assert_called_once_with(
            model_size="small",
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
            beam_size=1,
        )
        backend_cls.return_value.transcribe.assert_called_once_with(
            b"ogg", format="ogg", language="en"
        )


class TestVoiceWarmup:
    def test_warmup_loads_both_models(self):
        transcriber = FakeTranscriber()
        transcriber._ensure_model = MagicMock()
        tts = MagicMock(backend_id="kokoro")
        tts.synthesize.return_value = TTSResult(audio=b"", format="wav")
        ch = DiscordChannel(bot_token="t", transcriber=transcriber, tts=tts)
        ch.warm_voice_models()
        transcriber._ensure_model.assert_called_once_with()
        assert tts.synthesize.call_args.args == ("Ready.",)

    def test_warmup_skips_tts_when_voice_replies_never(self):
        tts = MagicMock(backend_id="kokoro")
        ch = DiscordChannel(
            bot_token="t", transcriber=FakeTranscriber(), tts=tts, voice_replies="never"
        )
        ch.warm_voice_models()
        tts.synthesize.assert_not_called()

    def test_warmup_starts_once_and_can_be_disabled(self):
        ch = DiscordChannel(bot_token="t")
        with patch("threading.Thread") as thread_cls:
            ch._start_voice_warmup()
            ch._start_voice_warmup()
        assert thread_cls.call_count == 1
        thread_cls.return_value.start.assert_called_once_with()

        off = DiscordChannel(bot_token="t", preload_voice_models=False)
        with patch("threading.Thread") as thread_cls:
            off._start_voice_warmup()
        thread_cls.assert_not_called()


class TestVoiceRoundTripThroughSystem:
    """Voice note in → JarvisSystem.wire_channel → voice message out."""

    def test_voice_in_voice_out(self, tmp_path):
        from openjarvis.core.config import JarvisConfig
        from openjarvis.core.events import EventBus
        from openjarvis.system import JarvisSystem

        config = JarvisConfig()
        config.sessions.db_path = str(tmp_path / "sessions.db")
        system = JarvisSystem(
            config=config,
            bus=EventBus(record_history=False),
            engine=MagicMock(),
            engine_key="mock",
            model="test-model",
            agent_name="",
        )
        system.ask = MagicMock(return_value={"content": "Ask what you can do."})

        ch = DiscordChannel(
            bot_token="t", allowed_user_ids=OWNER, transcriber=FakeTranscriber()
        )
        ch.synthesize_voice = MagicMock(return_value=_clip(1.5))
        system.wire_channel(ch)

        http = FakeDiscordHTTP()
        post, put = http.patched()
        attachment = FakeAttachment(VOICE_NOTE.read_bytes(), duration=11.0)
        with post, put:
            _run(ch, _message(content="", attachments=[attachment], voice=True))
            _run(ch, _message(content="and in text?", message_id="556"))

        asked = [c.args[0] for c in system.ask.call_args_list]
        assert asked == [RECORDED["text"], "and in text?"]
        ch.synthesize_voice.assert_called_once_with("Ask what you can do.")
        posts = http.message_posts()
        assert posts[0]["json"]["flags"] == 8192
        assert posts[1]["json"]["content"] == "Ask what you can do."
        assert posts[1]["json"]["message_reference"] == {"message_id": "556"}


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------


class TestConfigWiring:
    def test_toml_to_channel_kwargs(self, tmp_path):
        from openjarvis.core.config import load_config
        from openjarvis.system._channel_kwargs import build_channel_kwargs

        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            "[channel.discord]\n"
            f'allowed_user_ids = "{OWNER}"\n'
            "dm_only = true\n"
            'voice_replies = "always"\n'
            "max_voice_seconds = 90\n"
            "voice_speed = 1.25\n"
            "preload_voice_models = false\n"
        )
        cfg = load_config(cfg_file)
        kwargs = build_channel_kwargs(cfg.channel, "discord")
        assert kwargs["allowed_user_ids"] == OWNER
        assert kwargs["dm_only"] is True
        assert kwargs["voice_replies"] == "always"
        assert kwargs["max_voice_seconds"] == 90
        assert kwargs["voice_speed"] == 1.25
        assert kwargs["preload_voice_models"] is False
        assert "bot_token" not in kwargs  # token stays in the environment
        ch = DiscordChannel(**kwargs)
        assert ch.owner_id == OWNER and ch._max_voice_seconds == 90

    def test_defaults(self):
        from openjarvis.core.config import DiscordChannelConfig

        cfg = DiscordChannelConfig()
        assert cfg.dm_only is True
        assert cfg.voice_replies == "mirror"
        assert cfg.max_voice_seconds == 120
        assert cfg.stt_model == "small"
        assert cfg.voice_speed == 1.2
        assert cfg.preload_voice_models is True


# ---------------------------------------------------------------------------
# Real transcription of the fixture (slow, only when the model is cached)
# ---------------------------------------------------------------------------


def _small_model_cached() -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    hit = try_to_load_from_cache("Systran/faster-whisper-small", "model.bin")
    return isinstance(hit, str)


@pytest.mark.slow
@pytest.mark.skipif(
    not _small_model_cached(), reason="faster-whisper small not cached locally"
)
def test_fixture_transcribes_on_cpu():
    pytest.importorskip("faster_whisper")
    ch = DiscordChannel(bot_token="t", stt_language="en")
    text = ch.transcribe_voice(VOICE_NOTE.read_bytes())
    assert "fellow Americans" in text
    assert "your country" in text
