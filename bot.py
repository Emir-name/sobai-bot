import discord
from discord import app_commands
import os
from dotenv import load_dotenv
import aiohttp
import asyncio
import re
import logging

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

SYSTEM_PROMPT = (
    "Ты Собай — дружелюбный и остроумный ассистент в Discord. "
    "Отвечай коротко, по делу, с лёгким юмором там, где уместно. "
    "Пиши на том же языке, на котором задан вопрос."
)

async def ask_ai(prompt: str) -> str:
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20)
        ) as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 700,
                },
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Groq %s: %s", resp.status, body[:200])
                    return "❌ Groq API сейчас не отвечает, попробуй позже."
                data = await resp.json()
                return data["choices"][0]["message"]["content"]

    except aiohttp.ClientError as e:
        log.error("Сетевая ошибка: %s", e)
        return "🌐 Проблема с сетью, попробуй чуть позже."
    except asyncio.TimeoutError:
        log.warning("Groq timeout")
        return "⏱️ Groq не успел ответить за 20 секунд, попробуй снова."
    except Exception as e:
        log.exception("Неожиданная ошибка ask_ai: %s", e)
        return "Я сейчас немного занят, попробуй позже 😅"

@tree.command(name="sobai", description="Поговорить с Собай")
@app_commands.describe(message="Что спросить")
@app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
async def sobai(interaction: discord.Interaction, message: str):
    try:
        await interaction.response.defer(thinking=True)
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("defer() не удался: %s", e)
        return

    response = await ask_ai(message)

    try:
        await interaction.followup.send(response[:2000])
    except discord.NotFound:
        try:
            await interaction.channel.send(
                f"**{interaction.user.name}**, вот ответ:\n{response[:2000]}"
            )
        except discord.HTTPException:
            pass
    except discord.HTTPException as e:
        log.error("Ошибка followup: %s", e)

@sobai.error
async def sobai_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"⏳ Подожди ещё {error.retry_after:.1f} сек.", ephemeral=True
        )
    else:
        log.exception("Ошибка команды /sobai: %s", error)

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
        await message.reply("Да? Чем помочь? 😊")
        return

    async with message.channel.typing():
        response = await ask_ai(clean_text)
        try:
            await message.reply(response[:2000])
        except discord.HTTPException as e:
            log.error("Ошибка reply: %s", e)

@client.event
async def on_ready():
    await tree.sync()
    log.info("✅ Бот %s запущен!", client.user)

client.run(os.getenv("DISCORD_TOKEN"))
