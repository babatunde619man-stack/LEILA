from __future__ import annotations

import io
from datetime import timezone

import discord


async def generate_transcript(channel: discord.TextChannel) -> discord.File:
    buffer = io.StringIO()
    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{message.author}"
        content = message.clean_content.strip()
        buffer.write(f"[{timestamp}] {author}: {content}\n")
        for attachment in message.attachments:
            buffer.write(f"    [Attachment] {attachment.filename} -> {attachment.url}\n")
        for embed in message.embeds:
            buffer.write(f"    [Embed] {embed.title or 'Untitled'}\n")
    buffer.seek(0)
    return discord.File(buffer, filename=f"transcript-{channel.name}.txt")

