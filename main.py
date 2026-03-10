import os
import time
import threading
import queue
import sqlite3
import requests
import base64
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from yookassa import Configuration, Payment
from flask import Flask

# --- ЗАГЛУШКА ДЛЯ RENDER ---
app = Flask(__name__)
@app.route('/')
def index():
    return "Bot is running", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
SHOP_ID = os.getenv('SHOP_ID')
SHOP_API_KEY = os.getenv('SHOP_API_KEY')
FREE_GEMINI_KEY = os.getenv('FREE_GEMINI_KEY')
VIP_GEMINI_KEY = os.getenv('VIP_GEMINI_KEY')

Configuration.account_id = SHOP_ID
Configuration.secret_key = SHOP_API_KEY

bot = telebot.TeleBot(BOT_TOKEN)

# ЦЕНЫ
PRICE_TRIAL = 99.00
PRICE_REGULAR = 199.00

# Очереди
free_queue = queue.Queue()
vip_queue = queue.Queue()

# --- БАЗА ДАННЫХ ---
def db_query(query, args=(), commit=False):
    # Назвал базу data_v2.db чтобы она создалась с чистого листа с новыми колонками
    conn = sqlite3.connect('data_v2.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(query, args)
    if commit:
        conn.commit()
        result = None
    else:
        result = cursor.fetchall()
    conn.close()
    return result

def init_db():
    db_query('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        is_vip BOOLEAN DEFAULT 0,
        trial_used BOOLEAN DEFAULT 0
    )''', commit=True)
    db_query('''CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER
    )''', commit=True)

init_db()

def check_vip(user_id):
    res = db_query("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
    return bool(res and res[0][0])

# --- МОЗГИ (GEMINI) ---
def ask_ai(prompt, base64_img, key, is_vip):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
    
    # МАРКЕТИНГ: VIP получает подробное решение, обычный - только ответ
    if is_vip:
        sys_prompt = "Ты — профессиональный репетитор. Реши задачу. Дай очень подробное, пошаговое объяснение, чтобы ученик всё понял."
    else:
        sys_prompt = "Реши задачу. Дай только краткий ответ и минимум пояснений."
        
    parts = [{"text": f"{sys_prompt}\nЗапрос пользователя: {prompt}"}]
    
    if base64_img:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64_img}})
        
    try:
        res = requests.post(url, json={"contents": [{"parts": parts}]}, timeout=60).json()
        return res['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return "❌ Ошибка нейросети. Возможно, фото нечеткое или сервер перегружен."

# --- ОБРАБОТЧИКИ БОТА ---
@bot.message_handler(commands=['start'])
def start(message):
    db_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,), commit=True)
    text = (
        "Привет! 👋\n"
        "Присылай фото с задачей или пиши текст — я помогу решить всё!\n\n"
        "Бесплатно я даю краткие ответы в порядке очереди. "
        "Хочешь мгновенные ответы и **подробные пошаговые решения**? Жми /vip 🚀"
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['vip'])
def vip_command(message):
    user_id = message.from_user.id
    res = db_query("SELECT trial_used FROM users WHERE user_id = ?", (user_id,))
    trial_used = res[0][0] if res else 0

    price = PRICE_REGULAR if trial_used else PRICE_TRIAL
    
    text = (
        "💎 **VIP-ДОСТУП**\n\n"
        "• Мгновенные ответы вне очереди ⚡️\n"
        "• Детальные расписанные решения 📝\n"
        "• Распознавание сложных почерков 📸\n\n"
    )
    
    if not trial_used:
        text += f"🎁 **АКЦИЯ!** Твой первый месяц всего за **{int(price)} руб.** (далее {int(PRICE_REGULAR)} руб/мес)."
    else:
        text += f"Продление VIP: **{int(price)} руб.** / месяц."

    try:
        payment = Payment.create({
            "amount": {"value": str(price), "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://t.me/Speed_fotoGDZ_bot"}, # ВСТАВЬ ТУТ ЮЗЕРНЕЙМ СВОЕГО БОТА
            "capture": True,
            "description": "Оплата VIP статуса"
        })
        db_query("INSERT INTO payments (payment_id, user_id) VALUES (?, ?)", (payment.id, user_id), commit=True)
        
        # Кнопки
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text="💳 Оплатить", url=payment.confirmation.confirmation_url))
        markup.add(InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data=f"check_{payment.id}"))
        
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(message.chat.id, "❌ Ошибка кассы. Попробуй позже.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment_callback(call):
    payment_id = call.data.split('_')[1]
    user_id = call.from_user.id
    
    try:
        payment = Payment.find_one(payment_id)
        if payment.status == 'succeeded':
            db_query("UPDATE users SET is_vip = 1, trial_used = 1 WHERE user_id = ?", (user_id,), commit=True)
            bot.edit_message_text("✅ Оплата найдена! Ура, теперь ты VIP 🌟", call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "⏳ Оплата еще не прошла. Если ты только что оплатил, подожди минутку и нажми снова.", show_alert=True)
    except:
        bot.answer_callback_query(call.id, "Ошибка проверки.", show_alert=True)

@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    user_id = message.from_user.id
    is_vip = check_vip(user_id)
    
    task = {
        'chat_id': message.chat.id,
        'is_vip': is_vip,
        'base64': None,
        'text': message.text if message.text else (message.caption if message.caption else "Реши то, что на фото.")
    }

    if message.content_type == 'photo':
        status = bot.reply_to(message, "📸 Читаю твой почерк...")
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            task['base64'] = base64.b64encode(downloaded_file).decode('utf-8')
            bot.delete_message(message.chat.id, status.message_id)
        except Exception as e:
            bot.edit_message_text("❌ Ошибка при загрузке фото.", message.chat.id, status.message_id)
            return

    if is_vip:
        vip_queue.put(task)
        bot.send_message(message.chat.id, "✨ VIP: Готовлю подробное решение вне очереди...")
    else:
        free_queue.put(task)
        bot.send_message(message.chat.id, "⏳ Задача в очереди. Для ответа без очереди и с подробным решением — жми /vip")

# --- ФОНОВЫЙ ОБРАБОТЧИК ОЧЕРЕДЕЙ ---
def process_queue():
    while True:
        if not vip_queue.empty():
            task = vip_queue.get()
            key = VIP_GEMINI_KEY
        elif not free_queue.empty():
            task = free_queue.get()
            key = FREE_GEMINI_KEY
        else:
            time.sleep(1)
            continue

        try:
            ans = ask_ai(task['text'], task['base64'], key, task['is_vip'])
            bot.send_message(task['chat_id'], ans)
        except Exception as e:
            bot.send_message(task['chat_id'], "❌ Произошла ошибка при обработке нейросетью.")
        
        time.sleep(2) # Пауза чтобы не спамить API Google

threading.Thread(target=process_queue, daemon=True).start()

if __name__ == '__main__':
    print("Бот запущен...")
    bot.polling(none_stop=True)
