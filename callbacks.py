import json
from datetime import timedelta, datetime
import aiofiles
from aiocryptopay import AioCryptoPay, Networks
import config

crypto = AioCryptoPay(token=config.AIO_TOKEN, network=Networks.MAIN_NET)


async def activate_premium(user_id, days):
    try:
        premium_duration = timedelta(days=days)
        async with aiofiles.open('premium_users.json', 'r+') as file:
            data = json.loads(await file.read())
            if str(user_id) in data:
                current_expiration = datetime.strptime(data[str(user_id)], '%Y-%m-%d')
                new_expiration_date = current_expiration + premium_duration
            else:
                new_expiration_date = datetime.now() + premium_duration

            data[str(user_id)] = new_expiration_date.strftime('%Y-%m-%d')
            await file.seek(0)
            await file.write(json.dumps(data, ensure_ascii=False, indent=4))
            await file.truncate()
    except Exception as e:
        print(f"Ошибка активации премиум-статуса: {e}")


async def verify_payment_call(call, bot):
    parts = call.data.split('_')
    print(parts)
    if len(parts) < 3:
        await call.message.answer(call.message.chat.id, "Ошибка в данных платежа.")
        return

    action, context, invoice = parts[0], parts[1], parts[2]

    try:
        print("Invoice ID:", invoice)
        invoice_stat = await crypto.get_invoices(invoice_ids=int(invoice))
        if invoice_stat.status == 'paid':
            await activate_premium(call.from_user.id, 30)
            await call.message.answer("🌟 Спасибо за покупку Премиума! Наслаждайтесь эксклюзивными преимуществами.\n\nЧто дает премиум?\nГенерировать изображения 30 раз за 2 часа вместо 10.\nДелать 250 запросов к нейросети за 2 часа вместо 70.")
            await bot.delete_message(call.message.chat.id, call.message.message_id)
        else:
            await bot.send_message(call.from_user.id, "Оплата не прошла! Попробуйте еще раз.")
    except Exception as e:
        await bot.send_message(call.from_user.id, f"Произошла ошибка при проверке статуса платежа: {str(e)}")
