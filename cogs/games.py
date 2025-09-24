import discord, random
from discord.ext import commands
from discord import app_commands

# ---------- TicTacToe ----------
class TicTacToeButton(discord.ui.Button):
    def __init__(self, x, y):
        super().__init__(style=discord.ButtonStyle.secondary, label=" ", row=y)
        self.x = x
        self.y = y

    async def callback(self, interaction: discord.Interaction):
        view: TicTacToeView = self.view
        if not view.message:
            view.message = interaction.message

        if interaction.user != view.current_player:
            return await interaction.response.send_message("❌ Not your turn!", ephemeral=True)

        if view.board[self.y][self.x] != " ":
            return await interaction.response.send_message("❌ That spot's taken!", ephemeral=True)

        mark = "X" if view.current_player == view.player1 else "O"
        self.label = mark
        self.style = discord.ButtonStyle.success if mark == "X" else discord.ButtonStyle.danger
        self.disabled = True
        view.board[self.y][self.x] = mark

        winner = view.check_winner()
        if winner:
            for child in view.children:
                child.disabled = True
            await view.message.edit(content=f"🎉 {interaction.user.mention} wins!", view=view)
            return

        if view.is_full():
            for child in view.children:
                child.disabled = True
            await view.message.edit(content="🤝 It's a draw!", view=view)
            return

        view.current_player = view.player2 if view.current_player == view.player1 else view.player1
        await view.message.edit(content=f"It's {view.current_player.mention}'s turn", view=view)

class TicTacToeView(discord.ui.View):
    def __init__(self, player1, player2):
        super().__init__(timeout=300)
        self.player1 = player1
        self.player2 = player2
        self.current_player = player1
        self.board = [[" "]*3 for _ in range(3)]
        self.message = None
        for y in range(3):
            for x in range(3):
                self.add_item(TicTacToeButton(x, y))

    def check_winner(self):
        lines = self.board + list(zip(*self.board)) + [
            [self.board[i][i] for i in range(3)],
            [self.board[i][2-i] for i in range(3)]
        ]
        for line in lines:
            if line[0] != " " and all(cell == line[0] for cell in line):
                return line[0]
        return None

    def is_full(self):
        return all(cell != " " for row in self.board for cell in row)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(content="⏱️ TicTacToe timed out.", view=self)

# ---------- RPS ----------
class RPSView(discord.ui.View):
    def __init__(self, challenger, opponent, bot):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.opponent = opponent
        self.bot = bot
        self.moves = {}
        self.message = None

    async def handle_move(self, interaction: discord.Interaction, move: str):
        user = interaction.user
        if user not in [self.challenger, self.opponent]:
            return await interaction.response.send_message("❌ You're not in this match.", ephemeral=True)

        if user.id in self.moves:
            return await interaction.response.send_message("❌ You've already chosen.", ephemeral=True)

        self.moves[user.id] = move
        await interaction.response.send_message(f"✅ You chose **{move}**", ephemeral=True)

        if len(self.moves) == 2 or self.opponent == self.bot.user:
            await self.resolve_game()

    async def resolve_game(self):
        p1_move = self.moves[self.challenger.id]
        p2_move = self.moves.get(self.opponent.id) or random.choice(["Rock", "Paper", "Scissors"])

        result = self.get_result(p1_move, p2_move)
        if result == 0:
            msg = f"🤝 It's a draw! Both chose **{p1_move}**"
        elif result == 1:
            msg = f"🎉 {self.challenger.mention} wins! {p1_move} beats {p2_move}"
        else:
            msg = f"🎉 {self.opponent.mention} wins! {p2_move} beats {p1_move}"

        for child in self.children:
            child.disabled = True
        await self.message.edit(content=msg, view=self)

    def get_result(self, p1, p2):
        beats = {"Rock": "Scissors", "Scissors": "Paper", "Paper": "Rock"}
        if p1 == p2:
            return 0
        return 1 if beats[p1] == p2 else 2

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(content="⏱️ RPS match timed out.", view=self)

class RPSButton(discord.ui.Button):
    def __init__(self, label):
        super().__init__(label=label, style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle_move(interaction, self.label)

# ---------- Cog ----------
class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="tictactoe", description="Play TicTacToe against someone or Leyla")
    @app_commands.describe(opponent="Who to play against (leave blank to play vs Leyla)")
    async def tictactoe(self, interaction: discord.Interaction, opponent: discord.User = None):
        await interaction.response.defer()
        player1 = interaction.user
        player2 = opponent or self.bot.user
        view = TicTacToeView(player1, player2)
        msg = await interaction.followup.send(f"TicTacToe: {player1.mention} vs {player2.mention}", view=view)
        view.message = msg

    @app_commands.command(name="rps", description="Play Rock Paper Scissors against someone or Leyla")
    @app_commands.describe(opponent="Who to play against (leave blank to play vs Leyla)")
    async def rps(self, interaction: discord.Interaction, opponent: discord.User = None):
        await interaction.response.defer()
        challenger = interaction.user
        opponent = opponent or self.bot.user
        view = RPSView(challenger, opponent, self.bot)
        for move in ["Rock", "Paper", "Scissors"]:
            view.add_item(RPSButton(move))
        msg = await interaction.followup.send(f"RPS: {challenger.mention} vs {opponent.mention}", view=view)
        view.message = msg

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        try:
            await interaction.response.send_message(f"⚠️ Command error: {error}", ephemeral=True)
        except:
            pass

# ---------- Setup ----------
async def setup(bot):
    await bot.add_cog(Games(bot))