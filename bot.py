import discord, asyncio, json, os, random
from discord.ext import commands, tasks
from config import BOT_TOKEN, STATUS_ROTATION_INTERVAL

intents = discord.Intents.default()
intents.message_content = True

# ✅ Required even if unused
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Slash command sync failed: {e}")
    rotate_status.start()

@tasks.loop(seconds=STATUS_ROTATION_INTERVAL)
async def rotate_status():
    try:
        with open("data/status_lines.json", encoding="utf-8") as f:
            statuses = json.load(f)
        if statuses:
            status = random.choice(statuses)
            await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"❌ Status rotation error: {e}")

async def load_cogs():
    cogs = ["wallet", "games", "trivia", "journal", "mood", "admin", "talk"]
    for cog in cogs:
        try:
            await bot.load_extension(f"cogs.{cog}")
            print(f"✅ Loaded cog: {cog}")
        except Exception as e:
            print(f"❌ Failed to load cog {cog}: {e}")

async def main():
    async with bot:
        await load_cogs()
        await bot.start(BOT_TOKEN)

asyncio.run(main())