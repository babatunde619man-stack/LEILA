import discord, datetime
from discord.ext import commands
from discord import app_commands
from config import OWNER_ID

class MoodEngine:
    def get_mood():
        now = datetime.datetime.now().hour
        if 5 <= now < 12:
            return "Hammer Happy", "🌞 Morning energy! Time to build!"
        elif 12 <= now < 18:
            return "Brick Focused", "🔨 Afternoon grind — bricks don't lay themselves!"
        elif 18 <= now < 22:
            return "Dusty Chill", "🌇 Evening vibes — sweeping the jobsite."
        else:
            return "Scaffold Sleepy", "🌙 Night shift mode — quiet and cozy."

class Mood(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.current_mood, self.reason = MoodEngine.get_mood()
        self.vibe = "cheerful"

    @app_commands.command(name="leyla_mood", description="Check Leyla's current mood")
    async def leyla_mood(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"🧱 Leyla's Mood: {self.current_mood}",
            description=self.reason,
            color=0xf4c430 if self.vibe == "cheerful" else 0x888888
        )
        embed.set_footer(text=f"Vibe: {self.vibe}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leyla_mood_set", description="Manually set Leyla's mood")
    @app_commands.describe(mood="New mood name", reason="Why she's feeling that way")
    async def leyla_mood_set(self, interaction: discord.Interaction, mood: str, reason: str):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("❌ Only Leyla's foreman can set her mood.", ephemeral=True)

        self.current_mood = mood
        self.reason = reason
        await interaction.response.send_message(f"✅ Mood set to **{mood}** — {reason}", ephemeral=True)

    @app_commands.command(name="leyla_vibe", description="Change Leyla's personality style")
    @app_commands.describe(vibe="Choose: cheerful, chill, or sassy")
    async def leyla_vibe(self, interaction: discord.Interaction, vibe: str):
        if vibe.lower() not in ["cheerful", "chill", "sassy"]:
            return await interaction.response.send_message("❌ Invalid vibe. Choose: cheerful, chill, or sassy.", ephemeral=True)

        self.vibe = vibe.lower()
        await interaction.response.send_message(f"✅ Leyla's vibe is now **{self.vibe}**", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Mood(bot))