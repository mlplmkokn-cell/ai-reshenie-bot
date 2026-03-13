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

# --- СЕРВЕР ДЛЯ RAILWAY (чтобы не было статуса 'Inactive') ---
app = Flask(__name__)

@app.route('/')
def status():
    return "Бот работает. Очередь активна.", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
SHOP_ID = os.getenv('SHOP_ID')
SHOP_API_KEY = os.getenv('SHOP_API_KEY')
FREE_KEY = os.getenv('FREE_GEMINI_KEY')
VIP_KEY = os.getenv('VIP_GEMINI_KEY')

bot = telebot.TeleBot(BOT_TOKEN)

if SHOP_ID and SHOP_API_KEY:
    Configuration.account_id = SHOP_ID
    Configuration.secret_key = SHOP_API_KEY

# Очереди задач
free_queue = queue.Queue()
vip_queue = queue.Queue()

# --- БАЗА ДАННЫХ ---
def get_db():
    return sqlite3.connect('data_final.db', check_same_thread=False)

def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        is_vip BOOLEAN DEFAULT 0,
                        trial_used BOOLEAN DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS payments (
                        payment_id TEXT PRIMARY KEY, 
                        user_id INTEGER)''')
    conn.commit()
    conn.close()

init_db()

# --- ЛОГИКА ОЧЕРЕДИ И AI ---
def ask_ai(prompt, img_b64, api_key, is_vip):
    # Используем проверенную версию v1beta
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    sys_prompt = "Ты профи-репетитор. Дай подробный ответ." if is_vip else "Дай краткий ответ. Для шагов купи /vip."
    
    payload = {
        "contents": [{"parts": [{"text": f"{sys_prompt}\nЗадание: {prompt}"}]}]
    }
    if img_b64:
        payload["contents"][0]["parts"].append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})

    # Бот будет ждать ответа до 2 минут, не падая
    try:
        response = requests.post(url, json=payload, timeout=120)
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        return "⚠️ Нейросеть сейчас перегружена, но я попробую еще раз через минуту."
    except:
        return "❌ Сервер ИИ не ответил вовремя. Попробуйте еще раз."

# --- ВОРКЕР (Обработка очереди) ---
def worker():
    while True:
        task = None
        current_key = FREE_KEY
        
        # Сначала всегда VIP
        if not vip_queue.empty():
            task = vip_queue.get()
            current_key = VIP_KEY
        elif not free_queue.empty():
            task = free_queue.get()
            current_key = FREE_KEY
        
        if task:
            try:
                # Оповещаем, что начинаем решать
                bot.send_message(task['chat_id'], "✍️ Приступаю к решению вашей задачи...")
                result = ask_ai(task['text'], task['img'], current_key, task['is_vip'])
                bot.send_message(task['chat_id'], result)
            except Exception as e:
                print(f"Ошибка в воркере: {e}")
            
            # Небольшая пауза, чтобы не забанили API
            time.sleep(2)
        else:
            time.sleep(1)

threading.Thread(target=worker, daemon=True).start()

# --- ОБРАБОТЧИКИ ТЕЛЕГРАМ ---
@bot.message_handler(commands=['start'])
def start_cmd(message):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Привет! Пришли задачу текстом или фото. Я решу её в порядке очереди.")

@bot.message_handler(commands=['vip'])
def buy_vip(message):
    user_id = message.from_user.id
    conn = get_db()
    user = conn.execute("SELECT trial_used FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    price = 199 if (user and user[0]) else 99
    try:
        payment = Payment.create({
            "amount": {"value": str(price), "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://t.me/your_bot_link"},
            "capture": True, "description": "VIP доступ"
        })
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💳 Оплатить", url=payment.confirmation.confirmation_url))
        bot.send_message(message.chat.id, f"💎 VIP за {price}₽: решение без очереди и подробно!", reply_markup=markup)
    except:
        bot.send_message(message.chat.id, "Ошибка оплаты. Попробуйте позже.")

@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    user_id = message.from_user.id
    conn = get_db()
    user_data = conn.execute("SELECT is_vip FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    
    is_vip = bool(user_data and user_data[0])
    text = message.text or message.caption or "Реши это"
    img_b64 = None

    if message.content_type == 'photo':
        file_info = bot.get_file(message.photo[-1].file_id)
        file_data = bot.download_file(file_info.file_path)
        img_b64 = base64.b64encode(file_data).decode('utf-8')

    task = {'chat_id': message.chat.id, 'is_vip': is_vip, 'text': text, 'img': img_b64}
    
    if is_vip:
        vip_queue.put(task)
        q_size = vip_queue.qsize()
        bot.send_message(message.chat.id, f"🚀 VIP-запрос принят! Ты {q_size}-й в очереди VIP. Подожди около {q_size * 5} сек.")
    else:
        free_queue.put(task)
        q_size = free_queue.qsize()
        # Считаем примерно: 1 задача = 10-15 секунд
        wait_time = q_size * 15
        bot.send_message(message.chat.id, f"⏳ Задача в очереди. Ты {q_size}-й. Примерное время: {wait_time} сек.\nКупи /vip, чтобы быть первым!")

# --- ЗАПУСК БОТА ---
if __name__ == '__main__':
    print("🤖 Бот запущен и слушает очередь...")
    # infinity_polling сам перезапускает бота при ошибках сети
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
