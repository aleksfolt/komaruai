from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import types


async def back_to_menu(user_id):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🧹 Удалить чат", callback_data=f"clear_chat:{user_id}"))
    return builder.as_markup()


async def payment_crypto_keyboard(invoice_id, invoice_url):
    builder = InlineKeyboardBuilder()
    pay_button = types.InlineKeyboardButton(text="Оплатить", url=invoice_url)
    paid_button = types.InlineKeyboardButton(text="Я оплатил",
                                             callback_data=f"verify_payment_{invoice_id}")
    builder.add(pay_button, paid_button)
    return builder.as_markup()
