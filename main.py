import os
import time
import threading
import queue
import sqlite3
import base64
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from yookassa import Configuration, Payment
from flask import Flask
import google.generativeai as genai

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
SHOP_ID = os.getenv('SHOP_ID')
SHOP_API_KEY = os.getenv('SHOP_API_KEY')
FREE_GEMINI_KEY = os.getenv('FREE_GEMINI_KEY')
VIP_GEMINI_KEY = os.getenv('VIP_GEMINI_KEY')

Configuration.account_id = SHOP_ID
Configuration.secret_key = SHOP_API_KEY

bot = telebot.TeleBot(BOT_TOKEN)

# Очереди задач
free_queue = queue.Queue()
vip_queue = queue.Queue()

def init_db():
    conn = sqlite3.connect('data_v5.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        is_vip BOOLEAN DEFAULT 0,
                        trial_used BOOLEAN DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS payments (
                        payment_id TEXT PRIMARY KEY,
                        user_id INTEGER)''')
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

def ask_ai(prompt, base64_img, key, is_vip):
    try:
        # Пытаемся обойти блокировку региона через прокси
        os.environ['https_proxy'] = "http://167.172.189.231:80" 
        
        genai.configure(api_key=key)
        
        # ВНИМАНИЕ: Используем стабильную версию имени модели
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        sys_prompt = "Ты репетитор. Реши задачу." if is_vip else "Дай краткий ответ. Для подробного купи VIP."
        contents = [f"{sys_prompt}\nЗадание: {prompt}"]
        
        if base64_img:
            image_data = base64.b64decode(base64_img)
            contents.append({"mime_type": "image/jpeg", "data": image_data})
            
        # Устанавливаем таймаут, чтобы воркер не зависал вечно
        response = model.generate_content(contents)
        return response.text if response.text else "❌ Нейросеть промолчала."

    except Exception as e:
        error_text = str(e)
        print(f"!!! Ошибка воркера AI: {error_text}")
        if "location" in error_text.lower():
            return "⚠️ Ошибка региона (Google блокирует этот сервер). Попробуйте позже."
        return f"❌ Техническая ошибка: {error_text[:50]}..."

def worker():
    print("--- Воркер запущен и слушает очереди ---")
    while True:
        task = None
        try:
            # Сначала проверяем VIP очередь
            if not vip_queue.empty():
                task = vip_queue.get()
                key = VIP_GEMINI_KEY
                print(f"Обработка VIP задачи для {task['chat_id']}")
            elif not free_queue.empty():
                task = free_queue.get()
                key = FREE_GEMINI_KEY
                print(f"Обработка бесплатной задачи для {task['chat_id']}")
            
            if task:
                result = ask_ai(task['text'], task['img'], key, task['is_vip'])
                bot.send_message(task['chat_id'], result)
                print(f"✅ Задача для {task['chat_id']} выполнена.")
                
        except Exception as e:
            print(f"ОШИБКА В ЦИКЛЕ ВОРКЕРА: {e}")
            if task:
                bot.send_message(task['chat_id'], "❌ Произошел сбой при обработке вашей задачи.")
        
        time.sleep(1) # Небольшая пауза, чтобы не грузить процессор

# Запуск потока-обработчика
threading.Thread(target=worker, daemon=True).start()

@bot.message_handler(commands=['start'])
def start(message):
    conn = sqlite3.connect('data_v5.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Привет! Пришли фото или текст — я решу!")

@bot.message_handler(content_types=['text', 'photo'])
def handle_all(message):
    user_id = message.from_user.id
    is_vip = check_vip(user_id)
    
    text = message.text or message.caption or "Реши задачу"
    img_b64 = None

    if message.content_type == 'photo':
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            file_data = bot.download_file(file_info.file_path)
            img_b64 = base64.b64encode(file_data).decode('utf-8')
        except:
            bot.reply_to(message, "❌ Ошибка загрузки фото.")
            return

    task = {'chat_id': message.chat.id, 'is_vip': is_vip, 'text': text, 'img': img_b64}
    
    if is_vip:
        vip_queue.put(task)
        bot.send_message(message.chat.id, "🚀 VIP: Задача принята, решаю без очереди!")
    else:
        free_queue.put(task)
        bot.send_message(message.chat.id, "⏳ Задача в очереди. Для мгновенного решения — /vip.")

if __name__ == '__main__':
    print("Бот стартовал...")
    bot.polling(none_stop=True)
