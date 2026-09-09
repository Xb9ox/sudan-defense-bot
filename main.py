import os
import logging
import threading
import time
import asyncio
import feedparser
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# --- 1. إعدادات السجلات للتحقق من الأخطاء ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. سيرفر الويب (ضروري جداً لمنع توقف Render) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"✅ Web Server started on port {port}")
    server.serve_forever()

# تشغيل السيرفر في خيط منفصل فوراً
threading.Thread(target=run_web_server, daemon=True).start()

# --- 3. بيانات البوت والمصادر ---
TOKEN = "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA"

NEWS_SOURCES = {
    "dabanga": {"name": "📻 راديو دبنقا (السودان)", "url": "https://www.dabangasudan.org/ar/feed"},
    "tagheer": {"name": "📰 صحيفة التغيير", "url": "https://www.altagheer.info/ar/feed/"},
    "sudan_tribune": {"name": "🗞️ سودان تربيون", "url": "https://sudantribune.net/feed/"},
    "aj_sudan": {"name": "🌍 الجزيرة سودان", "url": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8"},
    "bbc_arabic": {"name": "🌐 BBC العربية", "url": "http://feeds.bbci.co.uk/arabic/rss.xml"},
    "skynews": {"name": "📺 سكاي نيوز", "url": "https://www.skynewsarabia.com/rss.xml"}
}

# دالة جلب الأخبار المعدلة (تستخدم Requests لتفادي الحظر)
def get_news(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=15)
        feed = feedparser.parse(response.content)
        
        if not feed.entries:
            return "❌ لا توجد أخبار متاحة الآن من هذا المصدر."
            
        news_list = []
        for entry in feed.entries[:5]: # آخر 5 أخبار
            title = entry.title
            link = entry.link
            news_list.append(f"🔹 **{title}**\n🔗 [إقرأ المزيد]({link})")
        
        return "\n\n".join(news_list)
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        return "⚠️ حدث خطأ أثناء جلب الأخبار. حاول مرة أخرى."

# --- 4. أوامر البوت ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇸🇩 أخبار السودان المحلية", callback_data="sudan_news")],
        [InlineKeyboardButton("🌍 الصحافة العالمية", callback_data="global_news")],
        [InlineKeyboardButton("⚡ عاجل الآن", callback_data="fast_news")]
    ]
    await update.message.reply_text(
        "مرحباً بك في بوت أخبار السودان والعالم 🇸🇩🌍\nاختر القسم المطلوب:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "sudan_news":
        keys = [
            [InlineKeyboardButton("راديو دبنقا", callback_data="fetch_dabanga")],
            [InlineKeyboardButton("صحيفة التغيير", callback_data="fetch_tagheer")],
            [InlineKeyboardButton("سودان تربيون", callback_data="fetch_sudan_tribune")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.edit_message_text("🇸🇩 اختر المصدر السوداني:", reply_markup=InlineKeyboardMarkup(keys))

    elif query.data == "global_news":
        keys = [
            [InlineKeyboardButton("BBC العربية", callback_data="fetch_bbc_arabic")],
            [InlineKeyboardButton("سكاي نيوز", callback_data="fetch_skynews")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        await query.edit_message_text("🌍 اختر المصدر العالمي:", reply_markup=InlineKeyboardMarkup(keys))

    elif query.data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🇸🇩 أخبار السودان المحلية", callback_data="sudan_news")],
            [InlineKeyboardButton("🌍 الصحافة العالمية", callback_data="global_news")]
        ]
        await query.edit_message_text("اختر القسم المطلوب:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("fetch_"):
        source_key = query.data.replace("fetch_", "")
        source = NEWS_SOURCES.get(source_key)
        
        await query.edit_message_text(f"⏳ جاري جلب آخر الأخبار من {source['name']}...")
        
        # تنفيذ جلب الأخبار في خيط منفصل لعدم تجميد البوت
        loop = asyncio.get_event_loop()
        news_content = await loop.run_in_executor(None, get_news, source['url'])
        
        back_key = [[InlineKeyboardButton("🔙 العودة للقائمة", callback_data="main_menu")]]
        await query.edit_message_text(
            f"📰 **{source['name']}**:\n\n{news_content}",
            reply_markup=InlineKeyboardMarkup(back_key),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

    elif query.data == "fast_news":
        await query.edit_message_text("⚡ جاري جلب أهم الأنباء السودانية العاجلة...")
        news_content = await asyncio.get_event_loop().run_in_executor(None, get_news, NEWS_SOURCES['aj_sudan']['url'])
        await query.edit_message_text(f"⚡ **عاجل السودان:**\n\n{news_content}", parse_mode="Markdown", disable_web_page_preview=True)

# --- 5. تشغيل التطبيق ---
def main():
    try:
        # بناء التطبيق بدون استخدام JobQueue بشكل مباشر لحل مشكلة NoneType
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_buttons))

        logger.info("🚀 Bot is starting polling...")
        app.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"💥 Fatal Error: {e}")

if __name__ == "__main__":
    main()
