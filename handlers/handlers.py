import json
import os
import requests
from collections import defaultdict, deque
from aiocryptopay import AioCryptoPay, Networks
from aiogram import Router, F, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import asyncio
import google.generativeai as genai
import aiofiles
from google.generativeai.types import HarmCategory, HarmBlockThreshold

import config
from callbacks import verify_payment_call
from functions import check_and_update_premium_status, save_photo, upload_to_gemini, extract_json, check_image_rate, \
    contains_stop_words, check_message_rate, remaining_messages, remaining_images
from kb import back_to_menu, payment_crypto_keyboard
from kandinsky import Text2ImageAPI
from texts import bot_prompt

genai.configure(api_key="AIzaSyDIulVh4cgNgZ_OKgV4T3LrDid6-HqoOMU")
crypto = AioCryptoPay(token=config.AIO_TOKEN, network=Networks.MAIN_NET)

generation_config = {
    "temperature": 1,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config=generation_config,
    system_instruction=bot_prompt,
    safety_settings={
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE
        }
)

router = Router()
user_chat_histories = defaultdict(list)
user_message_timestamps = defaultdict(deque)
user_image_timestamps = defaultdict(deque)

executor = ThreadPoolExecutor()


async def setup_router(dp, bot):
    @router.message(Command("start"))
    async def start_handler(msg: Message):
        chat_id = msg.chat.id
        await bot.forward_message(chat_id=chat_id, from_chat_id=config.TARGET_CHAT_ID, message_id=2)

    @router.message(F.photo)
    async def handle_photo(msg: Message):
        try:
            caption = msg.caption or ""
            if not caption.startswith("/kmr"):
                return

            user_id = msg.from_user.id
            is_premium, _ = await check_and_update_premium_status(user_id)
            user_query = caption[5:].strip()
            if not user_query:
                await msg.reply("Пожалуйста, введите запрос после команды /kmr.")
                return

            wait_message = await msg.reply("Обработка фото, пожалуйста подождите...")

            photo = msg.photo[-1]
            file_path = await save_photo(photo, user_id, bot)
            gemini_file = [await upload_to_gemini(file_path, mime_type="image/jpeg")]
            chat_history = user_chat_histories[user_id]
            chat_history.append({"role": "user", "parts": [gemini_file[0]]})
            chat_session = model.start_chat(history=chat_history)
            response = chat_session.send_message(user_query)

            chat_history.append({"role": "model", "parts": [response.text]})

            response_json = extract_json(response.text)
            if response_json and 'image' in response_json and 'prompt' in response_json['image']:
                can_generate_image, time_left_img = check_image_rate(user_id, is_premium)
                if not can_generate_image:
                    await wait_message.edit_text(
                        text=f"Превышено количество запросов на генерацию изображений. Подождите {time_left_img.total_seconds() // 60:.0f} минут.\nЗапросы можно увеличить покупкой премиума! Команда /premium.")
                    return

                user_image_timestamps[user_id].append(datetime.now())

                prompt = response_json['image']['prompt']
                message = response_json['image'].get('message', 'Here is your image 😊')
                if prompt.strip():
                    await wait_message.edit_text(text="Создаю изображение...")
                    api = Text2ImageAPI(
                        'https://api-key.fusionbrain.ai/',
                        '2968768A141CA34D4EA4EBF0F2C0C6F3',
                        '6EA7E0E92F1CB6C99649840F5CF8D02F'
                    )
                    model_id = await asyncio.get_event_loop().run_in_executor(executor, api.get_model)
                    uuid = await asyncio.get_event_loop().run_in_executor(executor,
                                                                          lambda: api.generate(prompt, model_id))
                    result = await asyncio.get_event_loop().run_in_executor(executor,
                                                                            lambda: api.check_generation(uuid))
                    if result:
                        for path in result:
                            photo = FSInputFile(path)
                            await bot.send_photo(chat_id=msg.chat.id, photo=photo, caption=message,
                                                 reply_to_message_id=msg.message_id)
                            await wait_message.delete()
                            os.remove(path)
                    return

            if contains_stop_words(response.text):
                await wait_message.edit_text(text="Ответ заблокирован из-за нецензурной или токсичной лексики.")
                os.remove(file_path)
                return
            try:
                await wait_message.edit_text(text=response.text, reply_markup=await back_to_menu(user_id),
                                             parse_mode=ParseMode.MARKDOWN)
                os.remove(file_path)
            except Exception as e:
                os.remove(file_path)
                print(f"Ошибка при редактировании сообщения (Markdown): {e}")
                try:
                    await wait_message.edit_text(text=response.text, reply_markup=await back_to_menu(user_id))
                    os.remove(file_path)
                except Exception as inner_e:
                    print(f"Ошибка при редактировании сообщения (без Markdown): {inner_e}")

        except Exception as e:
            print(f"Ошибка при обработке фото: {e}")
            await msg.reply(f"Ошибка при обработке фото, попробуйте заменить картинку или поменять запрос.")

    @router.message(F.text.casefold().startswith("/kmr".casefold()))
    async def gm_handler(msg: Message):
        global response, user_id, wait_msg
        wait_msg = None
        try:
            user_id = msg.from_user.id
            is_premium, _ = await check_and_update_premium_status(user_id)

            can_send_message, time_left_msg = check_message_rate(user_id, is_premium)
            if not can_send_message:
                await msg.reply(
                    text=f"Превышено количество сообщений. Подождите {time_left_msg.total_seconds() // 60:.0f} минут.\nЗапросы можно увеличить покупкой премиума! Команда /premium.")
                return

            user_message_timestamps[user_id].append(datetime.now())

            user_input = ' '.join(msg.text.split()[1:])
            if not user_input.strip():
                await msg.reply(text="Пожалуйста, введите запрос после команды /kmr.")
                return

            if contains_stop_words(user_input):
                await msg.reply(text="Ответ заблокирован из-за нецензурной или токсичной лексики.")
                return

            wait_msg = await msg.reply(text="...")

            chat_history = user_chat_histories[user_id]

            chat_session = model.start_chat(history=chat_history)
            response = chat_session.send_message(user_input)

            chat_history.append({"role": "user", "parts": [user_input]})
            chat_history.append({"role": "model", "parts": [response.text]})

            response_json = extract_json(response.text)
            if response_json and 'image' in response_json and 'prompt' in response_json['image']:
                can_generate_image, time_left_img = check_image_rate(user_id, is_premium)
                if not can_generate_image:
                    if wait_msg:
                        await wait_msg.edit_text(
                            text=f"Превышено количество запросов на генерацию изображений. Подождите {time_left_img.total_seconds() // 60:.0f} минут.\nЗапросы можно увеличить покупкой премиума! Команда /premium.")
                    return

                user_image_timestamps[user_id].append(datetime.now())

                prompt = response_json['image']['prompt']
                message = response_json['image'].get('message', 'Here is your image 😊')
                if prompt.strip():
                    if wait_msg:
                        await wait_msg.edit_text(text="Создаю изображение...")
                    api = Text2ImageAPI(
                        'https://api-key.fusionbrain.ai/',
                        '2968768A141CA34D4EA4EBF0F2C0C6F3',
                        '6EA7E0E92F1CB6C99649840F5CF8D02F'
                    )
                    model_id = await asyncio.get_event_loop().run_in_executor(executor, api.get_model)
                    uuid = await asyncio.get_event_loop().run_in_executor(executor,
                                                                          lambda: api.generate(prompt, model_id))
                    result = await asyncio.get_event_loop().run_in_executor(executor,
                                                                            lambda: api.check_generation(uuid))
                    if result:
                        for path in result:
                            photo = FSInputFile(path)
                            await bot.send_photo(chat_id=msg.chat.id, photo=photo, caption=message,
                                                 reply_to_message_id=msg.message_id)
                            try:
                                if wait_msg:
                                    await wait_msg.delete()
                            except Exception as e:
                                print(f"Ошибка при удалении сообщения: {e}")
                            os.remove(path)
                    return

            if contains_stop_words(response.text):
                if wait_msg:
                    await wait_msg.edit_text(text="Ответ заблокирован из-за нецензурной или токсичной лексики.")
                return

            try:
                if wait_msg:
                    await wait_msg.edit_text(text=response.text, reply_markup=await back_to_menu(user_id),
                                             parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                print(f"Ошибка при редактировании сообщения (Markdown): {e}")
                try:
                    if wait_msg:
                        await wait_msg.edit_text(text=response.text, reply_markup=await back_to_menu(user_id))
                except Exception as inner_e:
                    print(f"Ошибка при редактировании сообщения (без Markdown): {inner_e}")
                    await msg.answer(user_id=6184515646, text=str(inner_e))

        except Exception as e:
            print(f"Ошибка при генерации ответа: {e}")
            try:
                if wait_msg:
                    await wait_msg.edit_text(
                        text="Произошла ошибка при генерации, попробуйте позже или поменяйте запрос.",
                        reply_markup=await back_to_menu(user_id))
                await msg.answer(user_id=6184515646, text=str(e))
            except Exception as inner_e:
                print(f"Ошибка при редактировании сообщения об ошибке: {inner_e}")
                await msg.answer(user_id=6184515646, text=str(inner_e))

    @router.message(Command("menu"))
    async def menu_handler(msg: Message):
        try:
            user_id = msg.from_user.id
            is_premium, premium_expiration = await check_and_update_premium_status(user_id)
            premium_message = f"Премиум: активен до {premium_expiration}" if is_premium else "Премиум: не активен"

            remaining_msg = remaining_messages(user_id, is_premium)
            remaining_img = remaining_images(user_id, is_premium)

            response = (
                f"🔐 {msg.from_user.first_name} Твой профиль:\n\n"
                f"💎 {premium_message}\n"
                f"📄 Оставшиеся текстовые запросы к Komaru Assistant: {remaining_msg}\n"
                f"🖼 Оставшиеся генерации изображений: {remaining_img}\n"
            )

            await msg.reply(text=response)
        except Exception as e:
            print(f"Ошибка при отображении меню: {e}")
            await msg.reply(text=f"Ошибка: {e}")

    @router.message(F.text.casefold().startswith("/img".casefold()))
    async def image_handler(msg: Message):
        try:
            user_id = msg.from_user.id
            is_premium, _ = await check_and_update_premium_status(user_id)

            can_generate_image, time_left_img = check_image_rate(user_id, is_premium)
            if not can_generate_image:
                await msg.reply(
                    text=f"Превышено количество запросов на генерацию изображений. Подождите {time_left_img.total_seconds() // 60:.0f} минут.\nЗапросы можно увеличить покупкой премиума! Команда /premium.")
                return

            user_image_timestamps[user_id].append(datetime.now())

            user_input = ' '.join(msg.text.split()[1:])
            if not user_input.strip():
                await msg.reply(text="Пожалуйста, введите текст после команды /img.")
                return

            if contains_stop_words(user_input):
                await msg.reply(text="Запрос заблокирован из-за нецензурной или токсичной лексики.")
                return

            wait_message = await msg.reply("Генерация изображения...")

            api = Text2ImageAPI(
                'https://api-key.fusionbrain.ai/',
                '2968768A141CA34D4EA4EBF0F2C0C6F3',
                '6EA7E0E92F1CB6C99649840F5CF8D02F'
            )
            model_id = await asyncio.get_event_loop().run_in_executor(executor, api.get_model)
            uuid = await asyncio.get_event_loop().run_in_executor(executor, lambda: api.generate(user_input, model_id))
            result = await asyncio.get_event_loop().run_in_executor(executor, lambda: api.check_generation(uuid))
            if result:
                for path in result:
                    photo = FSInputFile(path)
                    await bot.send_photo(chat_id=msg.chat.id, photo=photo, reply_to_message_id=msg.message_id)
                    os.remove(path)  # Remove the image after sending
                    print(f"result: {path}")
                await wait_message.delete()
            else:
                await wait_message.edit_text("Не удалось сгенерировать изображение.")

        except Exception as e:
            print(f"Ошибка при генерации изображения: {e}")
            await msg.reply(text=f"Ошибка: {e}. Возможно не надо всякую херню в промпт писать.")

    @router.message(Command("premium"))
    async def buy_premium(msg: Message):
        try:
            user_id = msg.from_user.id
            try:
                invoice = await crypto.create_invoice(asset='USDT', amount=1)
                if not invoice:
                    await bot.send_message(user_id, "Ошибка при создании инвойса. Попробуйте позже.")
                    return None

                markup = await payment_crypto_keyboard(invoice.invoice_id, invoice.bot_invoice_url)

                response = (
                    f"❓ Что дает премиум?\n\n🖼 Генерировать изображения 30 раз за 2 часа вместо 10.\n"
                    f"✍️ Делать 250 запросов к нейросети за 2 часа вместо 70.\n\n"
                    f"💎 Премиум активируется после подтверждения оплаты. Реквизиты: {invoice.bot_invoice_url}"
                )
                await bot.send_message(user_id, response, reply_markup=markup)

                await msg.reply("Данные для оплаты отправлены вам в личные сообщения.")

                return invoice

            except Exception as e:
                print(f"Ошибка при создании инвойса: {e}")
                await msg.reply(
                    "Не удалось отправить сообщение в личные сообщения. Пожалуйста, напишите мне в личку и попробуйте снова.")
                return None

        except Exception as e:
            error_message = f"Ошибка при создании инвойса: {e}"
            print(error_message)
            await bot.send_message(user_id, error_message)
            return None

    @router.callback_query(F.data.startswith("verify_payment"))
    async def verify_payment(call):
        await verify_payment_call(call, bot)

    @router.callback_query(F.data.startswith("clear_chat:"))
    async def clear_chat_handler(callback_query: CallbackQuery):
        try:
            _, action_user_id = callback_query.data.split(":")
            if str(callback_query.from_user.id) != action_user_id:
                await callback_query.answer("Вы не можете очистить чужой чат.", show_alert=True)
                return

            user_chat_histories[int(action_user_id)] = []
            await callback_query.answer(text="Чат успешно очищен.")
            await callback_query.answer()
        except Exception as e:
            print(f"Ошибка при очистке чата: {e}")
            await callback_query.message.reply(text=f"Ошибка: {e}")
