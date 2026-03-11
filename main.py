import os, time, threading, queue, requests, base64, telebot
from dotenv import load_dotenv
from flask import Flask

# Запуск Flask для Railway (чтобы сервис не засыпал)
app = Flask(__name__)
@app.route('/')
def index(): return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

load_dotenv()
bot = telebot.TeleBot(os.getenv('BOT_TOKEN'))
AI_KEY = os.getenv('FREE_GEMINI_KEY')

def ask_ai(prompt, img_b64):
    # Используем проверенную ссылку и модель
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={AI_KEY}"
    
    parts = [{"text": prompt}]
    if img_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": img_b64}})
    
    payload = {"contents": [{"parts": parts}]}
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        res_data = response.json()
        if 'candidates' in res_data:
            return res_data['candidates'][0]['content']['parts'][0]['text']
        return f"⚠️ Ошибка нейросети: {res_data.get('error', {}).get('message', 'Неизвестно')}"
    except Exception as e:
        return f"❌ Ошибка связи: {e}"

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Бот готов! Пришли фото задачи или текст.")

@bot.message_handler(content_types=['text', 'photo'])
def handle(message):
    chat_id = message.chat.id
    text = message.text or message.caption or "Реши задачу на фото"
    img_b64 = None
    
    if message.content_type == 'photo':
        file_info = bot.get_file(message.photo[-1].file_id)
        file_data = bot.download_file(file_info.file_path)
        img_b64 = base64.b64encode(file_data).decode('utf-8')
    
    bot.send_message(chat_id, "⏳ Думаю...")
    
    # Запускаем решение в отдельном потоке, чтобы бот не тормозил
    def process():
        answer = ask_ai(text, img_b64)
        bot.send_message(chat_id, answer)
    
    threading.Thread(target=process).start()

if __name__ == '__main__':
    print("Бот запущен!")
    bot.polling(none_stop=True)
