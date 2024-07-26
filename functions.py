import json
from datetime import datetime, timedelta
import aiofiles
from collections import defaultdict, deque
import requests
from aiogram import types
import google.generativeai as genai
from aiogram.types import Message, CallbackQuery, FSInputFile
from texts import STOP_WORDS

genai.configure(api_key="AIzaSyDIulVh4cgNgZ_OKgV4T3LrDid6-HqoOMU")

user_chat_histories = defaultdict(list)
user_message_timestamps = defaultdict(deque)
user_image_timestamps = defaultdict(deque)


async def check_and_update_premium_status(user_id):
    async with aiofiles.open('premium_users.json', 'r') as file:
        premium_users = json.loads(await file.read())

    if str(user_id) in premium_users:
        expiration_date = datetime.strptime(premium_users[str(user_id)], '%Y-%m-%d')
        if expiration_date > datetime.now():
            return True, expiration_date.strftime('%Y-%m-%d')
        else:
            del premium_users[str(user_id)]
            async with aiofiles.open('premium_users.json', 'w') as file:
                await file.write(json.dumps(premium_users, ensure_ascii=False, indent=4))
            return False, None
    else:
        return False, None


def contains_stop_words(text):
    for word in STOP_WORDS:
        if word.lower() in text.lower():
            return True
    return False


def check_message_rate(user_id, is_premium):
    now = datetime.now()
    timestamps = user_message_timestamps[user_id]
    limit = 250 if is_premium else 70
    while timestamps and now - timestamps[0] > timedelta(hours=2):
        timestamps.popleft()
    if len(timestamps) < limit:
        return True, None
    else:
        next_allowed_time = timestamps[0] + timedelta(hours=2)
        time_left = next_allowed_time - now
        return False, time_left


def check_image_rate(user_id, is_premium):
    now = datetime.now()
    timestamps = user_image_timestamps[user_id]
    limit = 30 if is_premium else 10
    while timestamps and now - timestamps[0] > timedelta(hours=2):
        timestamps.popleft()
    if len(timestamps) < limit:
        return True, None
    else:
        next_allowed_time = timestamps[0] + timedelta(hours=2)
        time_left = next_allowed_time - now
        return False, time_left


def extract_json(text):
    try:
        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start != -1 and json_end != -1:
            response_json_str = text[json_start:json_end]
            return json.loads(response_json_str)
    except json.JSONDecodeError:
        pass
    return None


def remaining_messages(user_id, is_premium):
    now = datetime.now()
    timestamps = user_message_timestamps[user_id]
    limit = 250 if is_premium else 70
    while timestamps and now - timestamps[0] > timedelta(hours=2):
        timestamps.popleft()
    return limit - len(timestamps)


def remaining_images(user_id, is_premium):
    now = datetime.now()
    timestamps = user_image_timestamps[user_id]
    limit = 30 if is_premium else 10
    while timestamps and now - timestamps[0] > timedelta(hours=2):
        timestamps.popleft()
    return limit - len(timestamps)


async def save_photo(photo: types.PhotoSize, user_id: int, bot):
    date_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    file_path = f"{date_str}_{user_id}.jpg"

    file_info = await bot.get_file(photo.file_id)
    await bot.download_file(file_info.file_path, file_path)

    return file_path


async def upload_to_gemini(path, mime_type=None):
    """Загружает указанный файл в Gemini."""
    file = genai.upload_file(path, mime_type=mime_type)
    return file
