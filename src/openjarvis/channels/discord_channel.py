"""DiscordChannel — native Discord Bot API adapter.

Built for a private assistant: by default it only answers direct messages
from an allowlist of Discord user ids, and it can send and receive real
Discord voice messages (local Kokoro TTS out, local faster-whisper STT in).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from openjarvis.channels._stubs import (
    BaseChannel,
    ChannelHandler,
    ChannelMessage,
    ChannelStatus,
)
from openjarvis.channels.discord_voice import (
    VoiceClip,
    build_voice_clip,
    chunk_message,
    probe_duration,
    text_for_speech,
    upload_slot_payload,
    voice_message_payload,
)
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import ChannelRegistry

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"

VOICE_REPLY_MODES = ("mirror", "always", "never")
# Discord's attachment cap for bots without boosts.
MAX_VOICE_BYTES = 25 * 1024 * 1024
# Rough Kokoro speaking rate, used to skip TTS for replies that would run
# past ``max_voice_seconds`` anyway.
_SPOKEN_CHARS_PER_SECOND = 14
# Aliases that address the first allowlisted user (the owner) by DM.
_OWNER_ALIASES = ("discord", "captain")
# Kokoro at 1.0 sounded slow to the owner (about 160 words per minute).
DEFAULT_VOICE_SPEED = 1.2
_MAX_TRACKED_VOICE_MESSAGES = 256


def _parse_user_ids(value: Union[str, Iterable[Any], None]) -> List[str]:
    """Parse a comma/space separated string (or iterable) of Discord ids."""
    if value is None:
        return []
    if isinstance(value, (int, str)):
        items: Iterable[Any] = re.split(r"[\s,]+", str(value))
    else:
        items = value
    ids: List[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not text.isdigit():
            logger.warning("Ignoring invalid Discord user id in allowlist")
            continue
        if text not in ids:
            ids.append(text)
    return ids


@ChannelRegistry.register("discord")
class DiscordChannel(BaseChannel):
    """Native Discord channel adapter using the Discord REST API.

    Parameters
    ----------
    bot_token:
        Discord bot token.  Falls back to ``DISCORD_BOT_TOKEN`` env var.
    bus:
        Optional event bus for publishing channel events.
    allowed_user_ids:
        Discord user ids allowed to talk to the bot (comma separated string
        or list). Falls back to ``DISCORD_ALLOWED_USER_IDS``. The first id is
        the owner that ``"captain"`` and ``"discord"`` address. When empty,
        every inbound message is dropped (fail closed).
    dm_only:
        Ignore messages sent in servers; only direct messages are handled.
    voice_replies:
        ``"mirror"`` (voice in gets voice out, text in gets text out),
        ``"always"`` or ``"never"``.
    max_voice_seconds:
        Longest voice message accepted in, and produced out.
    tts_backend, voice_id, voice_speed:
        Text-to-speech settings for outgoing voice messages. Kokoro runs on
        CPU; ``voice_speed`` 1.2 is a brisk conversational pace.
    preload_voice_models:
        Load the speech-to-text and text-to-speech models in the background
        once the gateway is ready, so the first voice note is not slowed by
        model loading.
    stt_model, stt_language:
        faster-whisper model size and optional language for incoming voice
        messages (CPU, int8).
    tts, transcriber:
        Pre-built backends, mainly for tests.
    """

    channel_id = "discord"

    def __init__(
        self,
        bot_token: str = "",
        *,
        bus: Optional[EventBus] = None,
        allowed_user_ids: Union[str, Iterable[Any], None] = None,
        dm_only: bool = True,
        voice_replies: str = "mirror",
        max_voice_seconds: int = 120,
        tts_backend: str = "kokoro",
        voice_id: str = "",
        voice_speed: float = DEFAULT_VOICE_SPEED,
        stt_model: str = "small",
        stt_language: str = "",
        preload_voice_models: bool = True,
        tts: Optional[Any] = None,
        transcriber: Optional[Any] = None,
    ) -> None:
        self._token = bot_token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self._bus = bus
        if allowed_user_ids is None or allowed_user_ids == "":
            allowed_user_ids = os.environ.get("DISCORD_ALLOWED_USER_IDS", "")
        self._allowed_user_ids = _parse_user_ids(allowed_user_ids)
        self._dm_only = bool(dm_only)
        mode = (voice_replies or "mirror").strip().lower()
        if mode not in VOICE_REPLY_MODES:
            logger.warning(
                "Unknown Discord voice_replies %r; using 'mirror'", voice_replies
            )
            mode = "mirror"
        self._voice_replies = mode
        self._max_voice_seconds = max(1, int(max_voice_seconds))
        self._tts_backend = tts_backend or "kokoro"
        self._voice_id = voice_id
        self._voice_speed = float(voice_speed) if voice_speed else DEFAULT_VOICE_SPEED
        self._preload_voice_models = bool(preload_voice_models)
        self._voice_models_warmed = False
        self._stt_model = stt_model or "small"
        self._stt_language = stt_language or None
        self._tts = tts
        self._transcriber = transcriber
        # One synthesis and one transcription at a time: both are CPU heavy.
        self._tts_lock = threading.Lock()
        self._stt_lock = threading.Lock()
        # user id -> DM channel id, resolved once per user.
        self._dm_channels: Dict[str, str] = {}
        self._dm_lock = threading.Lock()
        # Inbound voice message ids, so the reply can mirror them.
        self._voice_inbound: "OrderedDict[str, None]" = OrderedDict()
        self._voice_lock = threading.Lock()
        self._handlers: List[ChannelHandler] = []
        self._status = ChannelStatus.DISCONNECTED
        self._listener_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Serialize listener registration and shutdown so a concurrent
        # connect() cannot clear a stop request after disconnect() returns.
        self._lifecycle_lock = threading.RLock()
        # Published by _gateway_loop while it's running so disconnect()
        # can wake the async lifecycle owner (#784).
        self._client: Optional[Any] = None
        self._loop: Optional[Any] = None
        self._async_stop_event: Optional[asyncio.Event] = None
        self._runtime_lock = threading.Lock()

    # -- connection lifecycle ---------------------------------------------------

    def connect(self) -> None:
        """Start listening for incoming messages via discord.py gateway."""
        with self._lifecycle_lock:
            if self._listener_thread is not None and self._listener_thread.is_alive():
                # A gateway connection is already running; starting another
                # one duplicates message handling (#784).
                logger.warning(
                    "Discord channel already connected; ignoring duplicate connect()"
                )
                return

            if not self._token:
                logger.warning("No Discord bot token configured")
                self._status = ChannelStatus.ERROR
                return

            if not self._allowed_user_ids:
                logger.warning(
                    "Discord allowed_user_ids is empty; every inbound message"
                    " will be ignored. Set [channel.discord] allowed_user_ids"
                    " or DISCORD_ALLOWED_USER_IDS."
                )

            self._stop_event.clear()
            self._status = ChannelStatus.CONNECTING

            try:
                import discord  # noqa: F401

                listener = threading.Thread(
                    target=self._gateway_loop,
                    daemon=True,
                )
                self._listener_thread = listener
                self._status = ChannelStatus.CONNECTED
                listener.start()
                logger.info("Discord channel connected (gateway)")
            except ImportError:
                logger.info("discord.py not installed; send-only mode")
                self._status = ChannelStatus.CONNECTED
            except Exception:
                self._listener_thread = None
                self._status = ChannelStatus.ERROR
                logger.exception("Failed to start Discord listener")

    def disconnect(self) -> None:
        """Stop the listener thread.

        The listener owns login, gateway connection, and client cleanup.
        ``disconnect`` publishes a durable stop request onto that loop; the
        listener races each async startup phase against it and performs the
        close itself. Status is only reported DISCONNECTED after the thread
        has actually terminated.
        """
        with self._lifecycle_lock:
            self._stop_event.set()

            with self._runtime_lock:
                loop = self._loop
                async_stop_event = self._async_stop_event
            if loop is not None and async_stop_event is not None:
                try:
                    loop.call_soon_threadsafe(async_stop_event.set)
                except RuntimeError:
                    logger.debug("Discord gateway event loop already closed")

            if self._listener_thread is not None:
                self._listener_thread.join(timeout=5.0)
                if self._listener_thread.is_alive():
                    logger.warning(
                        "Discord listener thread did not stop within timeout;"
                        " leaving status unchanged to avoid a duplicate"
                        " gateway connection"
                    )
                    return
                self._listener_thread = None

            self._status = ChannelStatus.DISCONNECTED

    # -- send / receive --------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        """Send a reply or a proactive message.

        ``channel`` is a Discord channel id, an allowlisted user id, or one
        of the aliases ``"captain"``/``"discord"`` (the owner's DM). Replies
        go out as voice messages per ``voice_replies``; ``metadata={"voice":
        True|False}`` forces the choice. Voice always falls back to text.
        """
        if not self._token:
            logger.warning("Cannot send: no Discord bot token")
            return False
        # Defensive guard for #459 follow-up: an empty `channel` arg would
        # produce /channels//messages and silently 404. Better to fail
        # explicitly so the upstream bug (wrong field passed in) surfaces
        # in the warning log instead of silently blackholing the reply.
        if not channel:
            logger.warning(
                "Cannot send: no Discord channel destination "
                "(caller passed empty channel id)"
            )
            return False

        destination = self._resolve_destination(channel)
        if not destination:
            return False

        if self._wants_voice(conversation_id, metadata) and self._send_voice(
            destination, content
        ):
            self._publish_sent(destination, content, conversation_id)
            return True
        return self._send_text(destination, content, conversation_id)

    # -- destinations ------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
        }

    @property
    def owner_id(self) -> str:
        """The first allowlisted user id, or ``""``."""
        return self._allowed_user_ids[0] if self._allowed_user_ids else ""

    def is_allowed(self, user_id: str) -> bool:
        """Whether *user_id* may talk to the bot."""
        return str(user_id) in self._allowed_user_ids

    def _resolve_destination(self, channel: str) -> str:
        """Map an alias or allowlisted user id to a channel id."""
        if channel in _OWNER_ALIASES:
            if not self.owner_id:
                logger.warning(
                    "Cannot send to %r: no Discord allowed_user_ids configured",
                    channel,
                )
                return ""
            return self.dm_channel_for(self.owner_id)
        if channel in self._allowed_user_ids:
            return self.dm_channel_for(channel)
        return channel

    def dm_channel_for(self, user_id: str) -> str:
        """Return (and cache) the DM channel id for *user_id*.

        Uses ``POST /users/@me/channels``. The bot must share a server with
        the user for Discord to allow the DM.
        """
        with self._dm_lock:
            cached = self._dm_channels.get(user_id)
            if cached:
                return cached
            try:
                import httpx

                resp = httpx.post(
                    f"{API_BASE}/users/@me/channels",
                    json={"recipient_id": user_id},
                    headers=self._headers(),
                    timeout=10.0,
                )
            except Exception:
                logger.warning("Discord DM channel lookup failed", exc_info=True)
                return ""
            if resp.status_code >= 300:
                logger.warning(
                    "Discord DM channel lookup returned status %d", resp.status_code
                )
                return ""
            try:
                dm_id = str(resp.json()["id"])
            except Exception:
                logger.warning("Discord DM channel lookup returned no channel id")
                return ""
            self._dm_channels[user_id] = dm_id
            return dm_id

    # -- text --------------------------------------------------------------------

    def _send_text(self, channel: str, content: str, conversation_id: str) -> bool:
        """Post *content* in chunks of at most 2,000 characters."""
        chunks = chunk_message(content)
        if not chunks:
            logger.warning("Cannot send: empty Discord message")
            return False
        try:
            import httpx

            url = f"{API_BASE}/channels/{channel}/messages"
            for index, chunk in enumerate(chunks):
                payload: Dict[str, Any] = {"content": chunk}
                # Only the first chunk replies to the inbound message.
                if conversation_id and index == 0:
                    payload["message_reference"] = {"message_id": conversation_id}
                resp = httpx.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                    timeout=10.0,
                )
                if resp.status_code >= 300:
                    logger.warning(
                        "Discord API returned status %d: %s",
                        resp.status_code,
                        resp.text,
                    )
                    return False
            self._publish_sent(channel, content, conversation_id)
            return True
        except Exception:
            logger.debug("Discord send failed", exc_info=True)
            return False

    # -- voice out ---------------------------------------------------------------

    def _remember_voice(self, message_id: str) -> None:
        with self._voice_lock:
            self._voice_inbound[message_id] = None
            while len(self._voice_inbound) > _MAX_TRACKED_VOICE_MESSAGES:
                self._voice_inbound.popitem(last=False)

    def _wants_voice(
        self, conversation_id: str, metadata: Optional[Dict[str, Any]]
    ) -> bool:
        with self._voice_lock:
            was_voice = bool(conversation_id) and conversation_id in self._voice_inbound
            if was_voice:
                del self._voice_inbound[conversation_id]
        if metadata and "voice" in metadata:
            return bool(metadata["voice"])
        if self._voice_replies == "always":
            return True
        if self._voice_replies == "never":
            return False
        return was_voice

    def _get_tts(self) -> Any:
        if self._tts is None:
            if self._tts_backend == "kokoro":
                # CPU only: the GPU is reserved for the language model.
                from openjarvis.speech.kokoro_tts import KokoroTTSBackend

                self._tts = KokoroTTSBackend(device="cpu")
            else:
                from openjarvis.speech._tts_discovery import get_tts_backend

                self._tts = get_tts_backend(self._tts_backend)
                if self._tts is None:
                    raise RuntimeError("no text-to-speech backend available")
        return self._tts

    def synthesize_voice(self, text: str) -> VoiceClip:
        """Speak *text* and encode it as a Discord voice message clip."""
        from openjarvis.speech._tts_discovery import default_voice_for

        spoken = text_for_speech(text)
        if not spoken:
            raise ValueError("nothing to speak")
        max_chars = self._max_voice_seconds * _SPOKEN_CHARS_PER_SECOND
        if len(spoken) > max_chars * max(self._voice_speed, 1.0):
            raise ValueError("reply too long for a voice message")
        started = time.monotonic()
        with self._tts_lock:
            tts = self._get_tts()
            backend_id = getattr(tts, "backend_id", "")
            voice = self._voice_id or default_voice_for(backend_id)
            kwargs: Dict[str, Any] = {"speed": self._voice_speed}
            if voice:
                kwargs["voice_id"] = voice
            if backend_id == "kokoro":
                kwargs["output_format"] = "wav"
            result = tts.synthesize(spoken, **kwargs)
            if not result.audio:
                raise ValueError("TTS returned no audio")
            clip = build_voice_clip(result.audio)
        logger.info(
            "Discord voice reply: %.1f s of audio (%d chars, speed %.2f)"
            " synthesized in %.1f s",
            clip.duration_secs,
            len(spoken),
            self._voice_speed,
            time.monotonic() - started,
        )
        if clip.duration_secs > self._max_voice_seconds:
            raise ValueError("voice reply longer than max_voice_seconds")
        return clip

    def _send_voice(self, channel: str, content: str) -> bool:
        """Upload *content* as a Discord voice message. False on any failure."""
        try:
            clip = self.synthesize_voice(content)
        except Exception as exc:
            logger.warning("Discord voice reply skipped, sending text: %s", exc)
            return False
        try:
            started = time.monotonic()
            sent = self.send_voice_clip(channel, clip)
            logger.info(
                "Discord voice reply uploaded in %.1f s", time.monotonic() - started
            )
            return sent
        except Exception:
            logger.warning("Discord voice upload failed, sending text", exc_info=True)
            return False

    def send_voice_clip(self, channel: str, clip: VoiceClip) -> bool:
        """Post *clip* to *channel* with the three step attachment upload."""
        import httpx

        slot = httpx.post(
            f"{API_BASE}/channels/{channel}/attachments",
            json=upload_slot_payload(clip),
            headers=self._headers(),
            timeout=10.0,
        )
        if slot.status_code >= 300:
            logger.warning(
                "Discord voice upload slot returned status %d", slot.status_code
            )
            return False
        attachment = slot.json()["attachments"][0]
        # The upload URL is a pre-signed storage URL: never send the bot
        # token to it.
        put = httpx.put(
            attachment["upload_url"],
            content=clip.ogg,
            headers={"Content-Type": "audio/ogg"},
            timeout=30.0,
        )
        if put.status_code >= 300:
            logger.warning("Discord voice upload returned status %d", put.status_code)
            return False
        msg = httpx.post(
            f"{API_BASE}/channels/{channel}/messages",
            json=voice_message_payload(attachment["upload_filename"], clip),
            headers=self._headers(),
            timeout=10.0,
        )
        if msg.status_code >= 300:
            logger.warning("Discord voice message returned status %d", msg.status_code)
            return False
        return True

    # -- voice in ----------------------------------------------------------------

    def _get_transcriber(self) -> Any:
        if self._transcriber is None:
            from openjarvis.speech.faster_whisper import FasterWhisperBackend

            # CPU only, int8: a 30 s note takes about 10 s on four cores.
            self._transcriber = FasterWhisperBackend(
                model_size=self._stt_model,
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                beam_size=1,
            )
        return self._transcriber

    def transcribe_voice(self, audio: bytes) -> str:
        """Transcribe a voice message (OGG Opus) to text."""
        started = time.monotonic()
        with self._stt_lock:
            result = self._get_transcriber().transcribe(
                audio, format="ogg", language=self._stt_language
            )
        logger.info(
            "Discord voice note: %.1f s of audio transcribed in %.1f s",
            getattr(result, "duration_seconds", 0.0) or 0.0,
            time.monotonic() - started,
        )
        return (result.text or "").strip()

    def warm_voice_models(self) -> None:
        """Load the STT and TTS models now instead of on the first voice note."""
        started = time.monotonic()
        try:
            transcriber = self._get_transcriber()
            ensure = getattr(transcriber, "_ensure_model", None)
            if callable(ensure):
                with self._stt_lock:
                    ensure()
        except Exception as exc:
            logger.warning("Discord speech-to-text preload failed: %s", exc)
        if self._voice_replies != "never":
            try:
                self.synthesize_voice("Ready.")
            except Exception as exc:
                logger.warning("Discord text-to-speech preload failed: %s", exc)
        logger.info(
            "Discord voice models preloaded in %.1f s", time.monotonic() - started
        )

    def _start_voice_warmup(self) -> None:
        """Preload voice models once, in the background."""
        if not self._preload_voice_models or self._voice_models_warmed:
            return
        self._voice_models_warmed = True
        threading.Thread(
            target=self.warm_voice_models, name="discord-voice-warmup", daemon=True
        ).start()

    @staticmethod
    def _voice_attachment(message: Any) -> Optional[Any]:
        """Return the audio attachment of a voice message, if any."""
        attachments = list(getattr(message, "attachments", None) or [])
        if not attachments:
            return None
        flags = getattr(message, "flags", None)
        if flags is not None and getattr(flags, "voice", False) is True:
            return attachments[0]
        for attachment in attachments:
            content_type = getattr(attachment, "content_type", None) or ""
            if content_type.startswith("audio/"):
                return attachment
        return None

    async def _transcribe_attachment(
        self, attachment: Any
    ) -> Tuple[Optional[str], Optional[str]]:
        """Return ``(transcript, None)`` or ``(None, polite_refusal)``."""
        limit = self._max_voice_seconds
        size = getattr(attachment, "size", 0) or 0
        if size > MAX_VOICE_BYTES:
            return None, "That voice message is too large for me (25 MB limit)."
        duration = getattr(attachment, "duration", None) or 0.0
        if duration > limit:
            return None, (
                f"That voice message is {duration:.0f} seconds long; I can take"
                f" up to {limit} seconds. Could you send a shorter one?"
            )
        try:
            audio = await attachment.read()
        except Exception:
            logger.warning("Discord voice message download failed", exc_info=True)
            return None, "Sorry, I could not download that voice message."
        if not duration:
            duration = await asyncio.to_thread(probe_duration, audio)
            if duration > limit:
                return None, (
                    f"That voice message is {duration:.0f} seconds long; I can"
                    f" take up to {limit} seconds. Could you send a shorter one?"
                )
        try:
            text = await asyncio.to_thread(self.transcribe_voice, audio)
        except Exception:
            logger.warning("Discord voice transcription failed", exc_info=True)
            return None, "Sorry, I could not transcribe that voice message."
        if not text:
            return None, "I could not make out any words in that voice message."
        return text, None

    async def _handle_gateway_message(self, message: Any, bot_user: Any = None) -> None:
        """Filter, transcribe and dispatch one gateway message."""
        author = message.author
        if getattr(author, "bot", False) or (
            bot_user is not None and author == bot_user
        ):
            return
        message_id = str(message.id)
        if self._dm_only and getattr(message, "guild", None) is not None:
            logger.debug("Discord: dropped server message %s (dm_only)", message_id)
            return
        sender = str(author.id)
        if not self.is_allowed(sender):
            logger.debug(
                "Discord: dropped message %s from non-allowlisted user %s",
                message_id,
                sender,
            )
            return

        channel_id = str(message.channel.id)
        content = message.content or ""
        attachment = self._voice_attachment(message)
        is_voice = attachment is not None
        if is_voice:
            transcript, refusal = await self._transcribe_attachment(attachment)
            if refusal is not None:
                await asyncio.to_thread(
                    self.send,
                    channel_id,
                    refusal,
                    conversation_id=message_id,
                    metadata={"voice": False},
                )
                return
            content = f"{content}\n\n{transcript}".strip() if content else transcript
        if not content or not content.strip():
            return

        cm = ChannelMessage(
            channel="discord",
            sender=sender,
            content=content,
            message_id=message_id,
            conversation_id=channel_id,
            metadata={"voice": is_voice},
        )
        if is_voice:
            self._remember_voice(message_id)
        # Handlers run the agent (and TTS for the reply): keep them off the
        # gateway event loop so heartbeats continue.
        await asyncio.to_thread(self._dispatch, cm)

    def _dispatch(self, cm: ChannelMessage) -> None:
        for handler in self._handlers:
            try:
                handler(cm)
            except Exception:
                logger.exception("Discord handler error")
        if self._bus is not None:
            self._bus.publish(
                EventType.CHANNEL_MESSAGE_RECEIVED,
                {
                    "channel": cm.channel,
                    "sender": cm.sender,
                    "content": cm.content,
                    "message_id": cm.message_id,
                },
            )

    def _warm_dm_cache(self) -> None:
        """Resolve the owner's DM channel so proactive sends can find it."""
        if self.owner_id:
            self.dm_channel_for(self.owner_id)

    # -- BaseChannel -------------------------------------------------------------

    def status(self) -> ChannelStatus:
        """Return the current connection status."""
        return self._status

    def list_channels(self) -> List[str]:
        """Return addressable destinations.

        ``"discord"`` always; ``"captain"`` (the owner's DM) and the resolved
        DM channel ids when an allowlist is configured.
        """
        names = ["discord"]
        if self.owner_id:
            names.append("captain")
        with self._dm_lock:
            names.extend(self._dm_channels.values())
        return names

    def on_message(self, handler: ChannelHandler) -> None:
        """Register a callback for incoming messages."""
        self._handlers.append(handler)

    # -- internal helpers -------------------------------------------------------

    def _stop_requested(self, async_stop_event: asyncio.Event) -> bool:
        return self._stop_event.is_set() or async_stop_event.is_set()

    async def _run_stage_until_stopped(
        self,
        awaitable: Any,
        async_stop_event: asyncio.Event,
    ) -> bool:
        """Run one Discord startup stage, cancelling it when stop wins."""
        if self._stop_requested(async_stop_event):
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            return False

        stage_task = asyncio.create_task(awaitable)
        stop_task = asyncio.create_task(async_stop_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {stage_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done or self._stop_event.is_set():
                stage_task.cancel()
                await asyncio.gather(stage_task, return_exceptions=True)
                return False
            await stage_task
            return True
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)

    async def _run_gateway_lifecycle(
        self,
        client: Any,
        async_stop_event: asyncio.Event,
    ) -> None:
        """Own Discord login/connect/close with stop checks at each boundary."""
        try:
            if not await self._run_stage_until_stopped(
                client.login(self._token),
                async_stop_event,
            ):
                return
            if self._stop_requested(async_stop_event):
                return
            await self._run_stage_until_stopped(
                client.connect(reconnect=True),
                async_stop_event,
            )
        finally:
            await client.close()

    def _gateway_loop(self) -> None:
        """Run the discord.py client in a background thread."""
        client: Any | None = None
        loop: asyncio.AbstractEventLoop | None = None
        try:
            import discord

            # DM only needs no privileged intent: Discord always delivers the
            # content of direct messages to the bot.
            intents = discord.Intents.none()
            intents.dm_messages = True
            intents.guilds = True
            if not self._dm_only:
                intents.guild_messages = True
                intents.message_content = True
            client = discord.Client(intents=intents)

            @client.event
            async def on_ready():
                self._start_voice_warmup()
                try:
                    await asyncio.to_thread(self._warm_dm_cache)
                except Exception:
                    logger.debug("Discord DM cache warmup failed", exc_info=True)

            @client.event
            async def on_message(message):
                try:
                    await self._handle_gateway_message(message, client.user)
                except Exception:
                    logger.exception("Discord message handling failed")

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            async_stop_event = asyncio.Event()
            with self._runtime_lock:
                self._client = client
                self._async_stop_event = async_stop_event
                self._loop = loop

            # A disconnect before these references were published remains in
            # the durable threading event. The owned lifecycle checks it before
            # login and races it against every asynchronous startup phase.
            loop.run_until_complete(
                self._run_gateway_lifecycle(client, async_stop_event)
            )
        except Exception:
            logger.debug("Discord gateway loop error", exc_info=True)
            if not self._stop_event.is_set():
                self._status = ChannelStatus.ERROR
        finally:
            if loop is not None and not loop.is_closed():
                try:
                    if client is not None and not client.is_closed():
                        loop.run_until_complete(client.close())
                except Exception:
                    logger.debug("Discord client cleanup failed", exc_info=True)
                finally:
                    asyncio.set_event_loop(None)
                    loop.close()
            with self._runtime_lock:
                self._client = None
                self._async_stop_event = None
                self._loop = None

    def _publish_sent(self, channel: str, content: str, conversation_id: str) -> None:
        """Publish a CHANNEL_MESSAGE_SENT event on the bus."""
        if self._bus is not None:
            self._bus.publish(
                EventType.CHANNEL_MESSAGE_SENT,
                {
                    "channel": channel,
                    "content": content,
                    "conversation_id": conversation_id,
                },
            )


__all__ = ["DiscordChannel"]
