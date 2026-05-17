# -*- coding: utf-8 -*-
import discord
from discord import app_commands
import os
from dotenv import load_dotenv
import aiohttp
from aiohttp import web
import asyncio
import re
import logging
import threading
import urllib.parse
import io
import random

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


# ====================== WEB SERVER ======================

async def handle(request):
    return web.Response(text="Sobai alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

def run_webserver():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_webserver())
    loop.run_forever()


# ====================== CHANNEL LANGUAGES ======================

SYSTEM_BASE = (
    "Ты Собай - дружелюбный и остроумный ассистент в Discord. "
    "Отвечай коротко, по делу, с лёгким юмором там, где уместно."
)

CHANNEL_LANG = {
    "ru-chat": "Отвечай ТОЛЬКО на русском языке.",
    "en-chat": "Reply ONLY in English.",
    "mx-chat": "Responde SOLO en espanol mexicano.",
    "global-chat": "Пиши на том же языке, на котором задан вопрос.",
}

def get_prompt(channel_name):
    for key, lang in CHANNEL_LANG.items():
        if key in channel_name:
            return SYSTEM_BASE + " " + lang
    return SYSTEM_BASE + " Пиши на том же языке, на котором задан вопрос."


# ====================== AI ======================

async def ask_ai(prompt, channel_name=""):
    system_prompt = get_prompt(channel_name)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": "Bearer " + os.getenv("GROQ_API_KEY")},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 700,
                },
            ) as resp:
                if resp.status != 200:
                    return "Groq API не отвечает, попробуй позже."
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except asyncio.TimeoutError:
        return "Timeout, попробуй снова."
    except Exception as e:
        log.exception("ask_ai error: %s", e)
        return "Я занят, попробуй позже."


# ====================== IMAGE ======================

async def generate_image(prompt):
    encoded = urllib.parse.quote(prompt)
    url = "https://image.pollinations.ai/prompt/" + encoded + "?width=1024&height=1024&nologo=true"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                return None
    except Exception as e:
        log.exception("image error: %s", e)
        return None


# ====================== MUSIC ======================

async def generate_music(prompt):
    token = os.getenv("HF_TOKEN")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    api_url = "https://api-inference.huggingface.co/models/facebook/musicgen-small"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            async with session.post(api_url, headers=headers, json={"inputs": prompt}) as resp:
                if resp.status == 200:
                    return await resp.read()
                body = await resp.text()
                log.error("MusicGen %s: %s", resp.status, body[:200])
                return None
    except Exception as e:
        log.exception("music error: %s", e)
        return None


# ====================== /sobai ======================

@tree.command(name="sobai", description="Поговорить с Собай")
@app_commands.describe(message="Что спросить")
@app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
async def sobai(interaction: discord.Interaction, message: str):
    try:
        await interaction.response.defer(thinking=True)
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("defer failed: %s", e)
        return
    channel_name = getattr(interaction.channel, "name", "")
    response = await ask_ai(message, channel_name)
    try:
        await interaction.followup.send(response[:2000])
    except discord.NotFound:
        try:
            await interaction.channel.send("**" + interaction.user.name + "**: " + response[:2000])
        except discord.HTTPException:
            pass
    except discord.HTTPException as e:
        log.error("followup error: %s", e)

@sobai.error
async def sobai_error(interaction, error):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            "Подожди " + str(round(error.retry_after, 1)) + " сек.", ephemeral=True
        )


# ====================== /sobima ======================

@tree.command(name="sobima", description="Сгенерировать изображение")
@app_commands.describe(prompt="Опиши что нарисовать")
@app_commands.checks.cooldown(1, 15, key=lambda i: i.user.id)
async def sobima(interaction: discord.Interaction, prompt: str):
    try:
        await interaction.response.defer(thinking=True)
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("defer failed: %s", e)
        return
    image_data = await generate_image(prompt)
    if image_data is None:
        try:
            await interaction.followup.send("Не удалось сгенерировать изображение.")
        except discord.HTTPException:
            pass
        return
    file = discord.File(fp=io.BytesIO(image_data), filename="image.png")
    try:
        await interaction.followup.send(content="🎨 **" + prompt + "**", file=file)
    except discord.NotFound:
        try:
            await interaction.channel.send(
                content="🎨 **" + prompt + "**",
                file=discord.File(fp=io.BytesIO(image_data), filename="image.png")
            )
        except discord.HTTPException:
            pass

@sobima.error
async def sobima_error(interaction, error):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            "Подожди " + str(round(error.retry_after, 1)) + " сек.", ephemeral=True
        )


# ====================== /music ======================

@tree.command(name="music", description="Сгенерировать музыку")
@app_commands.describe(prompt="Стиль музыки (lofi, epic, jazz...)")
@app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
async def music(interaction: discord.Interaction, prompt: str):
    try:
        await interaction.response.defer(thinking=True)
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("defer failed: %s", e)
        return
    try:
        await interaction.followup.send("🎵 Генерирую музыку, подожди...")
    except discord.HTTPException:
        pass
    audio_data = await generate_music(prompt)
    if audio_data is None:
        try:
            await interaction.channel.send("Не удалось сгенерировать музыку.")
        except discord.HTTPException:
            pass
        return
    file = discord.File(fp=io.BytesIO(audio_data), filename="music.wav")
    try:
        await interaction.channel.send(content="🎵 **" + prompt + "**", file=file)
    except discord.HTTPException as e:
        log.error("music send error: %s", e)

