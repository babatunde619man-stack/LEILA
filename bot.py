from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import discord
from discord.ext import commands, tasks

from config import BOT_TOKEN, STATUS_ROTATION_INTERVAL
from utils.constants import GUILD_ID
from utils.data_manager import DataManager


logging.basicConfig(level=logging.INFO)


class BloxburgBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        super().__init__(command_prefix="/", intents=intents)
        self.data_manager = DataManager(Path.cwd())
        self.status_lines: list[str] = []

    async def setup_hook(self) -> None:
        await self.data_manager.ensure_all()
        await self.load_extension("cogs.ticketing")
        await self.load_extension("cogs.admin")
        await self.tree.sync(guild=discord.Object(id=GUILD_ID))
        self.rotate_status.start()

    async def on_ready(self) -> None:
        logging.info("✅ Logged in as %s", self.user)

    @tasks.loop(seconds=STATUS_ROTATION_INTERVAL)
    async def rotate_status(self) -> None:
        try:
            status_file = Path("data/status_lines.json")
            if status_file.exists():
                statuses = [line.strip() for line in status_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            else:
                statuses = []
            if statuses:
                activity = discord.Game(name=random.choice(statuses))
                await self.change_presence(activity=activity)
        except Exception as exc:
            logging.error("Status rotation error: %s", exc)


async def main() -> None:
    bot = BloxburgBot()
    async with bot:
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())