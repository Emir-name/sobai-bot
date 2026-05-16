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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


# ====================== ВЕБ-СЕРВЕР (для UptimeRobot) ======================

async def handle(request):
    return web.Response(text="Собай жив! 🐾")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    log.info("🌐 Веб-сервер запущен на порту 8080")

def run_webserver():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_webserver())
    loop.run_forever()


# ====================== ЯЗЫКИ ПО КАНАЛАМ ======================

SYSTEM_PROMPT_BASE = (
    "Ты Собай — дружелюбный и остроумный ассистент в Discord. "
    "Отвечай коротко, по делу, с лёгким юмором там, где уместно."
)

CHANNEL_LANGUAGES = {
    "ru-chat":     "Отвечай ТОЛЬКО на русском языке.",
    "en-chat":     "Reply ONLY in English.",
    "mx-chat":     "Responde SOLO en español mexicano.",
    "global-chat": "Пиши на том же языке, на котором задан вопрос.",
}

def get_system_prompt(channel_name: str) -> str:
    for key, lang in CHANNEL_LANGUAGES.items():
        if key in channel_name:
            return f"{SYSTEM_PROMPT_BASE} {lang}"
    return f"{SYSTEM_PROMPT_BASE} Пиши на том же языке, на котором задан вопрос."


# ====================== AI ======================

async def ask_ai(prompt: str, channel_name: str = "") -> str:
    system_prompt = get_system_prompt(channel_name)
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
                        {"role": "system", "content": system_prompt},
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


# ====================== ГЕНЕРАЦИЯ ФОТО ======================

async def generate_image(prompt: str) -> bytes | None:
    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        ) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                log.error("Pollinations %s", resp.status)
                return None
    except Exception as e:
        log.exception("Ошибка генерации изображения: %s", e)
        return None


# ====================== ГЕНЕРАЦИЯ ВИДЕО ======================

async def generate_video(prompt: str) -> str | None:
    token = os.getenv("REPLICATE_API_TOKEN")
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json"
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
            # Создаём задачу
            async with session.post(
                "https://api.replicate.com/v1/predictions",
                headers=headers,
                json={
                    "version": "9f747673945c62801b13b84701c783929c0ee784e4748ec062204894dda1a351",
                    "input": {"prompt": prompt, "num_frames": 24, "fps": 8}
                }
            ) as resp:
                if resp.status not in (200, 201):
                    log.error("Replicate create %s", resp.status)
                    return None
                data = await resp.json()
                prediction_id = data["id"]

            # Ждём результат
            for _ in range(60):
                await asyncio.sleep(5)
                async with session.get(
                    f"https://api.replicate.com/v1/predictions/{prediction_id}",
                    headers=headers
                ) as resp:
                    data = await resp.json()
                    status = data.get("status")
                    if status == "succeeded":
                        output = data.get("output")
                        if isinstance(output, list):
                            return output[0]
                        return output
                    elif status == "failed":
                        log.error("Replicate failed: %s", data.get("error"))
                        return None

            return None
    except Exception as e:
        log.exception("Ошибка генерации видео: %s", e)
        return None


# ====================== СЛЕШ-КОМАНДА /sobai ======================

@tree.command(name="sobai", description="Поговорить с Собай")
@app_commands.describe(message="Что спросить")
@app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
async def sobai(interaction: discord.Interaction, message: str):
    try:
        await interaction.response.defer(thinking=True)
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("defer() не удался: %s", e)
        return

    channel_name = getattr(interaction.channel, "name", "")
    response = await ask_ai(message, channel_name)

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


# ====================== СЛЕШ-КОМАНДА /sobima ======================

