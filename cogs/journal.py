import discord, json, random
from discord.ext import commands
from discord import app_commands
from config import OWNER_ID

JOURNAL_PATH = "data/journal_entries.json"

class Journal(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="leyla_journal", description="Read a random entry from Leyla's journal")
    async def leyla_journal(self, interaction: discord.Interaction):
        try:
            with open(JOURNAL_PATH) as f:
                entries = json.load(f)
        except Exception:
            return await interaction.response.send_message("❌ Journal file missing or broken.", ephemeral=True)

        if not entries:
            return await interaction.response.send_message("📭 Leyla's journal is empty.", ephemeral=True)

        entry = random.choice(entries)
        embed = discord.Embed(
            title="📓 Leyla's Journal",
            description=entry,
            color=0xf4c430
        )
        embed.set_footer(text="LeylaBot • Construction Chronicles")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leyla_journal_add", description="Add a new entry to Leyla's journal")
    @app_commands.describe(entry="The journal entry to add")
    async def leyla_journal_add(self, interaction: discord.Interaction, entry: str):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("❌ Only Leyla's foreman can add entries.", ephemeral=True)

        try:
            with open(JOURNAL_PATH) as f:
                entries = json.load(f)
        except Exception:
            entries = []

        entries.append(entry)
        with open(JOURNAL_PATH, "w") as f:
            json.dump(entries, f, indent=2)

        await interaction.response.send_message("✅ Entry added to Leyla's journal!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Journal(bot))