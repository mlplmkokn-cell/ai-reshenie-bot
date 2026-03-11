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

# Официальная библиотека Google
import google.generativeai as genai

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running with Proxy Bypass", 200

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

# Цены
PRICE_TRIAL = 99.00
PRICE_REGULAR = 199.00

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
    conn = sqlite3.connect('data_v5.db')
    cursor = conn.cursor()
    cursor.execute("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return True if result and result[0] else False

def check_trial_used(user_id):
    conn = sqlite3.connect('data_v5.db')
    cursor = conn.cursor()
    cursor.execute("SELECT trial_used FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return True if result and result[0] else False

def ask_ai(prompt, base64_img, key, is_vip):
    try:
        # --- ПОПЫТКА ОБХОДА БЛОКИРОВКИ РЕГИОНА ---
        # Мы пробуем прокинуть запросы через прокси, если Render забанен
        # ВНИМАНИЕ: Если этот прокси упадет, Google снова выдаст ошибку региона.
        # В идеале здесь должен быть ваш платный прокси.
        os.environ['https_proxy'] = "http://167.172.189.231:80" 

        genai.configure(api_key=key)
        
        # Отключаем все фильтры "безопасности" Google
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        
        model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_settings)
        
        if is_vip:
            sys_prompt = "Ты — профессиональный репетитор. Реши задачу подробно с пояснениями."
        else:
            sys_prompt = "Дай только краткий ответ. В конце напиши: 'Для подробного решения купите VIP'."
            
        contents = [f"{sys_prompt}\nЗадание: {prompt}"]
        
        if base64_img:
            # Конвертируем обратно в байты для библиотеки Google
            image_data = base64.b64decode(base64_img)
            contents.append({
                "mime_type": "image/jpeg",
                "data": image_data
            })
            
        print(f">>> Запрос к Gemini (VIP: {is_vip})...")
        response = model.generate_content(contents)
        
        if response.text:
            return response.text
        return "❌ Нейросеть прислала пустой ответ."
            
    except Exception as e:
        error_msg = str(e)
        print(f"!!! Ошибка Google API: {error_msg}")
        
        if "location" in error_msg.lower():
            return "⚠️ Ошибка региона. Google блокирует сервер. Попробуйте еще раз или напишите позже (я чиню обход)."
        
        return "❌ Ошибка нейросети. Попробуйте отправить задачу еще раз."

@bot.message_handler(commands=['start'])
def start(message):
    conn = sqlite3.connect('data_v5.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, "Привет! Я Решала. 📚\nПришли фото или текст задачи — я помогу!")

@bot.message_handler(commands=['vip'])
def vip_command(message):
    user_id = message.from_user.id
    trial_used = check_trial_used(user_id)
    price = PRICE_REGULAR if trial_used else PRICE_TRIAL
    
    try:
        payment = Payment.create({
            "amount": {"value": str(price), "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://t.me/Speed_fotoGDZ_bot"},
            "capture": True,
            "description": "Покупка VIP"
        })
        
        conn = sqlite3.connect('data_v5.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO payments (payment_id, user_id) VALUES (?, ?)", (payment.id, user_id))
        conn.commit()
        conn.close()
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(text="💳 Оплатить", url=payment.confirmation.confirmation_url))
        markup.add(InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_{payment.id}"))
        
        bot.send_message(message.chat.id, f"💎 VIP за {int(price)}₽\n\nДаёт подробные решения и работу без очереди!", reply_markup=markup)
    except:
        bot.send_message(message.chat.id, "❌ Ошибка платежной системы.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment_callback(call):
    payment_id = call.data.split('_')[1]
    try:
        payment = Payment.find_one(payment_id)
        if payment.status == 'succeeded':
            conn = sqlite3.connect('data_v5.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET is_vip = 1, trial_used = 1 WHERE user_id = ?", (call.from_user.id,))
            conn.commit()
            conn.close()
            bot.edit_message_text("✅ VIP активирован! Теперь присылай задачи.", call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "Оплата не найдена.", show_alert=True)
    except:
        bot.answer_callback_query(call.id, "Ошибка проверки.")

@bot.message_handler(content_types=['text', 'photo'])
def handle_all(message):
    user_id = message.from_user.id
    is_vip = check_vip(user_id)
    
    text = message.text or message.caption or "Реши задачу"
    img_b64 = None

    if message.content_type == 'photo':
        status = bot.reply_to(message, "⏳ Обрабатываю...")
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            file_data = bot.download_file(file_info.file_path)
            img_b64 = base64.b64encode(file_data).decode('utf-8')
            bot.delete_message(message.chat.id, status.message_id)
        except:
            bot.edit_message_text("❌ Ошибка загрузки картинки.", message.chat.id, status.message_id)
            return

    task = {'chat_id': message.chat.id, 'is_vip': is_vip, 'text': text, 'img': img_b64}
    
    if is_vip:
        vip_queue.put(task)
        bot.send_message(message.chat.id, "🚀 VIP: Решаю вне очереди...")
    else:
        free_queue.put(task)
        bot.send_message(message.chat.id, "⏳ Задача в очереди.")

def worker():
    while True:
        try:
            task = None
            if not vip_queue.empty():
                task = vip_queue.get()
                key = VIP_GEMINI_KEY
            elif not free_queue.empty():
                task = free_queue.get()
                key = FREE_GEMINI_KEY
            
            if task:
                ans = ask_ai(task['text'], task['img'], key, task['is_vip'])
                bot.send_message(task['chat_id'], ans)
                time.sleep(2)
        except Exception as e:
            print(f"Ошибка воркера: {e}")
        time.sleep(1)

threading.Thread(target=worker, daemon=True).start()

if __name__ == '__main__':
    print("Бот запущен...")
    bot.polling(none_stop=True)