@tree.command(name="sobima", description="Сгенерировать изображение")
@app_commands.describe(prompt="Опиши что нарисовать")
@app_commands.checks.cooldown(1, 15, key=lambda i: i.user.id)
async def sobima(interaction: discord.Interaction, prompt: str):
    try:
        await interaction.response.defer(thinking=True)
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("defer() не удался: %s", e)
        return

    image_data = await generate_image(prompt)

    if image_data is None:
        try:
            await interaction.followup.send("❌ Не удалось сгенерировать изображение, попробуй ещё раз.")
        except discord.HTTPException:
            pass
        return

    file = discord.File(fp=io.BytesIO(image_data), filename="sobai_image.png")

    try:
        await interaction.followup.send(content=f"🎨 **{prompt}**", file=file)
    except discord.NotFound:
        try:
            await interaction.channel.send(
                content=f"🎨 **{prompt}**",
                file=discord.File(fp=io.BytesIO(image_data), filename="sobai_image.png")
            )
        except discord.HTTPException:
            pass
    except discord.HTTPException as e:
        log.error("Ошибка отправки изображения: %s", e)


@sobima.error
async def sobima_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"⏳ Подожди ещё {error.retry_after:.1f} сек.", ephemeral=True
        )


# ====================== СЛЕШ-КОМАНДА /video ======================

@tree.command(name="video", description="Сгенерировать видео")
@app_commands.describe(prompt="Опиши что снять")
@app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
async def video(interaction: discord.Interaction, prompt: str):
    try:
        await interaction.response.defer(thinking=True)
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("defer() не удался: %s", e)
        return

    try:
        await interaction.followup.send("🎬 Генерирую видео, подожди 1-2 минуты...")
    except discord.HTTPException:
        pass

    video_url = await generate_video(prompt)

    if video_url is None:
        try:
            await interaction.channel.send("❌ Не удалось сгенерировать видео, попробуй ещё раз.")
        except discord.HTTPException:
            pass
        return

    try:
        await interaction.channel.send(
            content=f"🎬 **{prompt}**\n{video_url}"
        )
    except discord.HTTPException as e:
        log.error("Ошибка отправки видео: %s", e)


@video.error
async def video_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"⏳ Подожди ещё {error.retry_after:.1f} сек.", ephemeral=True
        )


# ====================== /roll ======================

@tree.command(name="roll", description="Бросить кубик")
@app_commands.describe(sides="Количество граней (по умолчанию 6)")
async def roll(interaction: discord.Interaction, sides: int = 6):
    if sides < 2:
        await interaction.response.send_message("❌ Кубик должен иметь минимум 2 грани!", ephemeral=True)
        return
    result = random.randint(1, sides)
    await interaction.response.send_message(
        f"🎲 **{interaction.user.name}** бросил кубик d{sides} и выпало... **{result}**!"
    )


# ====================== /8ball ======================

EIGHT_BALL_ANSWERS = [
    "✅ Определённо да!",
    "✅ Без сомнений!",
    "✅ Скорее всего да.",
    "✅ Похоже на то.",
    "🤔 Спроси позже...",
    "🤔 Лучше не знать.",
    "🤔 Сложно сказать.",
    "❌ Не рассчитывай на это.",
    "❌ Мой ответ — нет.",
    "❌ Определённо нет!",
]

@tree.command(name="8ball", description="Магический шар отвечает на твой вопрос")
@app_commands.describe(question="Задай вопрос")
async def eight_ball(interaction: discord.Interaction, question: str):
    answer = random.choice(EIGHT_BALL_ANSWERS)
    msg = "🎱 **" + question + "**\n" + answer
    await interaction.response.send_message(msg)


# ====================== ОТВЕТ НА УПОМИНАНИЯ ======================

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

    channel_name = getattr(message.channel, "name", "")
    async with message.channel.typing():
        response = await ask_ai(clean_text, channel_name)
        try:
            await message.reply(response[:2000])
        except discord.HTTPException as e:
            log.error("Ошибка reply: %s", e)


# ====================== СТАРТ ======================

@client.event
async def on_ready():
    await tree.sync()
    log.info("✅ Бот %s запущен!", client.user)


threading.Thread(target=run_webserver, daemon=True).start()

client.run(os.getenv("DISCORD_TOKEN"))
