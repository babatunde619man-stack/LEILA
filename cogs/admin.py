from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.constants import GUILD_ID
from utils.data_manager import DataManager
from utils.embeds import teal_embed


DATA_DIR = Path("data/uploads")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def ensure_manage_guild(interaction: discord.Interaction) -> bool:
    return bool(interaction.user.guild_permissions.manage_guild)


def slugify(name: str) -> str:
    import re

    value = name.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "item"


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.data: DataManager = getattr(bot, "data_manager")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not ensure_manage_guild(interaction):
            await interaction.response.send_message("You need the Manage Server permission to use this command.", ephemeral=True)
            return False
        return True

    # region role setters
    @app_commands.command(name="setbuyers", description="Set the role granted when an order completes.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def setbuyers(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self.update_role_setting(interaction, "buyer", role)

    @app_commands.command(name="setmm2role", description="Set the role added to tickets when MM2 verification starts.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def setmm2role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self.update_role_setting(interaction, "mm2_approver", role)

    @app_commands.command(name="setsupportrole", description="Set the support role automatically added to tickets.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def setsupportrole(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self.update_role_setting(interaction, "support", role)

    @app_commands.command(name="setcashseller", description="Set the Cash Seller role for cash tickets.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def setcashseller(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self.update_role_setting(interaction, "cashseller", role)

    async def update_role_setting(self, interaction: discord.Interaction, key: str, role: discord.Role) -> None:
        async def mutate(settings: Dict[str, Any]) -> None:
            settings.setdefault("roles", {})[key] = role.id

        await self.data.update("settings", mutate)
        await interaction.response.send_message(f"Updated `{key}` role to {role.mention}.", ephemeral=True)

    # endregion

    # region panel visibility
    @app_commands.command(name="status", description="Toggle availability of Cash or Builds options on the panel.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def status(self, interaction: discord.Interaction, option: str, value: str) -> None:
        option = option.lower()
        value = value.lower()
        if option not in {"cash", "builds"} or value not in {"available", "unavailable"}:
            await interaction.response.send_message("Invalid option or value.", ephemeral=True)
            return

        available = value == "available"

        async def mutate(settings: Dict[str, Any]) -> None:
            settings.setdefault("panel", {}).setdefault("options", {})[f"{option}_available"] = available

        await self.data.update("settings", mutate)
        await interaction.response.send_message(f"Set `{option}` availability to **{value}**.", ephemeral=True)

    @status.autocomplete("option")
    async def status_option_autocomplete(self, _: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        options = ["cash", "builds"]
        return [app_commands.Choice(name=opt.title(), value=opt) for opt in options if opt.startswith(current.lower())]

    @status.autocomplete("value")
    async def status_value_autocomplete(self, _: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        options = ["available", "unavailable"]
        return [app_commands.Choice(name=opt.title(), value=opt) for opt in options if opt.startswith(current.lower())]

    # endregion

    # region product commands
    @app_commands.command(name="addproduct", description="Add a product to the purchase catalog.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Normal", value="normal"),
            app_commands.Choice(name="File", value="file"),
            app_commands.Choice(name="Key", value="key"),
            app_commands.Choice(name="Role", value="role"),
        ]
    )
    async def addproduct(
        self,
        interaction: discord.Interaction,
        name: str,
        price: float,
        type: app_commands.Choice[str],
        emoji: str,
        description: str,
        image_url: Optional[str] = None,
        file: Optional[discord.Attachment] = None,
        keys_file: Optional[discord.Attachment] = None,
        role: Optional[discord.Role] = None,
        seller_role: Optional[discord.Role] = None,
        giftcard_url: Optional[str] = None,
        gamepass_url: Optional[str] = None,
        gamepass_id: Optional[int] = None,
        mm2_value: Optional[int] = None,
        adoptme_value: Optional[float] = None,
    ) -> None:
        product_type = type.value
        if product_type == "file" and not file:
            await interaction.response.send_message("File products require an attachment.", ephemeral=True)
            return
        if product_type == "key" and not keys_file:
            await interaction.response.send_message("Key products require a keys file.", ephemeral=True)
            return
        if product_type == "role" and not role:
            await interaction.response.send_message("Role products require a role.", ephemeral=True)
            return

        storage_dir = DATA_DIR / "products" / slugify(name)
        storage_dir.mkdir(parents=True, exist_ok=True)

        file_path = None
        key_path = None
        if file:
            file_path = await self.save_attachment(file, storage_dir / file.filename)
        if keys_file:
            key_path = await self.save_attachment(keys_file, storage_dir / keys_file.filename)

        async def mutate(products: Dict[str, Any]) -> None:
            products[name] = {
                "type": product_type,
                "price_usd": float(price),
                "emoji": emoji,
                "description": description,
                "image_url": image_url or "",
                "file_path": file_path or "",
                "key_path": key_path or "",
                "role_id": role.id if role else 0,
                "seller_role_id": seller_role.id if seller_role else 0,
                "giftcard_url": giftcard_url or "",
                "gamepass_url": gamepass_url or "",
                "gamepass_id": int(gamepass_id or 0),
                "mm2_value": int(mm2_value or 0),
                "adoptme_value": float(adoptme_value or 0),
            }

        await self.data.update("products", mutate)
        await interaction.response.send_message(f"Added product **{name}**.", ephemeral=True)

    @app_commands.command(name="editproduct", description="Edit an existing product.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.choices(
        new_type=[
            app_commands.Choice(name="Normal", value="normal"),
            app_commands.Choice(name="File", value="file"),
            app_commands.Choice(name="Key", value="key"),
            app_commands.Choice(name="Role", value="role"),
        ]
    )
    async def editproduct(
        self,
        interaction: discord.Interaction,
        product: str,
        new_name: Optional[str] = None,
        new_price: Optional[float] = None,
        new_type: Optional[app_commands.Choice[str]] = None,
        new_emoji: Optional[str] = None,
        new_description: Optional[str] = None,
        new_image_url: Optional[str] = None,
        new_file: Optional[discord.Attachment] = None,
        new_keys_file: Optional[discord.Attachment] = None,
        new_role: Optional[discord.Role] = None,
        new_seller_role: Optional[discord.Role] = None,
        new_giftcard_url: Optional[str] = None,
        new_gamepass_url: Optional[str] = None,
        new_gamepass_id: Optional[int] = None,
        new_mm2_value: Optional[int] = None,
        new_adoptme_value: Optional[float] = None,
    ) -> None:
        products = await self.data.load("products")
        data = products.get(product)
        if data is None:
            await interaction.response.send_message("Product not found.", ephemeral=True)
            return

        storage_dir = DATA_DIR / "products" / slugify(new_name or product)
        storage_dir.mkdir(parents=True, exist_ok=True)

        if new_file:
            data["file_path"] = await self.save_attachment(new_file, storage_dir / new_file.filename)
        if new_keys_file:
            data["key_path"] = await self.save_attachment(new_keys_file, storage_dir / new_keys_file.filename)

        if new_name:
            products[new_name] = products.pop(product)
            product = new_name
            data = products[product]

        if new_price is not None:
            data["price_usd"] = float(new_price)
        if new_type is not None:
            data["type"] = new_type.value
        if new_emoji is not None:
            data["emoji"] = new_emoji
        if new_description is not None:
            data["description"] = new_description
        if new_image_url is not None:
            data["image_url"] = new_image_url
        if new_role is not None:
            data["role_id"] = new_role.id
        if new_seller_role is not None:
            data["seller_role_id"] = new_seller_role.id
        if new_giftcard_url is not None:
            data["giftcard_url"] = new_giftcard_url
        if new_gamepass_url is not None:
            data["gamepass_url"] = new_gamepass_url
        if new_gamepass_id is not None:
            data["gamepass_id"] = int(new_gamepass_id)
        if new_mm2_value is not None:
            data["mm2_value"] = int(new_mm2_value)
        if new_adoptme_value is not None:
            data["adoptme_value"] = float(new_adoptme_value)

        await self.data.save("products", products)
        await interaction.response.send_message(f"Updated product **{product}**.", ephemeral=True)

    @app_commands.command(name="removeproduct", description="Remove a product from the catalog.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def removeproduct(self, interaction: discord.Interaction, product: str) -> None:
        async def mutate(products: Dict[str, Any]) -> None:
            products.pop(product, None)

        await self.data.update("products", mutate)
        await interaction.response.send_message(f"Removed product **{product}**.", ephemeral=True)

    @app_commands.command(name="listproducts", description="List available products.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def listproducts(self, interaction: discord.Interaction) -> None:
        products = await self.data.load("products")
        if not products:
            await interaction.response.send_message("No products configured.", ephemeral=True)
            return
        embed = teal_embed(title="Products")
        for name, info in products.items():
            details = [
                f"Price: ${float(info.get('price_usd', 0.0)):.2f}",
                f"Type: {info.get('type', 'normal')}",
            ]
            if info.get("seller_role_id"):
                details.append(f"Seller Role: <@&{info['seller_role_id']}>")
            if info.get("giftcard_url"):
                details.append("Giftcard: ✅")
            if info.get("gamepass_url"):
                details.append("Gamepass: ✅")
            if info.get("type") == "key":
                details.append(f"Keys: {self.count_keys(info.get('key_path'))}")
            embed.add_field(name=f"{info.get('emoji', '')} {name}".strip(), value="\n".join(details), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="showkeystock", description="Show remaining key stock for a product.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def showkeystock(self, interaction: discord.Interaction, product: str) -> None:
        products = await self.data.load("products")
        info = products.get(product)
        if not info or info.get("type") != "key":
            await interaction.response.send_message("Product not found or not a key product.", ephemeral=True)
            return
        count = self.count_keys(info.get("key_path"))
        await interaction.response.send_message(f"`{product}` has **{count}** keys remaining.", ephemeral=True)

    @app_commands.command(name="addkeystock", description="Append key stock to a key-based product.")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def addkeystock(self, interaction: discord.Interaction, product: str, keys_file: discord.Attachment) -> None:
        products = await self.data.load("products")
        info = products.get(product)
        if not info or info.get("type") != "key":
            await interaction.response.send_message("Product not found or not a key product.", ephemeral=True)
            return
        key_path = info.get("key_path")
        if not key_path:
            storage_dir = DATA_DIR / "products" / slugify(product)
            storage_dir.mkdir(parents=True, exist_ok=True)
            key_path = storage_dir / keys_file.filename
            info["key_path"] = str(key_path)
        else:
            key_path = Path(key_path)
        existing = key_path.read_text(encoding="utf-8") if key_path.exists() else ""
        new_content = await keys_file.read()
        combined = existing.strip()
        if combined:
            combined += "\n"
        combined += new_content.decode("utf-8").strip()
        key_path.write_text(combined + "\n", encoding="utf-8")
        await self.data.save("products", products)
        await interaction.response.send_message("Keys appended.", ephemeral=True)

    # endregion

    # region cash commands
    cash = app_commands.Group(name="cash", description="Manage cash tiers.", guild_ids=[GUILD_ID])

    @cash.command(name="add", description="Add a cash tier.")
    async def cash_add(
        self,
        interaction: discord.Interaction,
        label: str,
        price: float,
        emoji: str,
        description: str,
        giftcard_url: Optional[str] = None,
        gamepass_url: Optional[str] = None,
        gamepass_id: Optional[int] = None,
        mm2_value: Optional[int] = None,
        adoptme_value: Optional[float] = None,
    ) -> None:
        async def mutate(payload: Dict[str, Any]) -> None:
            tier = {
                "label": label,
                "price_usd": float(price),
                "emoji": emoji,
                "description": description,
                "giftcard_url": giftcard_url or "",
                "gamepass_url": gamepass_url or "",
                "gamepass_id": int(gamepass_id or 0),
                "mm2_value": int(mm2_value or 0),
                "adoptme_value": float(adoptme_value or 0),
            }
            payload.setdefault("tiers", []).append(tier)

        await self.data.update("cash", mutate)
        await interaction.response.send_message(f"Added cash tier **{label}**.", ephemeral=True)

    @cash.command(name="edit", description="Edit an existing cash tier.")
    async def cash_edit(
        self,
        interaction: discord.Interaction,
        tier: str,
        new_label: Optional[str] = None,
        new_price: Optional[float] = None,
        new_emoji: Optional[str] = None,
        new_description: Optional[str] = None,
        new_giftcard_url: Optional[str] = None,
        new_gamepass_url: Optional[str] = None,
        new_gamepass_id: Optional[int] = None,
        new_mm2_value: Optional[int] = None,
        new_adoptme_value: Optional[float] = None,
    ) -> None:
        cash = await self.data.load("cash")
        tiers = cash.get("tiers", [])
        for entry in tiers:
            if entry.get("label") == tier:
                if new_label is not None:
                    entry["label"] = new_label
                if new_price is not None:
                    entry["price_usd"] = float(new_price)
                if new_emoji is not None:
                    entry["emoji"] = new_emoji
                if new_description is not None:
                    entry["description"] = new_description
                if new_giftcard_url is not None:
                    entry["giftcard_url"] = new_giftcard_url
                if new_gamepass_url is not None:
                    entry["gamepass_url"] = new_gamepass_url
                if new_gamepass_id is not None:
                    entry["gamepass_id"] = int(new_gamepass_id)
                if new_mm2_value is not None:
                    entry["mm2_value"] = int(new_mm2_value)
                if new_adoptme_value is not None:
                    entry["adoptme_value"] = float(new_adoptme_value)
                await self.data.save("cash", cash)
                await interaction.response.send_message(f"Updated tier **{entry['label']}**.", ephemeral=True)
                return
        await interaction.response.send_message("Tier not found.", ephemeral=True)

    @cash.command(name="remove", description="Remove a cash tier.")
    async def cash_remove(self, interaction: discord.Interaction, tier: str) -> None:
        async def mutate(payload: Dict[str, Any]) -> None:
            payload["tiers"] = [t for t in payload.get("tiers", []) if t.get("label") != tier]

        await self.data.update("cash", mutate)
        await interaction.response.send_message(f"Removed tier **{tier}**.", ephemeral=True)

    @cash.command(name="list", description="List all cash tiers.")
    async def cash_list(self, interaction: discord.Interaction) -> None:
        cash = await self.data.load("cash")
        tiers = cash.get("tiers", [])
        if not tiers:
            await interaction.response.send_message("No cash tiers configured.", ephemeral=True)
            return
        embed = teal_embed(title="Cash Tiers")
        for tier in tiers:
            lines = [
                f"Price: ${float(tier.get('price_usd', 0.0)):.2f}",
                f"Emoji: {tier.get('emoji', '')}",
            ]
            if tier.get("giftcard_url"):
                lines.append("Giftcard: ✅")
            if tier.get("gamepass_url"):
                lines.append("Gamepass: ✅")
            if tier.get("mm2_value"):
                lines.append(f"MM2: {tier['mm2_value']}")
            if tier.get("adoptme_value"):
                lines.append(f"AdoptMe: {tier['adoptme_value']}")
            embed.add_field(name=f"{tier.get('emoji', '')} {tier.get('label')}".strip(), value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # endregion

    # region helpers
    async def save_attachment(self, attachment: discord.Attachment, destination: Path) -> str:
        data = await attachment.read()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return str(destination)

    def count_keys(self, path: Optional[str]) -> int:
        if not path:
            return 0
        file_path = Path(path)
        if not file_path.exists():
            return 0
        return sum(1 for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip())

    # autocomplete helpers
    @addproduct.autocomplete("type")
    @editproduct.autocomplete("new_type")
    async def product_type_autocomplete(self, _: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        choices = ["normal", "file", "key", "role"]
        return [app_commands.Choice(name=choice.title(), value=choice) for choice in choices if choice.startswith(current.lower())]

    @editproduct.autocomplete("product")
    @removeproduct.autocomplete("product")
    @showkeystock.autocomplete("product")
    @addkeystock.autocomplete("product")
    async def product_autocomplete(self, _: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        products = await self.data.load("products")
        return [
            app_commands.Choice(name=name, value=name)
            for name in products.keys()
            if name.lower().startswith(current.lower())
        ][:25]

    @cash_edit.autocomplete("tier")
    @cash_remove.autocomplete("tier")
    async def cash_tier_autocomplete(self, _: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        cash = await self.data.load("cash")
        return [
            app_commands.Choice(name=tier.get("label", "Unnamed"), value=tier.get("label", ""))
            for tier in cash.get("tiers", [])
            if tier.get("label", "").lower().startswith(current.lower())
        ][:25]

    # endregion


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))