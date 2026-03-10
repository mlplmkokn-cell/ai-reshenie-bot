import telebot
from telebot import types
from yookassa import Configuration, Payment
import sqlite3
import datetime
import requests
import uuid
import os
import time
from dotenv import load_dotenv

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
SHOP_ID = os.getenv('SHOP_ID')
SHOP_API_KEY = os.getenv('SHOP_API_KEY')
FREE_GEMINI_KEY = os.getenv('FREE_GEMINI_KEY')
VIP_GEMINI_KEY = os.getenv('VIP_GEMINI_KEY')
BOT_USERNAME = os.getenv('BOT_USERNAME')
DATABASE_URL = os.getenv('DATABASE_URL')
MAX_RPM = 15 # Лимит запросов в минуту

Configuration.configure(SHOP_ID, SHOP_API_KEY)
bot = telebot.TeleBot(BOT_TOKEN)

# --- УМНЫЙ КОНТРОЛЛЕР ОЧЕРЕДИ ---
class GlobalQueue:
    def __init__(self, limit):
        self.limit = limit
        self.history = [] # Список таймстампов запросов

    def get_wait_time(self):
        now = time.time()
        # Убираем всё, что было больше минуты назад
        self.history = [t for t in self.history if now - t < 60]
        
        if len(self.history) < self.limit:
            return 0 # Место есть
        
        # Если мест нет, вычисляем, когда освободится самое старое
        oldest_request = self.history[0]
        wait_time = int((oldest_request + 60) - now)
        return wait_time if wait_time > 0 else 1

    def add_request(self):
        self.history.append(time.time())

# Два независимых менеджера очереди
free_queue = GlobalQueue(MAX_RPM)
vip_queue = GlobalQueue(MAX_RPM)

# --- БАЗА ДАННЫХ (Для VIP) ---
def db_query(query, params=(), fetchone=False, commit=False):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(query, params)
    if commit: conn.commit()
    res = cursor.fetchone() if fetchone else cursor.fetchall()
    conn.close()
    return res

def init_db():
    db_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_vip INTEGER DEFAULT 0, vip_until TEXT)", commit=True)
    db_query("CREATE TABLE IF NOT EXISTS payments (payment_id TEXT PRIMARY KEY, user_id INTEGER)", commit=True)

# --- РАБОТА С GEMINI ---
def ask_ai(prompt, key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
    try:
        res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
        return res.json()['candidates'][0]['content']['parts'][0]['text']
    except:
        return "❌ Ошибка нейросети. Попробуй еще раз через пару секунд."

# --- ОБРАБОТЧИКИ ---

@bot.message_handler(commands=['start'])
def start(message):
    db_query("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,), commit=True)
    bot.send_message(message.chat.id, ("""Привет!
    Присылай фото или текст, я всё решу. 🚀\n\n"
В зависимости от загруженности я могу ответить в течении двух минут.
Если хочешь мгновенных ответов без очереди — попробуй наш VIP-режим!""")

@bot.message_handler(commands=['vip'])
def buy_vip(message):
    uid = message.from_user.id
    pay = Payment.create({
        "amount": {"value": "199.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": "https://t.me/ТВОЙ_ЮЗЕРНЕЙМ_БОТА"},
        "capture": True, "description": "VIP доступ"
    }, str(uuid.uuid4()))
    
    db_query("INSERT INTO payments VALUES (?, ?)", (pay.id, uid), commit=True)
    
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("💳 Оплатить 199₽", url=pay.confirmation.confirmation_url))
    markup.add(types.InlineKeyboardButton("✅ Проверить", callback_data=f"check_{pay.id}"))
    bot.send_message(message.chat.id, "💎 **VIP-статус** дает доступ к менее загруженному каналу очереди!", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith('check_'))
def check(call):
    pid = call.data.split('_')[1]
    if Payment.find_one(pid).status == 'succeeded':
        until = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        db_query("UPDATE users SET is_vip=1, vip_until=? WHERE user_id=?", (until, call.from_user.id), commit=True)
        bot.edit_message_text("🎉 VIP активирован!", call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "Оплата не найдена", show_alert=True)

@bot.message_handler(func=lambda m: True)
def handle(message):
    uid = message.from_user.id
    user = db_query("SELECT is_vip, vip_until FROM users WHERE user_id=?", (uid,), fetchone=True)
    
    is_vip = 0
    if user and user[0] == 1:
        if datetime.datetime.strptime(user[1], '%Y-%m-%d %H:%M:%S') > datetime.datetime.now():
            is_vip = 1

    # Выбираем очередь и ключ
    queue = vip_queue if is_vip else free_queue
    key = VIP_GEMINI_KEY if is_vip else FREE_GEMINI_KEY

    # Проверяем очередь
    wait = queue.get_wait_time()
    if wait > 0:
        bot.reply_to(message, f"⏳ Очередь заполнена! Подожди **{wait} сек.**\n\n" + 
                              ("" if is_vip else "💎 У VIP-пользователей своя, менее загруженная очередь! `/vip`"))
        return

    # Если прошли очередь — регистрируем запрос и идем к ИИ
    queue.add_request()
    status = bot.reply_to(message, "💬 Думаю...")
    
    answer = ask_ai(message.text, key)
    bot.edit_message_text(answer, message.chat.id, status.message_id)

if __name__ == '__main__':
    init_db()
    bot.polling(none_stop=True)
