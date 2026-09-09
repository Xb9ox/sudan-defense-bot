import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import asyncio
import feedparser
import urllib.request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# 1. إعدادات السجل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 2. سيرفر الويب لإبقاء Render شغال 24/7
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Running Successfully!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌐 Web Server alive on port {port}")
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# 3. توكن البوت الخاص بك
TOKEN = "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA"

# 4. مصادر الأخبار السودانية والعالمية
NEWS_FEEDS = {
    "sudan_dabanga": {"name": "📻 راديو دبنقا", "url": "https://www.dabangasudan.org/ar/feed"},
    "sudan_altagheer": {"name": "📰 صحيفة التغيير السودانية", "url": "https://www.altagheer.info/ar/feed/"},
    "sudan_tribune": {"name": "🗞️ سودان تربيون", "url": "https://sudantribune.net/feed/"},
    "sudan_aj": {"name": "🌍 الجزيرة - السودان", "url": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8"},
    "global_bbcarabic": {"name": "🌐 BBC العربي", "url": "http://feeds.bbci.co.uk/arabic/rss.xml"},
    "global_sky": {"name": "📺 سكاي نيوز عربية", "url": "https://www.skynewsarabia.com/rss.xml"}
}

def fetch_news(rss_url):
    try:
        req = urllib.request.Request(
            rss_url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        html = urllib.request.urlopen(req, timeout=10).read()
        feed = feedparser.parse(html)
        
        results = []
        for entry in feed.entries[:5]: 
            title = entry.get('title', 'بدون عنوان')
            link = entry.get('link', '#')
            results.append(f"🔹 **[{title}]({link})**")
            
        return "\n\n".join(results) if results else "❌ لا توجد أخبار حالياً."
    except Exception as e:
        return "⚠️ المصدر لا يستجيب حالياً، حاول لاحقاً."

# 5. الأوامر
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇸🇩 أخبار السودان", callback_data="menu_sudan")],
        [InlineKeyboardButton("🌍 صحافة عالمية", callback_data="menu_global")],
        [InlineKeyboardButton("⚡ عاجل الآن", callback_data="latest_all")]
    ]
    await update.message.reply_text(
        "مرحباً بك في بوت أخبار السودان والعالم 🇸🇩🌍\nاختر القسم:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "menu_sudan":
        keyboard = [
            [InlineKeyboardButton("📻 راديو دبنقا", callback_data="feed_sudan_dabanga")],
            [InlineKeyboardButton("📰 صحيفة التغيير", callback_data="feed_sudan_altagheer")],
            [InlineKeyboardButton("🗞️ سودان تربيون", callback_data="feed_sudan_tribune")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
        ]
        await query.edit_message_text("🇸🇩 **مصادر السودان:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "menu_global":
        keyboard = [
            [InlineKeyboardButton("🌐 BBC عربية", callback_data="feed_global_bbcarabic")],
            [InlineKeyboardButton("📺 سكاي نيوز", callback_data="feed_global_sky")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
        ]
        await query.edit_message_text("🌍 **مصادر عالمية:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif query.data == "back_main":
        keyboard = [[InlineKeyboardButton("🇸🇩 أخبار السودان", callback_data="menu_sudan")], [InlineKeyboardButton("🌍 صحافة عالمية", callback_data="menu_global")]]
        await query.edit_message_text("اختر القسم:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("feed_"):
        feed_key = query.data.replace("feed_", "")
        source = NEWS_FEEDS[feed_key]
        await query.edit_message_text(f"⏳ جاري جلب الأخبار من {source['name']}...")
        news_text = await asyncio.to_thread(fetch_news, source['url'])
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
        await query.edit_message_text(f"📰 **{source['name']}:**\n\n{news_text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown", disable_web_page_preview=True)

    elif query.data == "latest_all":
        await query.edit_message_text("⏳ جاري جلب آخر الأحداث...")
        news_text = await asyncio.to_thread(fetch_news, NEWS_FEEDS["sudan_dabanga"]["url"])
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
        
