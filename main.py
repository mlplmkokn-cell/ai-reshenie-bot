import os
import time
import threading
import queue
import sqlite3
import requests
import telebot
from dotenv import load_dotenv
from yookassa import Configuration, Payment
from flask import Flask

# --- ЗАГЛУШКА ДЛЯ RENDER (чтобы не было ошибки Port scan timeout) ---
app = Flask(__name__)
@app.route('/')
def index():
    return "Bot is running", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()
# -------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
SHOP_ID = os.getenv('SHOP_ID')
SHOP_API_KEY = os.getenv('SHOP_API_KEY')
FREE_GEMINI_KEY = os.getenv('FREE_GEMINI_KEY')
VIP_GEMINI_KEY = os.getenv('VIP_GEMINI_KEY')

Configuration.account_id = SHOP_ID
Configuration.secret_key = SHOP_API_KEY

bot = telebot.TeleBot(BOT_TOKEN)
VIP_PRICE = 99.00

# Очереди
free_queue = queue.Queue()
vip_queue = queue.Queue()

# Подключение к БД
def db_query(query, args=(), commit=False):
    conn = sqlite3.connect('users.db', check_same_thread=False)
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
        vip_until DATETIME
    )''', commit=True)
    db_query('''CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER
    )''', commit=True)

init_db()

def check_vip(user_id):
    res = db_query("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
    if res and res[0][0]:
        return True
    return False

def check_payment(payment_id, user_id):
    try:
        payment = Payment.find_one(payment_id)
        if payment.status == 'succeeded':
            db_query("UPDATE users SET is_vip = 1 WHERE user_id = ?", (user_id,), commit=True)
            bot.send_message(user_id, "Оплата прошла успешно! Теперь у тебя VIP статус 🌟")
            return True
        return False
    except:
        return False

def ask_ai(prompt, key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
    try:
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
        return res.json()['candidates'][0]['content']['parts'][0]['text']
    except:
        return "❌ Ошибка нейросети. Попробуй позже."

@bot.message_handler(commands=['start'])
def start(message):
    db_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,), commit=True)
    welcome_text = (
        "Привет!\n"
        "Присылай фото или текст, я всё решу. 🚀\n\n"
        "В зависимости от загруженности я могу ответить в течение двух минут. "
        "Если хочешь мгновенных ответов без очереди — попробуй наш VIP-режим!"
    )
    bot.send_message(message.chat.id, welcome_text)

@bot.message_handler(commands=['vip'])
def vip(message):
    try:
        payment = Payment.create({
            "amount": {
                "value": str(VIP_PRICE),
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/ТВОЙ_БОТ" # Вставь тут ссылку на своего бота
            },
            "capture": True,
            "description": "Оплата VIP статуса"
        })
        
        db_query("INSERT INTO payments (payment_id, user_id) VALUES (?, ?)", (payment.id, message.chat.id), commit=True)
        bot.send_message(message.chat.id, f"Оплатить VIP статус ({VIP_PRICE} руб.) можно по ссылке:\n{payment.confirmation.confirmation_url}")
    except Exception as e:
        bot.send_message(message.chat.id, "Ошибка при создании платежа. Попробуй позже.")

@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    if check_vip(message.from_user.id):
        vip_queue.put(message)
        bot.send_message(message.chat.id, "✨ VIP: Решаю твою задачу вне очереди...")
    else:
        free_queue.put(message)
        bot.send_message(message.chat.id, "⏳ Задача в очереди. Обычно занимает около минуты. Чтобы без очереди - жми /vip")

def process_queue():
    while True:
        if not vip_queue.empty():
            msg = vip_queue.get()
            key = VIP_GEMINI_KEY
        elif not free_queue.empty():
            msg = free_queue.get()
            key = FREE_GEMINI_KEY
        else:
            time.sleep(1)
            continue

        try:
            prompt = ""
            if msg.content_type == 'text':
                prompt = msg.text
            else:
                prompt = "Опиши, что на фото и реши задачу"
            
            ans = ask_ai(prompt, key)
            bot.send_message(msg.chat.id, ans)
        except Exception as e:
            bot.send_message(msg.chat.id, "❌ Произошла ошибка при обработке.")
        time.sleep(2)

threading.Thread(target=process_queue, daemon=True).start()

if __name__ == '__main__':
    print("Бот запущен и готов к работе...")
    bot.polling(none_stop=True)
