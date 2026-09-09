import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import asyncio
import feedparser
import urllib.request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# 1. إعدادات السجل (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 2. سيرفر الويب الوهمي لإبقاء Render شغال 24/7
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Server alive!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌐 Web Server alive on port {port}")
    server.serve_forever()

# تشغيل السيرفر في خلفية النظام
threading.Thread(target=run_web_server, daemon=True).start()

# 3. توكن البوت من المتغيرات البيئية أو كتابته مباشرة
TOKEN = os.environ.get("TELEGRAM_TOKEN", "ضع_التوكن_هنا_إذا_لم_تستخدم_المتغيرات")

# 4. مصادر الأخبار السودانية والعالمية (روابط RSS موثوقة)
NEWS_FEEDS = {
    # الأخبار السودانية
    "sudan_dabanga": {"name": "📻 راديو دبنقا (السودان)", "url": "https://www.dabangasudan.org/ar/feed"},
    "sudan_altagheer": {"name": "📰 صحيفة التغيير السودانية", "url": "https://www.altagheer.info/ar/feed/"},
    "sudan_tribune": {"name": "🗞️ سودان تربيون", "url": "https://sudantribune.net/feed/"},
    "sudan_aj": {"name": "🌍 الجزيرة - أخبار السودان", "url": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8"},
    
    # الأخبار العالمية
    "global_bbcarabic": {"name": "🌐 BBC العربي", "url": "http://feeds.bbci.co.uk/arabic/rss.xml"},
    "global_sky": {"name": "📺 سكاي نيوز عربية", "url": "https://www.skynewsarabia.com/rss.xml"}
}

# دالة جلب الأخبار بأمان لتجنب الحظر
def fetch_news(rss_url):
    try:
        req = urllib.request.Request(
            rss_url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        html = urllib.request.urlopen(req, timeout=10).read()
        feed = feedparser.parse(html)
        
        results = []
        for entry in feed.entries[:5]: # جلب آخر 5 أخبار
            title = entry.get('title', 'بدون عنوان')
            link = entry.get('link', '#')
            results.append(f"🔹 **[{title}]({link})**")
            
        return "\n\n".join(results) if results else "❌ لم يتم العثور على أخبار حالياً."
    except Exception as e:
        logging.error(f"Error fetching {rss_url}: {e}")
        return "⚠️ تعذر جلب الأخبار من هذا المصدر حالياً، يرجى المحاولة لاحقاً."

# 5. أوامر البوت (Commands & Handlers)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🇸🇩 أخبار السودان المحلية", callback_data="menu_sudan")],
        [InlineKeyboardButton("🌍 المنتديات والصحافة العالمية", callback_data="menu_global")],
        [InlineKeyboardButton("⚡ آخر الأحداث العاجلة", callback_data="latest_all")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "مرحباً بك في بوت الأخبار السودانية والعالمية 📰✨\nاختر القسم الذي تريد متابعته:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data

    if data == "menu_sudan":
        keyboard = [
            [InlineKeyboardButton("📻 راديو دبنقا", callback_data="feed_sudan_dabanga")],
            [InlineKeyboardButton("📰 صحيفة التغيير", callback_data="feed_sudan_altagheer")],
            [InlineKeyboardButton("🗞️ سودان تربيون", callback_data="feed_sudan_tribune")],
            [InlineKeyboardButton("🌍 الجزيرة (ملف السودان)", callback_data="feed_sudan_aj")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_main")]
        ]
        await query.edit_message_text("🇸🇩 **اختر المصدر الصحفي السوداني:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "menu_global":
        keyboard = [
            [InlineKeyboardButton("🌐 BBC عربية", callback_data="feed_global_bbcarabic")],
            [InlineKeyboardButton("📺 سكاي نيوز عربية", callback_data="feed_global_sky")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_main")]
        ]
        await query.edit_message_text("🌍 **اختر القناة / المنتدى العالمي:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data == "back_main":
        keyboard = [
            [InlineKeyboardButton("🇸🇩 أخبار السودان المحلية", callback_data="menu_sudan")],
            [InlineKeyboardButton("🌍 المنتديات والصحافة العالمية", callback_data="menu_global")],
            [InlineKeyboardButton("⚡ آخر الأحداث العاجلة", callback_data="latest_all")]
        ]
        await query.edit_message_text("مرحباً بك في بوت الأخبار السودانية والعالمية 📰✨\nاختر القسم الذي تريد متابعته:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("feed_"):
        feed_key = data.replace("feed_", "")
        if feed_key in NEWS_FEEDS:
            source = NEWS_FEEDS[feed_key]
            await query.edit_message_text(f"⏳ جاري جلب آخر الأخبار من: **{source['name']}**...", parse_mode="Markdown")
            
            # جلب الأخبار بدون إيقاف السيرفر
            news_text = await asyncio.to_thread(fetch_news, source['url'])
            
            keyboard = [[InlineKeyboardButton("🔙 العودة", callback_data="back_main")]]
            await query.edit_message_text(
                f"📰 **آخر أخبار {source['name']}:**\n\n{news_text}", 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

    elif data == "latest_all":
        await query.edit_message_text("⏳ جاري جلب آخر الأحداث السودانية العاجلة...")
        news_text = await asyncio.to_thread(fetch_news, NEWS_FEEDS["sudan_dabanga"]["url"])
        keyboard = [[InlineKeyboardButton("🔙 العودة", callback_data="back_main")]]
        await query.edit_message_text(
            f"⚡ **آخر الأحداث العاجلة:**\n\n{news_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

# 6. التشغيل الرئيسي
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    # معالجة الموقت التلقائي بأمان لتجنب التوقف
    if app.job_queue:
        logging.info("JobQueue is active.")
    else:
        logging.warning("JobQueue is not available, but bot will work fine.")

    print("🤖 Bot is running successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
