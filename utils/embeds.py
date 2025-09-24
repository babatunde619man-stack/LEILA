import discord

def sphere_embed(title, description):
    embed = discord.Embed(title=title, description=description, color=0xe98f00)
    embed.set_footer(text="CraftBurg Systems™", icon_url="attachment://logo.png")
    return embed