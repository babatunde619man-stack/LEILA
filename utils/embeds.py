from __future__ import annotations

import discord

from .constants import PANEL_BANNER_PATH, PRIMARY_COLOR


def teal_embed(
    *,
    title: str | None = None,
    description: str | None = None,
) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=PRIMARY_COLOR)


def ticket_panel_embed() -> tuple[discord.Embed, str | None]:
    embed = teal_embed(
        title="Open a Ticket",
        description="Choose **Purchase**, **Cash**, **Builds**, or **Support** below.",
    )
    banner_path = PANEL_BANNER_PATH if PANEL_BANNER_PATH else None
    return embed, banner_path