@music.error
async def music_error(interaction, error):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            "Подожди " + str(round(error.retry_after, 1)) + " сек.", ephemeral=True
        )


# ====================== /serverinfo ======================

@tree.command(name="serverinfo", description="Информация о сервере")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Только для серверов!", ephemeral=True)
        return
    embed = discord.Embed(title=guild.name, color=0x5865F2)
    embed.add_field(name="Владелец", value=str(guild.owner), inline=True)
    embed.add_field(name="Участников", value=str(guild.member_count), inline=True)
    embed.add_field(name="Создан", value=guild.created_at.strftime("%d.%m.%Y"), inline=True)
    embed.add_field(name="Каналов", value=str(len(guild.channels)), inline=True)
    embed.add_field(name="Ролей", value=str(len(guild.roles)), inline=True)
    embed.add_field(name="Эмодзи", value=str(len(guild.emojis)), inline=True)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    await interaction.response.send_message(embed=embed)


# ====================== /userinfo ======================

@tree.command(name="userinfo", description="Информация о пользователе")
@app_commands.describe(user="Пользователь")
async def userinfo(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    embed = discord.Embed(title=str(user), color=user.color)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="ID", value=str(user.id), inline=True)
    embed.add_field(name="Создан", value=user.created_at.strftime("%d.%m.%Y"), inline=True)
    if hasattr(user, "joined_at") and user.joined_at:
        embed.add_field(name="Зашёл", value=user.joined_at.strftime("%d.%m.%Y"), inline=True)
    embed.add_field(name="Ролей", value=str(len(user.roles) - 1), inline=True)
    embed.add_field(name="Бот", value="Да" if user.bot else "Нет", inline=True)
    await interaction.response.send_message(embed=embed)


# ====================== /avatar ======================

@tree.command(name="avatar", description="Аватарка пользователя")
@app_commands.describe(user="Пользователь")
async def avatar(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    embed = discord.Embed(title="Аватарка " + user.name, color=0x5865F2)
    embed.set_image(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


# ====================== /rps ======================

@tree.command(name="rps", description="Камень ножницы бумага")
@app_commands.describe(choice="Твой выбор")
@app_commands.choices(choice=[
    app_commands.Choice(name="Камень", value="rock"),
    app_commands.Choice(name="Ножницы", value="scissors"),
    app_commands.Choice(name="Бумага", value="paper"),
])
async def rps(interaction: discord.Interaction, choice: app_commands.Choice[str]):
    names = {"rock": "Камень", "scissors": "Ножницы", "paper": "Бумага"}
    emojis = {"rock": "🪨", "scissors": "✂️", "paper": "📄"}
    wins = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
    bot_choice = random.choice(["rock", "scissors", "paper"])
    user = choice.value
    if user == bot_choice:
        result = "Ничья!"
    elif wins[user] == bot_choice:
        result = "Ты победил!"
    else:
        result = "Я победил!"
    msg = (emojis[user] + " " + names[user] + " vs " +
           emojis[bot_choice] + " " + names[bot_choice] + " — " + result)
    await interaction.response.send_message(msg)


# ====================== /roll ======================

@tree.command(name="roll", description="Бросить кубик")
@app_commands.describe(sides="Количество граней (по умолчанию 6)")
async def roll(interaction: discord.Interaction, sides: int = 6):
    if sides < 2:
        await interaction.response.send_message("Минимум 2 грани!", ephemeral=True)
        return
    result = random.randint(1, sides)
    await interaction.response.send_message(
        "🎲 **" + interaction.user.name + "** бросил d" + str(sides) + " — **" + str(result) + "**!"
    )


# ====================== /8ball ======================

ANSWERS = [
    "Определённо да!", "Без сомнений!", "Скорее всего да.", "Похоже на то.",
    "Спроси позже...", "Лучше не знать.", "Сложно сказать.",
    "Не рассчитывай на это.", "Мой ответ — нет.", "Определённо нет!",
]

@tree.command(name="8ball", description="Магический шар")
@app_commands.describe(question="Задай вопрос")
async def eight_ball(interaction: discord.Interaction, question: str):
    answer = random.choice(ANSWERS)
    await interaction.response.send_message("🎱 **" + question + "**\n" + answer)


# ====================== ON MESSAGE ======================

@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    mentioned = client.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)
    if not (mentioned or is_dm):
        return
    clean_text = re.sub(rf"<@!?{client.user.id}>", "", message.content).strip()
    if not clean_text:
        await message.reply("Да? Чем помочь?")
        return
    channel_name = getattr(message.channel, "name", "")
    async with message.channel.typing():
        response = await ask_ai(clean_text, channel_name)
        try:
            await message.reply(response[:2000])
        except discord.HTTPException as e:
            log.error("reply error: %s", e)


# ====================== ON READY ======================

@client.event
async def on_ready():
    await tree.sync()
    log.info("Bot %s is ready!", client.user)


threading.Thread(target=run_webserver, daemon=True).start()
client.run(os.getenv("DISCORD_TOKEN"))
