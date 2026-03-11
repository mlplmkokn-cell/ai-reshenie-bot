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
from flask import Flask

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is active on Railway", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
FREE_GEMINI_KEY = os.getenv('FREE_GEMINI_KEY')
VIP_GEMINI_KEY = os.getenv('VIP_GEMINI_KEY')

bot = telebot.TeleBot(BOT_TOKEN)

# Очереди задач
free_queue = queue.Queue()
vip_queue = queue.Queue()

def init_db():
    conn = sqlite3.connect('data_v5.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        is_vip BOOLEAN DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

def ask_ai_direct(prompt, img_b64, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    parts = [{"text": f"Реши задачу: {prompt}"}]
    if img_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
    
    payload = {"contents": [{"parts": parts}]}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        res_json = response.json()
        return res_json['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"AI Error: {e}")
        return "❌ Ошибка нейросети. Попробуй еще раз."

def worker():
    print("Воркер запущен...")
    while True:
        task = None
        if not vip_queue.empty():
            task = vip_queue.get()
            key = VIP_GEMINI_KEY
        elif not free_queue.empty():
            task = free_queue.get()
            key = FREE_GEMINI_KEY
        
        if task:
            ans = ask_ai_direct(task['text'], task['img'], key)
            bot.send_message(task['chat_id'], ans)
            time.sleep(1)
        time.sleep(1)

threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Привет! Пришли задачу (текст или фото).")

@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    text = message.text or message.caption or "Реши задачу"
    img_b64 = None

    if message.content_type == 'photo':
        file_info = bot.get_file(message.photo[-1].file_id)
        file_data = bot.download_file(file_info.file_path)
        img_b64 = base64.b64encode(file_data).decode('utf-8')

    # Для теста всех кидаем в бесплатную очередь
    free_queue.put({'chat_id': message.chat.id, 'text': text, 'img': img_b64})
    bot.send_message(message.chat.id, "⏳ Задача принята. Решаю...")

if __name__ == '__main__':
    print("Бот начинает опрос Telegram...")
    bot.polling(none_stop=True)
