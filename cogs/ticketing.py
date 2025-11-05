from __future__ import annotations

import asyncio
import os
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils.constants import (
    GUILD_ID,
    OWNER_ID,
    PANEL_BANNER_PATH,
    TRANSCRIPTS_CHANNEL_ID,
    TRANSACTIONS_CHANNEL_ID,
    VOUCH_CHANNEL_ID,
)
from utils.data_manager import DataManager
from utils.embeds import teal_embed, ticket_panel_embed
from utils.roblox import owns_gamepass, resolve_username
from utils.transcripts import generate_transcript


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:80] or "ticket"


def format_price(amount: float | int) -> str:
    return f"${amount:,.2f}"


class TicketType:
    PURCHASE = "purchase"
    CASH = "cash"
    BUILDS = "builds"
    SUPPORT = "support"


class PaymentMethod:
    ROBUX = "robux"
    USD = "usd"
    ADOPT = "adopt"
    MM2 = "mm2"


@dataclass(slots=True)
class TicketState:
    ticket_type: str
    owner_id: int
    channel_id: int
    controls_message_id: int
    starter_message_ids: List[int] = field(default_factory=list)
    summary_message_id: Optional[int] = None
    payment_target_name: Optional[str] = None
    payment_is_cash: bool = False
    payment_method: Optional[str] = None
    product_name: Optional[str] = None
    product_data: Optional[Dict[str, Any]] = None
    cash_selection: Optional[Dict[str, Any]] = None
    custom_cash_amount: Optional[float] = None
    credential_messages: Dict[str, List[int]] = field(default_factory=lambda: {
        PaymentMethod.USD: [],
        PaymentMethod.ADOPT: [],
        PaymentMethod.MM2: [],
        PaymentMethod.ROBUX: [],
    })
    claim_role_id: Optional[int] = None
    claimed_by: Optional[int] = None
    manual_started: bool = False
    manual_message_id: Optional[int] = None
    reopen_overwrites: Dict[int, discord.PermissionOverwrite] = field(default_factory=dict)
    is_closed: bool = False


@dataclass(slots=True)
class PendingReview:
    key: str
    method: str
    ticket_id: int
    user_id: int
    channel_id: int
    price: float
    order_label: str
    submitted_info: str
    created_at: datetime
    roblox_user_id: Optional[int] = None
    gamepass_id: Optional[int] = None


class TicketingCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.data: DataManager = getattr(bot, "data_manager")
        self.session: Optional[aiohttp.ClientSession] = None
        self.ticket_states: Dict[int, TicketState] = {}
        self.pending_reviews: Dict[str, PendingReview] = {}
        self.cached_settings: Dict[str, Any] = {}
        self.categories = {
            TicketType.PURCHASE: "Purchase",
            TicketType.CASH: "Cash",
            TicketType.BUILDS: "Builds",
            TicketType.SUPPORT: "Support",
        }

    async def cog_load(self) -> None:
        await self.data.ensure_all()
        self.cached_settings = await self.data.load("settings")
        self.session = aiohttp.ClientSession()
        await self.bot.wait_until_ready()
        self.bot.add_view(TicketPanelView(self))

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()

    # region helpers
    async def get_settings(self) -> Dict[str, Any]:
        self.cached_settings = await self.data.load("settings")
        return self.cached_settings

    def get_state(self, channel_id: int) -> Optional[TicketState]:
        return self.ticket_states.get(channel_id)

    async def log_event(self, guild: discord.Guild, *, title: str, description: str, fields: Optional[Dict[str, str]] = None) -> None:
        channel = guild.get_channel(self.get_transactions_channel_id())
        if not isinstance(channel, discord.TextChannel):
            return
        embed = teal_embed(title=title, description=description)
        embed.timestamp = datetime.now(timezone.utc)
        if fields:
            for name, value in fields.items():
                embed.add_field(name=name, value=value, inline=False)
        await channel.send(embed=embed)

    def get_transactions_channel_id(self) -> int:
        return self.cached_settings.get("channels", {}).get("transactions_id", TRANSACTIONS_CHANNEL_ID)

    def get_transcripts_channel_id(self) -> int:
        return self.cached_settings.get("channels", {}).get("transcript_id", TRANSCRIPTS_CHANNEL_ID)

    # endregion

    # region panel
    @app_commands.command(name="ticketpanel", description="Post the Bloxburg Services ticket panel.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def ticketpanel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        embed, thumbnail_path = ticket_panel_embed()

        view = TicketPanelView(self)
        files: List[discord.File] = []
        if thumbnail_path and Path(thumbnail_path).exists():
            file = discord.File(thumbnail_path, filename="panel_banner.png")
            embed.set_thumbnail(url="attachment://panel_banner.png")
            files.append(file)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("This command must be used in a text channel.", ephemeral=True)
            return

        await channel.send(embed=embed, view=view, files=files)
        await interaction.followup.send("Ticket panel posted.", ephemeral=True)

    # endregion

    async def handle_panel_selection(self, interaction: discord.Interaction, ticket_type: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("You can only open tickets in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await self.create_ticket(interaction, ticket_type)
        if channel is None:
            await interaction.followup.send("Unable to create ticket. Please contact staff.", ephemeral=True)
            return

        await interaction.followup.send(f"Created {channel.mention}.", ephemeral=True)

    async def ensure_category(self, guild: discord.Guild, ticket_type: str) -> discord.CategoryChannel:
        name = self.categories[ticket_type]
        category = discord.utils.get(guild.categories, name=name)
        if category:
            return category
        return await guild.create_category(name=name, reason="Bloxburg Services ticket category")

    async def create_ticket(self, interaction: discord.Interaction, ticket_type: str) -> Optional[discord.TextChannel]:
        guild = interaction.guild
        if guild is None:
            return None

        category = await self.ensure_category(guild, ticket_type)
        overwrites = await self.build_overwrites(guild, interaction.user, ticket_type)
        channel_name = f"{ticket_type}-{slugify(interaction.user.display_name)}"
        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Ticket opened by {interaction.user} ({ticket_type})",
            topic=f"Ticket for {interaction.user.id} ({ticket_type})",
        )

        controls_view = TicketControlsView(self)
        controls_msg = await channel.send(view=controls_view)

        state = TicketState(
            ticket_type=ticket_type,
            owner_id=interaction.user.id,
            channel_id=channel.id,
            controls_message_id=controls_msg.id,
        )
        state.reopen_overwrites = {
            target.id: overwrite
            for target, overwrite in channel.overwrites.items()
            if isinstance(target, (discord.Role, discord.Member))
        }
        self.ticket_states[channel.id] = state

        await self.log_event(
            guild,
            title="Ticket Created",
            description=f"{interaction.user.mention} opened a {ticket_type.title()} ticket in {channel.mention}.",
        )

        starter_ids = await self.render_ticket_intro(channel, ticket_type)
        state.starter_message_ids.extend(starter_ids)
        return channel

    async def build_overwrites(
        self,
        guild: discord.Guild,
        opener: discord.abc.User,
        ticket_type: str,
    ) -> Dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
        settings = await self.get_settings()
        overwrites: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            opener: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        support_role_id = settings.get("roles", {}).get("support")
        if support_role_id:
            role = guild.get_role(support_role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        if ticket_type == TicketType.CASH:
            cash_role_id = settings.get("roles", {}).get("cashseller")
            if cash_role_id:
                role = guild.get_role(cash_role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        return overwrites

    async def render_ticket_intro(self, channel: discord.TextChannel, ticket_type: str) -> List[int]:
        message_ids: List[int] = []
        if ticket_type == TicketType.PURCHASE:
            embed = teal_embed(title="Purchase Ticket", description="Pick your product from the dropdown below.")
            products = await self.data.load("products")
            if not products:
                embed.add_field(name="No Products", value="An admin must add products with /addproduct.")
            view = ProductSelectView(self)
            msg = await channel.send(embed=embed, view=view)
            message_ids.append(msg.id)
        elif ticket_type == TicketType.CASH:
            embed = teal_embed(title="Cash Ticket", description="Select a cash amount tier, or choose Custom.")
            view = CashSelectView(self)
            msg = await channel.send(embed=embed, view=view)
            message_ids.append(msg.id)
        elif ticket_type == TicketType.BUILDS:
            embed = teal_embed(title="Build Request", description="Fill the form with your details. We’ll follow up here.")
            view = BuildFormView(self)
            msg = await channel.send(embed=embed, view=view)
            message_ids.append(msg.id)
        else:
            embed = teal_embed(title="Support Ticket", description="Click “Open Support Form” and tell us what you need.")
            view = SupportFormView(self)
            msg = await channel.send(embed=embed, view=view)
            message_ids.append(msg.id)
        return message_ids

    # region ticket controls
    async def handle_add_user(self, interaction: discord.Interaction, member_input: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This isn’t a ticket channel.", ephemeral=True)
            return
        state = self.get_state(interaction.channel.id)
        if state is None:
            await interaction.response.send_message("Ticket state missing.", ephemeral=True)
            return
        if interaction.user.id not in {state.owner_id} and not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("Only the ticket owner or staff can add members.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Guild not found.", ephemeral=True)
            return

        member = self.resolve_member(guild, member_input)
        if member is None:
            await interaction.response.send_message("Member not found.", ephemeral=True)
            return

        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await interaction.response.send_message(f"Added {member.mention} to this ticket.", ephemeral=True)

    def resolve_member(self, guild: discord.Guild, query: str) -> Optional[discord.Member]:
        match = re.search(r"(\d{17,19})", query)
        if match:
            member = guild.get_member(int(match.group(1)))
            if member:
                return member
        query = query.strip().lstrip("@")
        for member in guild.members:
            if member.name.lower() == query.lower() or member.display_name.lower() == query.lower():
                return member
        return None

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This isn’t a ticket channel.", ephemeral=True)
            return
        state = self.get_state(interaction.channel.id)
        if state is None:
            await interaction.response.send_message("Ticket state missing.", ephemeral=True)
            return
        if state.is_closed:
            await interaction.response.send_message("This ticket is already closed.", ephemeral=True)
            return

        state.is_closed = True
        transcripts_channel = interaction.guild.get_channel(self.get_transcripts_channel_id()) if interaction.guild else None
        transcript_file = await generate_transcript(interaction.channel)
        if isinstance(transcripts_channel, discord.TextChannel):
            await transcripts_channel.send(content=f"Transcript for {interaction.channel.mention}", file=transcript_file)

        opener = interaction.guild.get_member(state.owner_id) if interaction.guild else None
        if opener:
            await interaction.channel.set_permissions(opener, view_channel=True, send_messages=False, read_message_history=True)

        embed = teal_embed(title="Ticket Closed", description="This ticket has been closed. Use the buttons below to reopen or delete.")
        view = ClosedTicketView(self)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("Ticket closed.", ephemeral=True)

    async def reopen_ticket(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This isn’t a ticket channel.", ephemeral=True)
            return
        state = self.get_state(interaction.channel.id)
        if state is None:
            await interaction.response.send_message("Ticket state missing.", ephemeral=True)
            return
        if not state.is_closed:
            await interaction.response.send_message("Ticket is already open.", ephemeral=True)
            return

        guild = interaction.guild
        if guild:
            opener = guild.get_member(state.owner_id)
            if opener:
                await interaction.channel.set_permissions(opener, view_channel=True, send_messages=True, read_message_history=True)
            for target_id, overwrite in state.reopen_overwrites.items():
                target = guild.get_role(target_id) or guild.get_member(target_id)
                if target:
                    await interaction.channel.set_permissions(target, overwrite=overwrite)

        state.is_closed = False
        controls_view = TicketControlsView(self)
        await interaction.channel.send("Ticket reopened.", view=controls_view)
        await interaction.response.send_message("Reopened the ticket.", ephemeral=True)

    async def delete_ticket(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This isn’t a ticket channel.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("Only staff can delete tickets.", ephemeral=True)
            return
        await interaction.response.send_message("Deleting...", ephemeral=True)
        self.ticket_states.pop(interaction.channel.id, None)
        await interaction.channel.delete(reason=f"Deleted by {interaction.user}")

    # endregion

    # region claim & manual fulfilment
    async def handle_claim(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This isn’t a ticket channel.", ephemeral=True)
            return
        state = self.get_state(interaction.channel.id)
        if state is None:
            await interaction.response.send_message("Ticket state missing.", ephemeral=True)
            return
        if state.claimed_by and state.claimed_by != interaction.user.id:
            await interaction.response.send_message("This ticket is already claimed.", ephemeral=True)
            return

        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Guild missing.", ephemeral=True)
            return

        allowed = interaction.user.guild_permissions.manage_channels
        if not allowed and state.claim_role_id:
            role = guild.get_role(state.claim_role_id)
            allowed = role in getattr(interaction.user, "roles", [])
        if not allowed:
            await interaction.response.send_message("You don’t have permission to claim this ticket.", ephemeral=True)
            return

        state.claimed_by = interaction.user.id
        await interaction.channel.edit(name=f"claimed-{slugify(interaction.user.display_name)}")

        if state.claim_role_id:
            role = guild.get_role(state.claim_role_id)
            if role:
                await interaction.channel.set_permissions(role, overwrite=discord.PermissionOverwrite(view_channel=False))
        await interaction.channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True)

        await self.refresh_payment_view(interaction.channel, state)
        await self.log_event(
            guild,
            title="Ticket Claimed",
            description=f"{interaction.user.mention} claimed {interaction.channel.mention}.",
        )
        await interaction.response.send_message("Ticket claimed.", ephemeral=True)

    async def start_manual_order(self, interaction: discord.Interaction, state: TicketState) -> bool:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
            return False
        if state.manual_started:
            await interaction.response.send_message("Order already started.", ephemeral=True)
            return False
        if not self.is_authorised_seller(interaction.user, state):
            await interaction.response.send_message("You are not authorised to start this order.", ephemeral=True)
            return False

        state.manual_started = True
        await interaction.response.send_message("Marked as started.", ephemeral=True)
        return True

    async def complete_manual_order(self, interaction: discord.Interaction, state: TicketState) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
            return
        if not self.is_authorised_seller(interaction.user, state):
            await interaction.response.send_message("You are not authorised to complete this order.", ephemeral=True)
            return

        guild = interaction.guild
        buyer = guild.get_member(state.owner_id) if guild else None
        await self.complete_order(interaction.channel, buyer, state, method=state.payment_method or "manual")
        if state.manual_message_id:
            try:
                message = await interaction.channel.fetch_message(state.manual_message_id)
                await message.edit(view=None)
            except discord.NotFound:
                pass
        state.manual_message_id = None
        await interaction.response.send_message("Order marked complete.", ephemeral=True)

    async def refresh_manual_view(self, interaction: discord.Interaction, state: TicketState) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        if not state.manual_message_id:
            return
        try:
            message = await interaction.channel.fetch_message(state.manual_message_id)
        except discord.NotFound:
            return
        await message.edit(view=ManualFulfillmentView(self, state))

    def is_authorised_seller(self, member: discord.Member | discord.User, state: TicketState) -> bool:
        if isinstance(member, discord.User):
            return False
        if member.guild_permissions.manage_channels:
            return True
        if state.claimed_by and state.claimed_by != member.id:
            return False
        if state.claim_role_id:
            role = member.guild.get_role(state.claim_role_id)
            if role and role in member.roles:
                return True
        return False

    async def start_manual_fulfillment(
        self,
        channel: discord.TextChannel,
        state: TicketState,
        buyer: Optional[discord.Member],
        method: str,
        *,
        roblox_username: Optional[str] = None,
    ) -> None:
        description_lines = ["A seller will complete your order shortly."]
        if roblox_username:
            description_lines.append(f"Roblox Username: **{roblox_username}**")
        embed = teal_embed(title="Manual Fulfillment", description="\n".join(description_lines))
        view = ManualFulfillmentView(self, state)
        message = await channel.send(embed=embed, view=view)
        state.manual_message_id = message.id
        await self.log_event(
            channel.guild,
            title="Manual Fulfillment",
            description=f"Manual order started for {channel.mention} ({method.upper()}).",
        )

    async def deliver_digital_product(self, buyer: discord.Member, state: TicketState, method: str) -> None:
        if state.ticket_type != TicketType.PURCHASE or not state.product_data:
            return
        product = state.product_data
        embed = teal_embed(title="Order Delivered", description="Thank you for purchasing from Bloxburg Services!")
        embed.add_field(name="Payment Method", value=method.upper())
        embed.add_field(name="Vouch", value=f"Please vouch here: <#{VOUCH_CHANNEL_ID}>")

        files: List[discord.File] = []
        if product.get("type") == "file" and product.get("file_path"):
            path = Path(product["file_path"])
            if not path.is_absolute():
                path = Path.cwd() / product["file_path"]
            if path.exists():
                files.append(discord.File(path, filename=path.name))
        elif product.get("type") == "key" and product.get("key_path"):
            key = self.pop_key(product["key_path"])
            if key:
                embed.add_field(name="Your Key", value=f"`{key}`", inline=False)

        try:
            await buyer.send(embed=embed, files=files)
        except discord.Forbidden:
            channel = buyer.guild.get_channel(state.channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(f"{buyer.mention}, I couldn’t DM you. Please enable DMs to receive your order.")

    def pop_key(self, relative_path: str) -> Optional[str]:
        path = Path(relative_path)
        if not path.is_absolute():
            path = Path.cwd() / relative_path
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        key = lines[0].strip()
        remaining = "\n".join(line for line in lines[1:] if line.strip())
        path.write_text(remaining, encoding="utf-8")
        return key or None

    async def complete_order(
        self,
        channel: discord.TextChannel,
        buyer: Optional[discord.Member],
        state: TicketState,
        *,
        method: str,
    ) -> None:
        if buyer:
            await self.grant_buyer_role(buyer)
        embed = teal_embed(title="Order Completed", description=f"Thanks for purchasing — please vouch here: <#{VOUCH_CHANNEL_ID}>")
        await channel.send(embed=embed)
        await self.data.update_order_status(state.channel_id, "completed", method=method)
        await self.log_event(
            channel.guild,
            title="Order Fulfilled",
            description=f"Order complete in {channel.mention} via {method.upper()}.",
        )

    async def grant_buyer_role(self, member: discord.Member) -> None:
        settings = await self.get_settings()
        role_id = settings.get("roles", {}).get("buyer")
        if not role_id:
            return
        role = member.guild.get_role(role_id)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="Bloxburg Services order completed")
            except discord.Forbidden:
                pass

    async def refresh_payment_view(self, channel: discord.TextChannel, state: TicketState) -> None:
        if not state.summary_message_id:
            return
        try:
            message = await channel.fetch_message(state.summary_message_id)
        except discord.NotFound:
            return
        view = PaymentMethodView(self, state)
        await message.edit(view=view)

    # endregion

    # region payment flows
    def get_order_label(self, state: TicketState) -> Optional[str]:
        if state.ticket_type == TicketType.PURCHASE:
            return state.product_name
        if state.ticket_type == TicketType.CASH:
            if state.cash_selection:
                return state.cash_selection.get("label")
            if state.custom_cash_amount:
                return f"Custom Cash ({format_price(state.custom_cash_amount)})"
        return None

    def get_order_price(self, state: TicketState) -> float:
        if state.ticket_type == TicketType.PURCHASE and state.product_data:
            return float(state.product_data.get("price_usd", 0.0))
        if state.ticket_type == TicketType.CASH:
            if state.cash_selection:
                return float(state.cash_selection.get("price_usd", 0.0))
            if state.custom_cash_amount:
                return float(state.custom_cash_amount)
        return 0.0

    def get_gamepass_info(self, state: TicketState) -> tuple[Optional[str], int]:
        if state.ticket_type == TicketType.PURCHASE and state.product_data:
            return state.product_data.get("gamepass_url"), int(state.product_data.get("gamepass_id") or 0)
        if state.ticket_type == TicketType.CASH and state.cash_selection:
            return state.cash_selection.get("gamepass_url"), int(state.cash_selection.get("gamepass_id") or 0)
        return None, 0

    async def handle_product_selection(self, interaction: discord.Interaction, product_name: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Select within a ticket channel.", ephemeral=True)
            return
        state = self.get_state(interaction.channel.id)
        if state is None or state.ticket_type != TicketType.PURCHASE:
            await interaction.response.send_message("This isn’t a purchase ticket.", ephemeral=True)
            return

        products = await self.data.load("products")
        product = products.get(product_name)
        if product is None:
            await interaction.response.send_message("Product not found.", ephemeral=True)
            return

        state.product_name = product_name
        state.product_data = product
        state.payment_target_name = product_name
        state.payment_is_cash = False
        state.payment_method = None
        state.claim_role_id = int(product.get("seller_role_id") or 0) or None

        embed = self.build_product_embed(product_name, product)
        view = PaymentMethodView(self, state)
        message = await interaction.channel.send(embed=embed, view=view)
        state.summary_message_id = message.id
        await self.log_event(
            interaction.guild,
            title="Product Selected",
            description=f"{interaction.user.mention} selected **{product_name}** in {interaction.channel.mention}.",
        )
        await interaction.response.send_message(f"Selected **{product_name}**.", ephemeral=True)

    def build_product_embed(self, name: str, product: Dict[str, Any]) -> discord.Embed:
        title = f"{product.get('emoji', '')} {name}".strip()
        embed = teal_embed(title=title, description=product.get("description"))
        embed.add_field(name="Price", value=format_price(float(product.get("price_usd", 0.0))))
        embed.add_field(name="Type", value=product.get("type", "normal"))
        if product.get("type") == "role" and product.get("role_id"):
            embed.add_field(name="Role", value=f"<@&{int(product['role_id'])}>")
        if product.get("type") == "key" and product.get("key_path"):
            stock = self.count_key_stock(product["key_path"])
            embed.add_field(name="Stock", value=str(stock))
        if product.get("giftcard_url"):
            embed.add_field(name="Giftcard Link", value=f"[Link]({product['giftcard_url']})", inline=False)
        if product.get("gamepass_url"):
            embed.add_field(name="Gamepass Link", value=f"[Link]({product['gamepass_url']})", inline=False)
        if product.get("mm2_value"):
            embed.add_field(name="MM2 Value", value=str(product["mm2_value"]))
        if product.get("adoptme_value"):
            embed.add_field(name="AdoptMe Value", value=str(product["adoptme_value"]))
        if product.get("image_url"):
            embed.set_image(url=product["image_url"])
        return embed

    def count_key_stock(self, relative_path: str) -> int:
        path = Path(relative_path)
        if not path.is_absolute():
            path = Path.cwd() / relative_path
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    async def handle_cash_selection(self, interaction: discord.Interaction, value: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Select within a ticket channel.", ephemeral=True)
            return
        state = self.get_state(interaction.channel.id)
        if state is None or state.ticket_type != TicketType.CASH:
            await interaction.response.send_message("This isn’t a cash ticket.", ephemeral=True)
            return

        if value == "__custom__":
            await interaction.response.send_modal(CashCustomModal(self))
            return

        cash_data = await self.data.load("cash")
        tiers = cash_data.get("tiers", [])
        tier = next((item for item in tiers if item.get("label") == value), None)
        if tier is None:
            await interaction.response.send_message("Tier not found.", ephemeral=True)
            return

        state.cash_selection = tier
        state.custom_cash_amount = None
        state.payment_target_name = tier.get("label")
        state.payment_is_cash = True
        state.payment_method = None
        state.claim_role_id = int(cash_data.get("seller_role_id") or 0) or None

        embed = self.build_cash_embed(tier)
        view = PaymentMethodView(self, state)
        message = await interaction.channel.send(embed=embed, view=view)
        state.summary_message_id = message.id
        await self.log_event(
            interaction.guild,
            title="Cash Tier Selected",
            description=f"{interaction.user.mention} selected **{value}** in {interaction.channel.mention}.",
        )
        await interaction.response.send_message(f"Selected **{value}**.", ephemeral=True)

    def build_cash_embed(self, tier: Dict[str, Any]) -> discord.Embed:
        title = f"{tier.get('emoji', '')} {tier.get('label')}".strip()
        embed = teal_embed(title=title, description=tier.get("description"))
        embed.add_field(name="Price", value=format_price(float(tier.get("price_usd", 0.0))))
        if tier.get("giftcard_url"):
            embed.add_field(name="Giftcard Link", value=f"[Link]({tier['giftcard_url']})", inline=False)
        if tier.get("gamepass_url"):
            embed.add_field(name="Gamepass Link", value=f"[Link]({tier['gamepass_url']})", inline=False)
        if tier.get("mm2_value"):
            embed.add_field(name="MM2 Value", value=str(tier["mm2_value"]))
        if tier.get("adoptme_value"):
            embed.add_field(name="AdoptMe Value", value=str(tier["adoptme_value"]))
        return embed

    async def handle_custom_cash(self, interaction: discord.Interaction, amount: float) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
            return
        state = self.get_state(interaction.channel.id)
        if state is None or state.ticket_type != TicketType.CASH:
            await interaction.response.send_message("This isn’t a cash ticket.", ephemeral=True)
            return

        cash_data = await self.data.load("cash")
        state.cash_selection = None
        state.custom_cash_amount = amount
        state.payment_target_name = f"Custom Cash ({format_price(amount)})"
        state.payment_is_cash = True
        state.payment_method = None
        state.claim_role_id = int(cash_data.get("seller_role_id") or 0) or None

        embed = teal_embed(title="Custom Cash Request")
        embed.add_field(name="Requested Amount", value=format_price(amount))
        embed.description = "A team member will follow up with pricing confirmation."
        view = PaymentMethodView(self, state)
        message = await interaction.channel.send(embed=embed, view=view)
        state.summary_message_id = message.id
        await interaction.response.send_message("Custom cash request submitted.", ephemeral=True)

    async def handle_payment_method(self, interaction: discord.Interaction, state: TicketState, method: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
            return
        label = self.get_order_label(state)
        if label is None:
            await interaction.response.send_message("Select a product or tier first.", ephemeral=True)
            return

        state.payment_method = method
        await self.log_event(
            interaction.guild,
            title="Payment Method Chosen",
            description=f"{interaction.user.mention} chose **{method.upper()}** in {interaction.channel.mention}.",
            fields={"Order": label},
        )

        if method == PaymentMethod.ROBUX:
            await self.start_robux_flow(interaction, state)
        elif method == PaymentMethod.USD:
            await self.start_usd_flow(interaction, state)
        elif method == PaymentMethod.ADOPT:
            await self.start_adopt_flow(interaction, state)
        elif method == PaymentMethod.MM2:
            await self.start_mm2_flow(interaction, state)
        else:
            await interaction.response.send_message("Unknown payment method.", ephemeral=True)

    async def start_robux_flow(self, interaction: discord.Interaction, state: TicketState) -> None:
        label = self.get_order_label(state) or "Order"
        gamepass_url, _ = self.get_gamepass_info(state)
        embed = teal_embed(title="Robux Payment (Gamepass)")
        embed.add_field(name="Product" if not state.payment_is_cash else "Cash Tier", value=label)
        embed.add_field(name="Gamepass Link", value=f"[Purchase here]({gamepass_url})" if gamepass_url else "Not configured", inline=False)
        embed.add_field(
            name="Instructions",
            value="Set your Roblox inventory to public. Click **Submit Username** once you have purchased the gamepass.",
            inline=False,
        )
        view = RobuxInstructionsView(self, state)
        await interaction.response.send_message(embed=embed, view=view)

    async def process_roblox_username(self, interaction: discord.Interaction, state: TicketState, username: str) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This must be used in a ticket channel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        session = self.session or aiohttp.ClientSession()
        if self.session is None:
            self.session = session

        roblox_user_id = await resolve_username(session, username)
        if not roblox_user_id:
            await interaction.followup.send("Roblox username not found. Double-check spelling and try again.", ephemeral=True)
            return

        _, gamepass_id = self.get_gamepass_info(state)
        if gamepass_id <= 0:
            await interaction.followup.send("Gamepass not configured for this selection. Contact staff.", ephemeral=True)
            return

        owns = await owns_gamepass(session, roblox_user_id, gamepass_id)
        if not owns:
            await interaction.followup.send("Ownership not detected. Ensure the pass is purchased and inventory is public.", ephemeral=True)
            return

        if await self.is_repeat_robux_order(roblox_user_id, gamepass_id, state):
            await self.handle_manual_robux_review(interaction, state, username, roblox_user_id, gamepass_id)
            return

        await self.record_order(state, method=PaymentMethod.ROBUX, roblox_user_id=roblox_user_id, gamepass_id=gamepass_id)
        await self.handle_auto_or_manual_fulfillment(
            interaction.channel,
            interaction.user,
            state,
            method=PaymentMethod.ROBUX,
            roblox_username=username,
        )
        await interaction.followup.send("Gamepass ownership verified!", ephemeral=True)

    async def is_repeat_robux_order(self, roblox_user_id: int, gamepass_id: int, state: TicketState) -> bool:
        orders = await self.data.load("orders")
        label = self.get_order_label(state)
        for order in orders.get("orders", []):
            if (
                order.get("roblox_user_id") == roblox_user_id
                and order.get("gamepass_id") == gamepass_id
                and order.get("product_name") == label
                and order.get("status") in {"approved", "completed"}
            ):
                return True
        return False

    async def handle_manual_robux_review(
        self,
        interaction: discord.Interaction,
        state: TicketState,
        username: str,
        roblox_user_id: int,
        gamepass_id: int,
    ) -> None:
        channel = interaction.channel
        assert isinstance(channel, discord.TextChannel)
        role = channel.guild.get_role(state.claim_role_id) if state.claim_role_id else None
        embed = teal_embed(
            title="Manual Verification Required",
            description="We detected a prior purchase. Delete the pass, repurchase, and wait for staff approval.",
        )
        embed.add_field(name="Roblox Username", value=username)
        mention = role.mention if role else None
        message = await channel.send(content=mention, embed=embed)
        state.credential_messages[PaymentMethod.ROBUX].append(message.id)
        await self.queue_owner_review(
            state,
            method="Robux Manual",
            submitted_info=f"Roblox Username: {username}",
            roblox_user_id=roblox_user_id,
            gamepass_id=gamepass_id,
        )
        await interaction.followup.send("Manual approval required. The owner has been notified.", ephemeral=True)

    async def record_order(
        self,
        state: TicketState,
        *,
        method: str,
        roblox_user_id: Optional[int] = None,
        gamepass_id: Optional[int] = None,
    ) -> None:
        entry = {
            "ticket_id": state.channel_id,
            "user_id": state.owner_id,
            "product_name": self.get_order_label(state) or "Order",
            "status": "approved",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": method,
        }
        if roblox_user_id:
            entry["roblox_user_id"] = roblox_user_id
        if gamepass_id:
            entry["gamepass_id"] = gamepass_id
        await self.data.append_order(entry)
        await self.update_stats(state, method)

    async def queue_owner_review(
        self,
        state: TicketState,
        *,
        method: str,
        submitted_info: str,
        roblox_user_id: Optional[int] = None,
        gamepass_id: Optional[int] = None,
    ) -> None:
        guild = self.bot.get_guild(GUILD_ID)
        label = self.get_order_label(state) or "Order"
        price = self.get_order_price(state)
        key = f"{state.channel_id}:{datetime.now(timezone.utc).timestamp()}"
        pending = PendingReview(
            key=key,
            method=method,
            ticket_id=state.channel_id,
            user_id=state.owner_id,
            channel_id=state.channel_id,
            price=price,
            order_label=label,
            submitted_info=submitted_info,
            created_at=datetime.now(timezone.utc),
            roblox_user_id=roblox_user_id,
            gamepass_id=gamepass_id,
        )
        self.pending_reviews[key] = pending

        owner = self.bot.get_user(OWNER_ID) or await self.bot.fetch_user(OWNER_ID)
        ticket_jump = f"https://discord.com/channels/{GUILD_ID}/{state.channel_id}"
        embed = teal_embed(title=f"Payment Review — {method}")
        embed.add_field(name="Guild Name", value=guild.name if guild else "Guild")
        embed.add_field(name="Ticket Jump Link", value=f"[Open Ticket]({ticket_jump})", inline=False)
        embed.add_field(name="User", value=f"<@{state.owner_id}>")
        embed.add_field(name="Product/Tier", value=label)
        embed.add_field(name="Price", value=format_price(price))
        embed.add_field(name="Submitted Info", value=submitted_info, inline=False)
        view = OwnerReviewView(self, key)
        try:
            await owner.send(embed=embed, view=view)
        except discord.Forbidden:
            if guild:
                channel = guild.get_channel(state.channel_id)
                if isinstance(channel, discord.TextChannel):
                    await channel.send("Could not DM the owner. Please alert them manually.")

        if guild:
            ticket_channel = guild.get_channel(state.channel_id)
            ticket_label = ticket_channel.mention if isinstance(ticket_channel, discord.TextChannel) else "ticket"
            await self.log_event(
                guild,
                title="Owner Review Requested",
                description=f"Sent {method} details to owner for {ticket_label}.",
            )

    async def handle_owner_decision(self, interaction: discord.Interaction, pending_key: str, *, approved: bool) -> None:
        pending = self.pending_reviews.pop(pending_key, None)
        if pending is None:
            await interaction.response.send_message("Review entry not found or already processed.", ephemeral=True)
            return

        guild = self.bot.get_guild(GUILD_ID)
        channel = guild.get_channel(pending.channel_id) if guild else None
        state = self.ticket_states.get(pending.ticket_id)
        if not isinstance(channel, discord.TextChannel) or not state:
            await interaction.response.send_message("Ticket channel unavailable.", ephemeral=True)
            return

        method = self.get_canonical_method(pending.method)
        if approved:
            await interaction.response.send_message("Approved.", ephemeral=True)
            await self.record_order(state, method=method)
            await self.handle_auto_or_manual_fulfillment(channel, interaction.user, state, method=method, approved_by_owner=True)
            await channel.send(embed=teal_embed(title="Payment Approved", description="The owner approved your payment. We’ll proceed with fulfillment."))
            await self.data.update_order_status(state.channel_id, "approved", method=method)
        else:
            await interaction.response.send_message("Marked as not working.", ephemeral=True)
            await self.delete_last_credential_embed(channel, state, method)
            await channel.send(embed=teal_embed(title="Payment Issue", description="The provided information did not work. Please submit a new payment method."))
            await self.data.update_order_status(state.channel_id, "failed", method=method)

        if guild:
            await self.log_event(
                guild,
                title="Owner Decision",
                description=f"Owner {'approved' if approved else 'denied'} {pending.method} for {channel.mention}.",
            )

    def get_canonical_method(self, method_label: str) -> str:
        lowered = method_label.lower()
        if "robux" in lowered:
            return PaymentMethod.ROBUX
        if "usd" in lowered or "gift" in lowered:
            return PaymentMethod.USD
        if "adopt" in lowered:
            return PaymentMethod.ADOPT
        if "mm2" in lowered:
            return PaymentMethod.MM2
        return lowered

    async def delete_last_credential_embed(self, channel: discord.TextChannel, state: TicketState, method: str) -> None:
        stack = state.credential_messages.get(method)
        if not stack:
            return
        message_id = stack.pop() if stack else None
        if not message_id:
            return
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            return
        await message.delete()

    async def start_usd_flow(self, interaction: discord.Interaction, state: TicketState) -> None:
        modal = USDModal(self, state)
        await interaction.response.send_modal(modal)

    async def start_adopt_flow(self, interaction: discord.Interaction, state: TicketState) -> None:
        embed = teal_embed(
            title="Adopt Me Payment",
            description=textwrap.dedent(
                """
                Check values at https://adoptmevalues.gg
                Use a fresh alt (no email).
                Click Submit Account to send credentials (username : password).
                """
            ).strip(),
        )
        view = AdoptMeView(self, state)
        await interaction.response.send_message(embed=embed, view=view)

    async def start_mm2_flow(self, interaction: discord.Interaction, state: TicketState) -> None:
        embed = teal_embed(title="MM2 Payment")
        embed.description = textwrap.dedent(
            """
            Check values at http://mm2values.com
            Each item must be worth ≥ 500 to be eligible.
            """
        ).strip()
        view = MM2PaymentView(self, state)
        await interaction.response.send_message(embed=embed, view=view)

    # endregion

    # region slash commands - support
    @app_commands.command(name="close", description="Close this ticket and upload the transcript.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def slash_close(self, interaction: discord.Interaction) -> None:
        await self.close_ticket(interaction)

    @app_commands.command(name="stats", description="View Bloxburg Services order stats.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def stats(self, interaction: discord.Interaction) -> None:
        stats = await self.data.load("stats")
        totals = stats.get("totals", {})
        by_type = stats.get("by_type", {})
        embed = teal_embed(title="Order Stats")
        embed.add_field(
            name="Successful Orders",
            value=textwrap.dedent(
                f"""
                Overall: {totals.get('overall', 0)}
                Robux: {totals.get('robux', 0)}
                USD: {totals.get('usd', 0)}
                Adopt Me: {totals.get('adopt', 0)}
                MM2: {totals.get('mm2', 0)}
                """
            ).strip(),
            inline=False,
        )
        embed.add_field(
            name="By Product Type",
            value=textwrap.dedent(
                f"""
                Normal: {by_type.get('normal', 0)}
                File: {by_type.get('file', 0)}
                Key: {by_type.get('key', 0)}
                Role: {by_type.get('role', 0)}
                """
            ).strip(),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # endregion

    # region internal utilities
    async def update_stats(self, state: TicketState, method: str) -> None:
        async def mutate(payload: Dict[str, Any]) -> None:
            payload.setdefault("totals", {})
            payload.setdefault("by_type", {})
            payload["totals"]["overall"] = payload["totals"].get("overall", 0) + 1
            payload["totals"][method] = payload["totals"].get(method, 0) + 1
            if state.product_data:
                product_type = state.product_data.get("type", "normal")
                payload["by_type"][product_type] = payload["by_type"].get(product_type, 0) + 1

        await self.data.update("stats", mutate)

    # endregion


# region views


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(cog))


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, cog: TicketingCog) -> None:
        self.cog = cog
        options = self.build_options()
        super().__init__(
            placeholder="Select a ticket type",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="blox:panel:select",
        )

    def build_options(self) -> List[discord.SelectOption]:
        settings = self.cog.cached_settings
        panel_opts = settings.get("panel", {}).get("options", {})
        options = [discord.SelectOption(label="Purchase", value=TicketType.PURCHASE, emoji="🛒")]
        if panel_opts.get("cash_available", True):
            options.append(discord.SelectOption(label="Cash", value=TicketType.CASH, emoji="💵"))
        if panel_opts.get("builds_available", True):
            options.append(discord.SelectOption(label="Builds", value=TicketType.BUILDS, emoji="🏗️"))
        options.append(discord.SelectOption(label="Support", value=TicketType.SUPPORT, emoji="🆘"))
        return options

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_panel_selection(interaction, self.values[0])


class TicketControlsView(discord.ui.View):
    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.primary, custom_id="blox:ticket:adduser")
    async def add_user(self, interaction: discord.Interaction, _) -> None:
        modal = AddUserModal(self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="blox:ticket:close")
    async def close_ticket(self, interaction: discord.Interaction, _) -> None:
        await self.cog.close_ticket(interaction)


class AddUserModal(discord.ui.Modal, title="Add User"):
    user_input: discord.ui.TextInput = discord.ui.TextInput(
        label="User ID or @mention",
        placeholder="123456789012345678",
        required=True,
        custom_id="blox:modal:adduser",
    )

    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_add_user(interaction, str(self.user_input.value))


class ClosedTicketView(discord.ui.View):
    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Reopen", style=discord.ButtonStyle.primary, custom_id="blox:ticket:reopen")
    async def reopen(self, interaction: discord.Interaction, _) -> None:
        await self.cog.reopen_ticket(interaction)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="blox:ticket:delete")
    async def delete(self, interaction: discord.Interaction, _) -> None:
        await self.cog.delete_ticket(interaction)


class ProductSelectView(discord.ui.View):
    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(ProductSelect(cog))


class ProductSelect(discord.ui.Select):
    def __init__(self, cog: TicketingCog) -> None:
        self.cog = cog
        products = asyncio.run_coroutine_threadsafe(cog.data.load("products"), cog.bot.loop).result()
        options: List[discord.SelectOption] = []
        for name, info in products.items():
            label = f"{info.get('emoji', '')} {name}".strip()
            description = f"{format_price(float(info.get('price_usd', 0.0)))} • {info.get('type', 'normal')}"
            options.append(discord.SelectOption(label=label, value=name, description=description))
        if not options:
            options.append(discord.SelectOption(label="No products configured", value="__none__", default=True))
        super().__init__(
            placeholder="Select a product",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="blox:purchase:product",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "__none__":
            await interaction.response.send_message("No products configured.", ephemeral=True)
            return
        await self.cog.handle_product_selection(interaction, self.values[0])


class CashSelectView(discord.ui.View):
    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.add_item(CashSelect(cog))


class CashSelect(discord.ui.Select):
    def __init__(self, cog: TicketingCog) -> None:
        self.cog = cog
        cash = asyncio.run_coroutine_threadsafe(cog.data.load("cash"), cog.bot.loop).result()
        options: List[discord.SelectOption] = []
        for tier in cash.get("tiers", []):
            label = f"{tier.get('emoji', '')} {tier.get('label')}".strip()
            options.append(discord.SelectOption(label=label, value=tier.get('label'), description=format_price(float(tier.get('price_usd', 0.0)))))
        options.append(discord.SelectOption(label="Custom", value="__custom__", description="Enter a custom amount"))
        super().__init__(
            placeholder="Select a cash tier",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="blox:cash:tier",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_cash_selection(interaction, self.values[0])


class CashCustomModal(discord.ui.Modal, title="Custom Cash Amount"):
    amount: discord.ui.TextInput = discord.ui.TextInput(
        label="Cash Amount",
        placeholder="100.0",
        required=True,
        custom_id="blox:cash:custom",
    )

    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = float(str(self.amount.value))
        except ValueError:
            await interaction.response.send_message("Enter a valid number.", ephemeral=True)
            return
        if value <= 0:
            await interaction.response.send_message("Amount must be greater than zero.", ephemeral=True)
            return
        await self.cog.handle_custom_cash(interaction, value)


class BuildFormView(discord.ui.View):
    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Open Build Form", style=discord.ButtonStyle.primary, custom_id="blox:builds:form")
    async def open_form(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_modal(BuildFormModal(self.cog))


class BuildFormModal(discord.ui.Modal, title="Build Request Form"):
    username = discord.ui.TextInput(label="Roblox Username", max_length=80)
    cash = discord.ui.TextInput(label="Bloxburg Cash on Hand", max_length=80)
    gamepasses = discord.ui.TextInput(label="Gamepasses Owned", max_length=200)
    budget = discord.ui.TextInput(label="Budget (USD or Robux)", max_length=80)
    description = discord.ui.TextInput(label="Describe Your Build", style=discord.TextStyle.paragraph, max_length=1000)
    payment_method = discord.ui.TextInput(label="Preferred Payment Method", max_length=80)

    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = teal_embed(title="Build Details")
        embed.add_field(name="Roblox Username", value=str(self.username))
        embed.add_field(name="Cash on Hand", value=str(self.cash))
        embed.add_field(name="Gamepasses Owned", value=str(self.gamepasses))
        embed.add_field(name="Budget", value=str(self.budget))
        embed.add_field(name="Preferred Payment", value=str(self.payment_method))
        embed.add_field(name="Description", value=str(self.description), inline=False)
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Build request submitted.", ephemeral=True)


class SupportFormView(discord.ui.View):
    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Open Support Form", style=discord.ButtonStyle.primary, custom_id="blox:support:form")
    async def open_form(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_modal(SupportFormModal(self.cog))


class SupportFormModal(discord.ui.Modal, title="Support Form"):
    subject = discord.ui.TextInput(label="Subject", max_length=100)
    details = discord.ui.TextInput(label="Details", style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, cog: TicketingCog) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = teal_embed(title="Support Details")
        embed.add_field(name="Subject", value=str(self.subject), inline=False)
        embed.add_field(name="Details", value=str(self.details), inline=False)
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Support request submitted.", ephemeral=True)


class PaymentMethodView(discord.ui.View):
    def __init__(self, cog: TicketingCog, state: TicketState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

        self.add_item(PaymentButton("Robux", "💎", PaymentMethod.ROBUX))
        self.add_item(PaymentButton("USD", "💵", PaymentMethod.USD))
        self.add_item(PaymentButton("Adopt Me", "🐾", PaymentMethod.ADOPT))
        self.add_item(PaymentButton("MM2", "🔪", PaymentMethod.MM2))

        claim_role_id = state.claim_role_id
        show_claim = False
        if state.payment_is_cash and claim_role_id:
            show_claim = True
        elif state.product_data:
            product_type = state.product_data.get("type", "normal")
            show_claim = product_type in {"normal", "role"} and (claim_role_id or state.product_data.get("seller_role_id"))
        if show_claim:
            button = ClaimButton()
            if state.claimed_by:
                button.disabled = True
                button.label = "Claimed"
                button.style = discord.ButtonStyle.success
            self.add_item(button)

    async def payment_callback(self, interaction: discord.Interaction, method: str) -> None:
        await self.cog.handle_payment_method(interaction, self.state, method)


class PaymentButton(discord.ui.Button):
    def __init__(self, label: str, emoji: str, method: str) -> None:
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.primary, custom_id=f"blox:pay:{method}")
        self.method = method

    async def callback(self, interaction: discord.Interaction) -> None:
        view: PaymentMethodView = self.view  # type: ignore[assignment]
        await view.payment_callback(interaction, self.method)


class ClaimButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Claim", style=discord.ButtonStyle.secondary, custom_id="blox:ticket:claim")

    async def callback(self, interaction: discord.Interaction) -> None:
        view: PaymentMethodView = self.view  # type: ignore[assignment]
        await view.cog.handle_claim(interaction)


class RobuxInstructionsView(discord.ui.View):
    def __init__(self, cog: TicketingCog, state: TicketState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

    @discord.ui.button(label="Submit Username", style=discord.ButtonStyle.primary, custom_id="blox:robux:submit")
    async def submit_username(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_modal(RobuxUsernameModal(self.cog, self.state))


class RobuxUsernameModal(discord.ui.Modal, title="Submit Roblox Username"):
    username = discord.ui.TextInput(label="Roblox Username", placeholder="RobloxUsername", custom_id="blox:modal:robux")

    def __init__(self, cog: TicketingCog, state: TicketState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.process_roblox_username(interaction, self.state, str(self.username))


class USDModal(discord.ui.Modal, title="USD Giftcard"):
    giftcard = discord.ui.TextInput(
        label="Giftcard Code",
        placeholder="XXXX-XXXX-XXXX",
        custom_id="blox:modal:usd",
    )

    def __init__(self, cog: TicketingCog, state: TicketState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        code = str(self.giftcard.value).strip()
        if not code:
            await interaction.response.send_message("Giftcard code cannot be empty.", ephemeral=True)
            return
        state = self.state
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
            return

        embed = teal_embed(title="USD Payment", description="Info has been DM’d to the Owner. Please wait for approval.")
        message = await interaction.channel.send(embed=embed)
        state.credential_messages[PaymentMethod.USD].append(message.id)

        await self.cog.queue_owner_review(
            state,
            method="USD",
            submitted_info=f"Giftcard Code: {code}",
        )
        await interaction.response.send_message("Giftcard submitted for approval.", ephemeral=True)


class AdoptMeView(discord.ui.View):
    def __init__(self, cog: TicketingCog, state: TicketState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

    @discord.ui.button(label="Submit Account", style=discord.ButtonStyle.primary, custom_id="blox:adopt:submit")
    async def submit(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_modal(AdoptCredentialsModal(self.cog, self.state))


class AdoptCredentialsModal(discord.ui.Modal, title="Adopt Me Credentials"):
    credentials = discord.ui.TextInput(
        label="Credentials (Username : Password)",
        placeholder="username : password",
        custom_id="blox:modal:adopt",
    )

    def __init__(self, cog: TicketingCog, state: TicketState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        creds = str(self.credentials.value).strip()
        if ":" not in creds:
            await interaction.response.send_message("Please use the format username : password.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
            return

        embed = teal_embed(title="Adopt Me Payment", description="Info has been DM’d to the Owner. Please wait for approval.")
        message = await interaction.channel.send(embed=embed)
        self.state.credential_messages[PaymentMethod.ADOPT].append(message.id)

        await self.cog.queue_owner_review(
            self.state,
            method="Adopt Me",
            submitted_info=f"Credentials: {creds}",
        )
        await interaction.response.send_message("Credentials submitted for approval.", ephemeral=True)


class MM2PaymentView(discord.ui.View):
    def __init__(self, cog: TicketingCog, state: TicketState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

    @discord.ui.button(label="Enough Value", style=discord.ButtonStyle.primary, custom_id="blox:mm2:enough")
    async def enough(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_modal(MM2Modal(self.cog, self.state))

    @discord.ui.button(label="Not Enough Value", style=discord.ButtonStyle.secondary, custom_id="blox:mm2:notenough")
    async def not_enough(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_message("Pick another payment method or close the ticket.", ephemeral=True)


class MM2Modal(discord.ui.Modal, title="MM2 Items"):
    items = discord.ui.TextInput(
        label="List your items and values",
        style=discord.TextStyle.paragraph,
        custom_id="blox:modal:mm2",
    )

    def __init__(self, cog: TicketingCog, state: TicketState) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Not a ticket channel.", ephemeral=True)
            return

        embed = teal_embed(title="MM2 Approver Panel")
        embed.add_field(name="User", value=interaction.user.mention)
        embed.add_field(name="Items", value=str(self.items), inline=False)
        price = self.cog.get_order_price(self.state)
        embed.add_field(name="Product/Tier", value=self.cog.get_order_label(self.state) or "Order")
        embed.add_field(name="Price", value=format_price(price))

        settings = await self.cog.get_settings()
        approver_role_id = settings.get("roles", {}).get("mm2_approver")
        if approver_role_id:
            role = interaction.guild.get_role(approver_role_id)
            if role:
                await interaction.channel.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True)

        view = MM2ApproverView(self.cog, self.state, str(self.items))
        message = await interaction.channel.send(embed=embed, view=view)
        self.state.credential_messages[PaymentMethod.MM2].append(message.id)

        await self.cog.queue_owner_review(
            self.state,
            method="MM2",
            submitted_info=str(self.items),
        )
        await interaction.response.send_message("MM2 submission sent for approval.", ephemeral=True)


class MM2ApproverView(discord.ui.View):
    def __init__(self, cog: TicketingCog, state: TicketState, submitted_items: str) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state
        self.submitted_items = submitted_items

    @discord.ui.button(label="Payment Approved", style=discord.ButtonStyle.primary, custom_id="blox:mm2:approve")
    async def approve(self, interaction: discord.Interaction, _) -> None:
        if not self.is_authorised(interaction.user):
            await interaction.response.send_message("You’re not authorised to approve.", ephemeral=True)
            return
        await interaction.response.send_message("Approved.", ephemeral=True)
        await self.cog.handle_auto_or_manual_fulfillment(interaction.channel, interaction.user, self.state, method=PaymentMethod.MM2, approved_by_owner=True)

    @discord.ui.button(label="Payment Failed", style=discord.ButtonStyle.danger, custom_id="blox:mm2:fail")
    async def fail(self, interaction: discord.Interaction, _) -> None:
        if not self.is_authorised(interaction.user):
            await interaction.response.send_message("You’re not authorised to mark failed.", ephemeral=True)
            return
        await self.cog.delete_last_credential_embed(interaction.channel, self.state, PaymentMethod.MM2)
        await interaction.channel.send("MM2 verification failed. Please try again or choose another method.")
        await interaction.response.send_message("Marked failed.", ephemeral=True)

    def is_authorised(self, user: discord.abc.User) -> bool:
        if user.id == OWNER_ID:
            return True
        if isinstance(user, discord.Member):
            return user.guild_permissions.manage_guild
        return False


# endregion


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketingCog(bot))

