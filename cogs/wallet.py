import discord, json, os, datetime
from discord.ext import commands
from discord import app_commands
from config import MODLOG_CHANNEL_ID

ADMIN_ROLE_ID = 1418856407783968858  # 🔒 Replace with your actual Admin role ID

WALLET_PATH = "data/wallet.json"
TX_PATH = "data/transactions.json"
os.makedirs("data", exist_ok=True)

# Ensure wallet.json is a dict
if not os.path.exists(WALLET_PATH):
    with open(WALLET_PATH, "w") as f:
        json.dump({}, f)
else:
    with open(WALLET_PATH, "r") as f:
        try:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError
        except:
            with open(WALLET_PATH, "w") as f:
                json.dump({}, f)

# Ensure transactions.json is a list
if not os.path.exists(TX_PATH):
    with open(TX_PATH, "w") as f:
        json.dump([], f)
else:
    with open(TX_PATH, "r") as f:
        try:
            data = json.load(f)
            if not isinstance(data, list):
                raise ValueError
        except:
            with open(TX_PATH, "w") as f:
                json.dump([], f)

def load_wallet():
    with open(WALLET_PATH, "r") as f:
        return json.load(f)

def save_wallet(data):
    with open(WALLET_PATH, "w") as f:
        json.dump(data, f, indent=2)

def load_tx():
    with open(TX_PATH, "r") as f:
        return json.load(f)

def save_tx(data):
    with open(TX_PATH, "w") as f:
        json.dump(data, f, indent=2)

class Wallet(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="balance", description="Check your wallet balance")
    async def balance(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        wallet = load_wallet()
        bal = wallet.get(uid, 0)
        await interaction.response.send_message(f"💰 You have **${bal}** in your wallet.", ephemeral=True)

    @app_commands.command(name="send", description="Send currency to another user")
    @app_commands.describe(user="Who to send money to", amount="Amount to send", reason="Optional reason")
    async def send(self, interaction: discord.Interaction, user: discord.User, amount: int, reason: str = None):
        if user.id == interaction.user.id:
            return await interaction.response.send_message("❌ You can't send money to yourself.", ephemeral=True)
        if amount <= 0:
            return await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)

        wallet = load_wallet()
        sender_id = str(interaction.user.id)
        receiver_id = str(user.id)
        sender_bal = wallet.get(sender_id, 0)

        if sender_bal < amount:
            return await interaction.response.send_message("❌ You don't have enough funds.", ephemeral=True)

        wallet[sender_id] = sender_bal - amount
        wallet[receiver_id] = wallet.get(receiver_id, 0) + amount
        save_wallet(wallet)

        tx = load_tx()
        tx.append({
            "from": sender_id,
            "to": receiver_id,
            "amount": amount,
            "reason": reason,
            "time": datetime.datetime.now().isoformat()
        })
        save_tx(tx)

        await interaction.response.send_message(f"✅ Sent **${amount}** to {user.mention}" + (f" for *{reason}*" if reason else ""), ephemeral=True)

        log_channel = interaction.guild.get_channel(MODLOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="💸 Transaction Logged",
                description=f"{interaction.user.mention} → {user.mention}",
                color=0xe98f00
            )
            embed.add_field(name="Amount", value=f"${amount}", inline=True)
            embed.add_field(name="Reason", value=reason or "—", inline=True)
            embed.set_footer(text="LeylaBot • Wallet System")
            await log_channel.send(embed=embed)

    @app_commands.command(name="addmoney", description="Add money to a user's wallet (Admin only)")
    @app_commands.describe(user="User to receive money", amount="Amount to add", reason="Optional reason")
    async def addmoney(self, interaction: discord.Interaction, user: discord.User, amount: int, reason: str = None):
        try:
            admin_role = discord.utils.get(interaction.user.roles, id=ADMIN_ROLE_ID)
            if not admin_role and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("Access Denied.", ephemeral=True)
                return

            if amount <= 0:
                await interaction.response.send_message("Amount must be positive.", ephemeral=True)
                return

            wallet = load_wallet()
            uid = str(user.id)
            wallet[uid] = wallet.get(uid, 0) + amount
            save_wallet(wallet)

            tx = load_tx()
            tx.append({
                "from": "ADMIN",
                "to": uid,
                "amount": amount,
                "reason": reason,
                "time": datetime.datetime.now().isoformat()
            })
            save_tx(tx)

            await interaction.response.send_message(f"✅ Added **${amount}** to {user.mention}'s wallet.", ephemeral=True)

            log_channel = interaction.guild.get_channel(MODLOG_CHANNEL_ID)
            if log_channel:
                embed = discord.Embed(
                    title="💰 Admin Wallet Injection",
                    description=f"{interaction.user.mention} → {user.mention}",
                    color=0x00b86b
                )
                embed.add_field(name="Amount", value=f"${amount}", inline=True)
                embed.add_field(name="Reason", value=reason or "—", inline=True)
                embed.set_footer(text="LeylaBot • Wallet System")
                await log_channel.send(embed=embed)

        except Exception as e:
            error_msg = f"⚠️ Failed to add money: {e}"
            if interaction.response.is_done():
                await interaction.followup.send(error_msg, ephemeral=True)
            else:
                await interaction.response.send_message(error_msg, ephemeral=True)

    @app_commands.command(name="transactions", description="View your last 10 transactions")
    async def transactions(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        tx = load_tx()
        recent = [t for t in tx if t["from"] == uid or t["to"] == uid][-10:]

        if not recent:
            return await interaction.response.send_message("📭 No transactions found.", ephemeral=True)

        lines = []
        for t in reversed(recent):
            direction = "→" if t["from"] == uid else "←"
            other_id = t["to"] if t["from"] == uid else t["from"]
            if other_id == "ADMIN":
                other_name = "Admin"
            else:
                other = await self.bot.fetch_user(int(other_id))
                other_name = other.name
            lines.append(f"{direction} {other_name} — ${t['amount']} ({t['reason'] or '—'})")

        embed = discord.Embed(
            title="📋 Your Recent Transactions",
            description="\n".join(lines),
            color=0xe98f00
        )
        embed.set_footer(text="LeylaBot • Wallet System")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="leaderboard", description="View the top wallet balances")
    async def leaderboard(self, interaction: discord.Interaction):
        wallet = load_wallet()
        top = sorted(wallet.items(), key=lambda x: x[1], reverse=True)[:10]

        lines = []
        for i, (uid, bal) in enumerate(top, 1):
            user = await self.bot.fetch_user(int(uid))
            lines.append(f"**{i}.** {user.name} — ${bal}")

        embed = discord.Embed(
            title="🏆 Wallet Leaderboard",
            description="\n".join(lines),
            color=0xe98f00
        )
        embed.set_footer(text="LeylaBot • Wallet System")
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(Wallet(bot))