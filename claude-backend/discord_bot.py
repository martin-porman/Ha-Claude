"""Discord Bot Integration.

Listens for Discord messages via discord.py gateway and relays them to Amira API.
"""

import asyncio
import logging
from threading import Thread
from typing import Any, Dict, Optional, Set

import requests

try:
    import discord  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    discord = None

logger = logging.getLogger(__name__)


def _parse_ids(raw: str) -> Set[int]:
    if not raw:
        return set()
    ids: Set[int] = set()
    for part in str(raw).replace(";", ",").split(","):
        token = part.strip()
        if token.isdigit():
            ids.add(int(token))
    return ids


class DiscordBot:
    """Discord bot handler using discord.py."""

    def __init__(
        self,
        token: str,
        api_base_url: str = "http://localhost:5010",
        allowed_channel_ids: Optional[Set[int]] = None,
        allowed_user_ids: Optional[Set[int]] = None,
    ):
        self.token = (token or "").strip()
        self.api_base = api_base_url.rstrip("/")
        self.allowed_channel_ids = set(allowed_channel_ids or set())
        self.allowed_user_ids = set(allowed_user_ids or set())
        self.thread: Optional[Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client = None
        self.running = False

    async def _on_ready(self):
        if not self.client:
            return
        logger.info("Discord bot connected as %s", self.client.user)

    async def _send_chunks(self, channel, text: str):
        text = (text or "").strip()
        if not text:
            return
        chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)]
        for chunk in chunks:
            await channel.send(chunk)

    async def _send_to_channel(self, channel_id: int, text: str) -> bool:
        """Send a message to a Discord channel id."""
        if not self.client:
            return False
        try:
            channel = self.client.get_channel(int(channel_id))
            if channel is None:
                channel = await self.client.fetch_channel(int(channel_id))
            if channel is None:
                logger.warning("Discord send: channel not found (%s)", channel_id)
                return False
            await self._send_chunks(channel, text)
            return True
        except Exception as e:
            logger.error("Discord send error (channel=%s): %s", channel_id, e)
            return False

    async def _on_message(self, message):
        if not self.client:
            return
        if message.author.bot:
            return
        text = (message.content or "").strip()
        if not text:
            return

        user_id = int(message.author.id)
        channel_id = int(message.channel.id)
        logger.info(
            "Discord event received: user=%s channel=%s guild=%s content_len=%s",
            user_id,
            channel_id,
            int(message.guild.id) if message.guild else "dm",
            len(text),
        )

        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            logger.info("Discord message blocked: user_id=%s not in allow-list", user_id)
            return
        if self.allowed_channel_ids and channel_id not in self.allowed_channel_ids:
            logger.info("Discord message blocked: channel_id=%s not in allow-list", channel_id)
            return

        logger.info("Discord: incoming message user=%s channel=%s text=%s", user_id, channel_id, text[:80])

        from messaging import get_messaging_manager

        mgr = get_messaging_manager()
        mgr.add_message("discord", str(user_id), text, role="user")
        history = mgr.get_chat_history("discord", str(user_id), limit=5)

        payload: Dict[str, Any] = {
            "user_id": user_id,
            "channel_id": channel_id,
            "guild_id": int(message.guild.id) if message.guild else None,
            "text": text,
            "history": history,
        }

        try:
            resp = requests.post(f"{self.api_base}/api/discord/message", json=payload, timeout=90)
            if resp.status_code != 200:
                logger.warning("Discord API error: %s %s", resp.status_code, resp.text[:200])
                await self._send_chunks(message.channel, "⚠️ API error, please try again.")
                return
            data = resp.json()
            response_text = (data.get("response") or "").strip() or "⚠️ Empty response."
            mgr.add_message("discord", str(user_id), response_text, role="assistant")
            await self._send_chunks(message.channel, response_text)
        except Exception as e:
            logger.error("Discord message processing error: %s", e)
            await self._send_chunks(message.channel, "❌ Error processing message.")

    async def _runner(self):
        if discord is None:
            logger.warning("discord.py not installed; Discord bot disabled")
            return
        # Suppress discord.py voice-related warnings (PyNaCl/davey not installed — voice unused)
        import logging as _logging
        _logging.getLogger("discord.client").setLevel(_logging.ERROR)
        _logging.getLogger("discord.voice_client").setLevel(_logging.ERROR)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.dm_messages = True
        self.client = discord.Client(intents=intents)
        # Register handlers with canonical names for broad discord.py compatibility.
        @self.client.event
        async def on_ready():
            await self._on_ready()

        @self.client.event
        async def on_message(message):
            await self._on_message(message)

        logger.info(
            "Discord listeners registered via @event (on_ready/on_message) "
            "message_content=%s messages=%s guilds=%s dm_messages=%s",
            intents.message_content,
            intents.messages,
            intents.guilds,
            intents.dm_messages,
        )
        await self.client.start(self.token)

    def start(self):
        if self.running:
            logger.warning("Discord bot already running")
            return
        if not self.token:
            logger.info("Discord bot not configured (no token)")
            return

        self.running = True

        def _thread_main():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._runner())
            except Exception as e:
                logger.error("Discord bot stopped with error: %s", e)
            finally:
                self.running = False
                if self.loop and not self.loop.is_closed():
                    self.loop.stop()
                    self.loop.close()

        self.thread = Thread(target=_thread_main, daemon=True)
        self.thread.start()
        logger.info("Discord bot started")

    def stop(self):
        self.running = False
        if self.loop and self.client:
            try:
                fut = asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)
                fut.result(timeout=5)
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("Discord bot stopped")

    def send_message(self, channel_id: int, text: str, timeout: float = 10.0) -> bool:
        """Thread-safe synchronous send wrapper used by backend tools."""
        if not self.running or not self.loop or not self.client:
            logger.warning("Discord send requested but bot is not running")
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._send_to_channel(int(channel_id), text),
                self.loop,
            )
            return bool(fut.result(timeout=timeout))
        except Exception as e:
            logger.error("Discord send_message failed: %s", e)
            return False


_bot: Optional[DiscordBot] = None


def get_discord_bot(
    token: str,
    api_base: str = "http://localhost:5010",
    allowed_channel_ids: str = "",
    allowed_user_ids: str = "",
) -> Optional[DiscordBot]:
    """Get or create Discord bot instance."""
    global _bot
    if not token:
        return None
    if _bot is None:
        _bot = DiscordBot(
            token=token,
            api_base_url=api_base,
            allowed_channel_ids=_parse_ids(allowed_channel_ids),
            allowed_user_ids=_parse_ids(allowed_user_ids),
        )
    return _bot
