import os
import time
import threading
import queue
import sqlite3
import requests
import base64
import telebot
from dotenv import load_dotenv
from flask import Flask

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is alive", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
FREE_KEY = os.getenv('FREE_GEMINI_KEY')
VIP_KEY = os.getenv('VIP_GEMINI_KEY')

bot = telebot.TeleBot(BOT_TOKEN)
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

def check_vip(user_id):
    try:
        conn = sqlite3.connect('data_v5.db')
        cursor = conn.cursor()
        cursor.execute("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return True if result and result[0] else False
    except: return False

def ask_ai_direct(prompt, img_b64, api_key, is_vip):
    # ОБНОВЛЕННАЯ ССЫЛКА (v1 вместо v1beta)
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    
    sys_instruction = "Ты профессиональный репетитор. Реши задачу подробно." if is_vip else "Дай только краткий ответ. Для подробного решения купите VIP."
    
    parts = [{"text": f"{sys_instruction}\nЗадание: {prompt}"}]
    if img_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
    
    payload = {"contents": [{"parts": parts}]}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=40)
        res_json = response.json()
        if 'error' in res_json:
            return f"⚠️ Ошибка нейросети: {res_json['error']['message']}"
        return res_json['candidates'][0]['content']['parts'][0]['text']
    except Exception:
        return "❌ Техническая ошибка. Попробуйте позже."

def worker():
    while True:
        task = None
        if not vip_queue.empty():
            task = vip_queue.get()
            key = VIP_KEY
        elif not free_queue.empty():
            task = free_queue.get()
            key = FREE_KEY
        
        if task:
            ans = ask_ai_direct(task['text'], task['img'], key, task['is_vip'])
            bot.send_message(task['chat_id'], ans)
        time.sleep(1)

threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Привет! Я Решала. 📚\nПришли фото или текст задачи — я помогу!")

@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    user_id = message.from_user.id
    is_vip = check_vip(user_id)
    text = message.text or message.caption or "Реши задачу"
    img_b64 = None

    if message.content_type == 'photo':
        file_info = bot.get_file(message.photo[-1].file_id)
        file_data = bot.download_file(file_info.file_path)
        img_b64 = base64.b64encode(file_data).decode('utf-8')

    task = {'chat_id': message.chat.id, 'is_vip': is_vip, 'text': text, 'img': img_b64}
    
    if is_vip:
        vip_queue.put(task)
        bot.send_message(message.chat.id, "🚀 VIP: Решаю максимально подробно...")
    else:
        free_queue.put(task)
        bot.send_message(message.chat.id, "⏳ Задача в очереди. Для мгновенного решения купите /vip.")

if __name__ == '__main__':
    bot.polling(none_stop=True)
