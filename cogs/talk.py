import discord, io, json
from discord.ext import commands
from discord import app_commands

# Load config.json directly
with open("config.json", "r") as f:
    config = json.load(f)

LOG_CHANNEL_ID = int(config["channels"]["message_log"])
STAFF_ROLE_ID = int(config["roles"]["staff"])
OWNER_ROLE_ID = int(config["roles"]["owner"])

class Talk(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="talk", description="Send a raw message as the bot (Staff only)")
    @app_commands.describe(
        message="The message to send",
        user="Send as DM to this user",
        channel="Send to this channel",
        file="Attach a file or image"
    )
    async def talk(
        self,
        interaction: discord.Interaction,
        message: str,
        user: discord.User = None,
        channel: discord.TextChannel = None,
        file: discord.Attachment = None
    ):
        try:
            # ✅ Block if not in a server
            if interaction.guild is None or not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("Access Denied.", ephemeral=True)
                return

            # ✅ Role check
            if not any(role.id in [STAFF_ROLE_ID, OWNER_ROLE_ID] for role in interaction.user.roles):
                await interaction.response.send_message("Access Denied.", ephemeral=True)
                return

            target = user or channel or interaction.channel
            kwargs = {"content": message}

            if file:
                file_data = await file.read()
                discord_file = discord.File(fp=io.BytesIO(file_data), filename=file.filename)
                kwargs["file"] = discord_file

            await target.send(**kwargs)
            location = "DM" if isinstance(target, discord.User) else target.mention

            # ✅ Log the outgoing message
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="📨 Message Sent by Leyla",
                    description=message,
                    color=0x5865F2
                )
                embed.add_field(name="Target", value=location, inline=True)
                embed.add_field(name="Sent by", value=interaction.user.mention, inline=True)
                if file:
                    embed.set_footer(text=f"File: {file.filename}")
                embed.timestamp = discord.utils.utcnow()
                await log_channel.send(embed=embed)

            await interaction.response.send_message(f"✅ Message sent to {location}", ephemeral=True)

        except Exception as e:
            error_msg = f"⚠️ Failed to send message: {e}"
            if interaction.response.is_done():
                await interaction.followup.send(error_msg, ephemeral=True)
            else:
                await interaction.response.send_message(error_msg, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None and not message.author.bot:
            log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="📥 DM Received by Leyla",
                    description=message.content or "—",
                    color=0xED4245
                )
                embed.set_author(
                    name=f"{message.author.name} ({message.author.id})",
                    icon_url=message.author.display_avatar.url
                )
                embed.timestamp = discord.utils.utcnow()
                if message.attachments:
                    embed.set_footer(text=f"Attachment: {message.attachments[0].filename}")
                await log_channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Talk(bot))