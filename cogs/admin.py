import discord, datetime, json, os
from discord.ext import commands
from discord import app_commands
from config import OWNER_ID, STATUS_ROTATION_INTERVAL

STATUS_PATH = "data/status_lines.json"

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = datetime.datetime.utcnow()

    @app_commands.command(name="leyla_say", description="Make Leyla say something")
    @app_commands.describe(message="What should Leyla say?")
    async def leyla_say(self, interaction: discord.Interaction, message: str):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        await interaction.response.send_message(message)

    @app_commands.command(name="leyla_silence", description="Stop Leyla's status rotation")
    async def leyla_silence(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        self.bot.change_presence(activity=None)
        await interaction.response.send_message("🔇 Leyla is now silent.", ephemeral=True)

    @app_commands.command(name="leyla_reset", description="Reset Leyla's status rotation")
    async def leyla_reset(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        await interaction.response.send_message("🔄 Status rotation will resume on next cycle.", ephemeral=True)

    @app_commands.command(name="leyla_status_add", description="Add a new status line to Leyla's rotation")
    @app_commands.describe(line="The status line to add")
    async def leyla_status_add(self, interaction: discord.Interaction, line: str):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        try:
            with open(STATUS_PATH) as f:
                statuses = json.load(f)
        except Exception:
            statuses = []

        statuses.append(line)
        with open(STATUS_PATH, "w") as f:
            json.dump(statuses, f, indent=2)

        await interaction.response.send_message("✅ Status line added!", ephemeral=True)

    @app_commands.command(name="leyla_stats", description="View Leyla's stats")
    async def leyla_stats(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📊 LeylaBot Stats",
            description="Here's what Leyla's been up to:",
            color=0x00b2ff
        )
        embed.add_field(name="Status Rotation", value=f"Every {STATUS_ROTATION_INTERVAL} seconds", inline=True)
        embed.add_field(name="Mood Engine", value="Time-based auto-adjust", inline=True)
        embed.add_field(name="Games", value="TicTacToe, RPS, Hangman", inline=True)
        embed.add_field(name="Wallet", value="Balance, Send, Leaderboard", inline=True)
        embed.set_footer(text="LeylaBot • Admin Panel")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leyla_uptime", description="Check how long Leyla's been online")
    async def leyla_uptime(self, interaction: discord.Interaction):
        now = datetime.datetime.utcnow()
        delta = now - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime = f"{hours}h {minutes}m {seconds}s"
        await interaction.response.send_message(f"⏱️ Leyla has been online for **{uptime}**", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Admin(bot))