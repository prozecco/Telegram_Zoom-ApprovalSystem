import logging
import asyncio
from typing import Optional

import config

logger = logging.getLogger("userbot_service")

class UserbotService:
    def __init__(self):
        self.api_id = config.TELEGRAM_API_ID
        self.api_hash = config.TELEGRAM_API_HASH
        self.session_str = config.TELEGRAM_SESSION_STRING
        
        self.client = None
        self.is_started = False
        self._lock = asyncio.Lock()
        
        if self.api_id and self.api_hash and self.session_str:
            try:
                # Lazy import inside constructor to avoid RuntimeError in non-asyncio test runners
                from pyrogram import Client
                self.client = Client(
                    "zoom_userbot_session",
                    api_id=self.api_id,
                    api_hash=self.api_hash,
                    session_string=self.session_str,
                    in_memory=True
                )
                logger.info("Userbot Service configured successfully!")
            except Exception as e:
                logger.error(f"Error configuring Userbot Service: {e}")
        else:
            logger.warning("Userbot credentials missing. Username-to-ID lookup is disabled.")
            
    async def start(self):
        async with self._lock:
            if self.client and not self.is_started:
                try:
                    logger.info("Starting Userbot Client...")
                    await self.client.start()
                    self.is_started = True
                    logger.info("Userbot Client started successfully!")
                except Exception as e:
                    logger.error(f"Failed to start Userbot Client: {e}")
                
    async def stop(self):
        async with self._lock:
            if self.client and self.is_started:
                try:
                    await self.client.stop()
                    self.is_started = False
                    logger.info("Userbot Client stopped successfully!")
                except Exception as e:
                    logger.error(f"Failed to stop Userbot Client: {e}")
                
    async def resolve_username(self, username: str) -> Optional[dict]:
        """
        Resolves a Telegram username to user details including numeric ID.
        username: Can be with or without @ prefix (e.g. '@username' or 'username').
        Returns: Dict containing {"telegram_id": int, "username": str, "name": str} or None.
        """
        if not self.client:
            return None
            
        if not self.is_started:
            await self.start()
            
        if not self.is_started:
            logger.error("Userbot is not started. Cannot resolve username.")
            return None
            
        username = username.strip().lstrip("@")
        if not username:
            return None
            
        try:
            # Pyrogram get_users handles username resolution over MTProto
            user = await self.client.get_users(username)
            if user:
                fullname = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Telegram User"
                return {
                    "telegram_id": user.id,
                    "username": user.username or username,
                    "name": fullname
                }
        except Exception as e:
            logger.error(f"Failed to resolve username '{username}': {e}")
            
        return None

# Singleton instance
userbot_service = UserbotService()
