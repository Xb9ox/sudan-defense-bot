import os
import json
import logging
import asyncio
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import feedparser
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
)

# ============================================
# ⚙️ الإعدادات والتوكن
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA")
ADMIN_ID = 6414385813

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# 🌐 خادم لمنع إيقاف السيرفر (Keep-Alive Server)
# ============================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("🇸🇩 Sudan Defense & Minister News Monitoring Bot 24/7 is Active!".encode('utf-8'))

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"🌐 Server alive on port {port}")
    server.serve_forever()

# دالة آمنة لجلب الأخبار وتجاوز حظر المواقع
def fetch_rss_safe(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=12)
        if response.status_code == 200:
            return feedparser.parse(response.content)
    except Exception as e:
        logger.error(f"Error fetching RSS from {url}: {e}")
    return feedparser.parse("")

# ============================================
# 🗄️ إدارة الكلمات والمصادر الشاملة (سودانية وعالمية)
# ============================================
class DataManager:
    def __init__(self, filename="sudan_bot_data.json"):
        self.filename = filename
        self.data = self.load_data()

    def load_data(self):
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {
                "subscribers": {},
                "keywords": [
                    "وزير الدفاع السوداني", "وزارة الدفاع السودانية", "وزير دفاع السودان",
                    "حسن داؤود كبرون", "حسن داوود كبرون", "حسن كبرون", "الفريق حسن داؤود", "وزير الدفاع المكلف",
                    "القوات المسلحة السودانية", "الجيش السوداني", "هيئة العمليات", "القيادة العامة",
                    "البرهان", "حميدتي", "الدعم السريع", "الخرطوم", "بورتسودان",
                    "Sudan Defense Minister", "Sudan Defence Minister", "Hassan Dawood Kabroon",
                    "Sudan Ministry of Defense", "Sudanese Army", "Sudan Military", "SAF Sudan", "RSF Sudan"
                ],
                "sources": {
                    # 🇸🇩 مصادر وقنوات سودانية محليّة
                    "📻 راديو دبنقا": "https://www.dabangasudan.org/ar/feed",
                    "📰 صحيفة التغيير": "https://www.altagheer.info/ar/feed/",
                    "🗞️ سودان تربيون": "https://sudantribune.net/feed/",
                    "🌍 الجزيرة - ملف السودان": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8",
                    "📌 أخبار السودان العاجلة": "https://news.google.com/rss/search?q=%D8%A7%D9%84%D8%B3%D9%88%D8%AF%D8%A7%D9%86&hl=ar&gl=SD&ceid=SD:ar",
                    
                    # 🌍 صحافة ومنتديات عالمية
                    "📺 قناة الحدث": "https://www.alhadath.net/.mrss/ar.xml",
                    "📺 قناة العربية": "https://www.alarabiya.net/.mrss/ar.xml",
                    "🌐 BBC عربي": "http://feeds.bbci.co.uk/arabic/rss.xml",
                    "📺 سكاي نيوز": "https://www.skynewsarabia.com/web/rss",
                    "🇫🇷 فرانس 24": "https://www.france24.com/ar/rss",
                    "🇷🇺 RT عربي": "https://arabic.rt.com/rss/"
                },
                "seen_articles": [],
                "stats": {"total_alerts": 0, "start_date": datetime.now().strftime('%Y-%m-%d')}
            }

    def save_data(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Save error: {e}")

    def add_subscriber(self, chat_id, first_name, username):
        self.data["subscribers"][str(chat_id)] = {
            "first_name": first_name or "عضو",
            "username": username or "Unknown",
            "active": True
        }
        self.save_data()

# ============================================
# 🔍 محرك البحث الموسع
# ============================================
class SearchEngine:
    def __init__(self, dm):
        self.dm = dm

    def scan_all(self):
        found = []
        keywords = self.dm.data["keywords"]
        
        # 1. فحص جميع المصادر السودانية والعالمية
        for name, url in self.dm.data["sources"].items():
            try:
                feed = fetch_rss_safe(url)
                for entry in feed.entries[:15]:
                    art = self._check_entry(entry, name, keywords)
                    if art:
                        found.append(art)
            except Exception as e:
                logger.error(f"Error reading {name}: {e}")

        # 2. فحص Google News المباشر عن وزير الدفاع والجيش
        queries = [
            "وزير+الدفاع+السوداني",
            "حسن+داؤود+كبرون",
            "الجيش+السوداني",
            "Sudan+Defense+Minister"
        ]
        for q in queries:
            try:
                g_url = f"https://news.google.com/rss/search?q={q}&hl=ar&gl=SD&ceid=SD:ar"
                feed = fetch_rss_safe(g_url)
                for entry in feed.entries[:10]:
                    art = self._check_entry(entry, "رصد Google الإخباري", keywords)
                    if art:
                        found.append(art)
            except:
                pass

        return found

    def _check_entry(self, entry, source, keywords):
        link = entry.get('link', '')
        title = entry.get('title', '')
        summary = entry.get('summary', '')

        if not link or link in self.dm.data["seen_articles"]:
            return None

        full_text = f"{title} {summary}".lower()
        for kw in keywords:
            if kw.lower() in full_text:
                self.dm.data["seen_articles"].append(link)
                if len(self.dm.data["seen_articles"]) > 3000:
                    self.dm.data["seen_articles"] = self.dm.data["seen_articles"][-3000:]
                self.dm.save_data()
                return {
                    "title": title,
                    "link": link,
                    "source": source,
                    "keyword": kw,
                    "time": datetime.now().strftime('%H:%M:%S')
                }
        return None

# ============================================
# 🤖 التفاعل والأوامر
# ============================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    dm = context.bot_data['dm']
    dm.add_subscriber(chat_id, user.first_name, user.username)

    msg = f"""
🦅 **نظام الرصد الإخباري المباشر - السودان ووزارة الدفاع** 🇸🇩

مرحباً بك يا **{user.first_name}** في المنصة العاجلة لمتابعة أخبار السودان والقوات المسلحة.

📡 **التغطية تشمل الفحص الفوري من:**
راديو دبنقا • صحيفة التغيير • سودان تربيون • قناة الحدث • العربية • الجزيرة • BBC • فرانس 24.

🎯 **متابعة خاصة لـ:**
• التصريحات والقرارات الصادرة عن وزير الدفاع (حسن داؤود كبرون).
• البيانات الرسمية لوزارة الدفاع والجيش السوداني.
"""
    keyboard = [
        [InlineKeyboardButton("📰 أحدث الأخبار", callback_data="latest"), InlineKeyboardButton("📊 الإحصائيات", callback_data="stats")],
        [InlineKeyboardButton("🔑 الكلمات المراقبة", callback_data="keywords"), InlineKeyboardButton("📡 القنوات المربوطة", callback_data="sources")]
    ]
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔍 اكتب الكلمة المراد البحث عنها، مثال:\n`/search وزير الدفاع`", parse_mode='Markdown')
        return

    q = " ".join(context.args)
    await update.message.reply_text(f"🔍 جاري البحث المباشر عن: *{q}*...", parse_mode='Markdown')

    url = f"https://news.google.com/rss/search?q={q}&hl=ar&gl=SD&ceid=SD:ar"
    feed = await asyncio.to_thread(fetch_rss_safe, url)

    res = []
    for entry in feed.entries[:6]:
        res.append(f"📌 *{entry.title}*\n🔗 {entry.link}\n")

    if res:
        await update.message.reply_text(f"🔍 **نتائج البحث السريع:**\n\n" + "\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)
    else:
        await update.message.reply_text(f"❌ لم نجد نتائج حديثة لـ: *{q}*", parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dm = context.bot_data['dm']
    data = query.data

    if data == "stats":
        subs = len(dm.data["subscribers"])
        msg = f"📊 **المشتركين:** `{subs}` | **الكلمات:** `{len(dm.data['keywords'])}` | **التنبيهات:** `{dm.data['stats']['total_alerts']}`\n🟢 **حالة الرصد:** 24/7 شغال بدون توقف."
        await query.message.reply_text(msg, parse_mode='Markdown')

    elif data == "keywords":
        kws = "\n".join([f"• `{k}`" for k in dm.data["keywords"]])
        await query.message.reply_text(f"🔑 **الكلمات المراقبة حالياً:**\n\n{kws}", parse_mode='Markdown')

    elif data == "sources":
        srcs = "\n".join([f"✅ {s}" for s in dm.data["sources"].keys()])
        await query.message.reply_text(f"📡 **القنوات والمصادر النشطة:**\n\n{srcs}", parse_mode='Markdown')

    elif data == "latest":
        await query.edit_message_text("⏳ جاري جلب أحدث الأخبار السودانية والعالمية...")
        
        # جلب الأخبار من راديو دبنقا وقناة الحدث بآمان
        feed_sudan = await asyncio.to_thread(fetch_rss_safe, "https://www.dabangasudan.org/ar/feed")
        
        items = []
        if feed_sudan.entries:
            for e in feed_sudan.entries[:4]:
                items.append(f"🔹 [{e.title}]({e.link})")
                
        news_text = "\n\n".join(items) if items else "❌ تعذر جلب الأخبار، حاول مجدداً."
        
        keyboard = [[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="back_home")]]
        await query.edit_message_text(
            f"📰 **آخر الأحداث السودانية العاجلة (راديو دبنقا):**\n\n{news_text}", 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

    elif data == "back_home":
        keyboard = [
            [InlineKeyboardButton("📰 أحدث الأخبار", callback_data="latest"), InlineKeyboardButton("📊 الإحصائيات", callback_data="stats")],
            [InlineKeyboardButton("🔑 الكلمات المراقبة", callback_data="keywords"), InlineKeyboardButton("📡 القنوات المربوطة", callback_data="sources")]
        ]
        await query.edit_message_text("اختر من القائمة التالية:", reply_markup=InlineKeyboardMarkup(keyboard))

# ============================================
# 🔄 حلقة الرصد التلقائي في الخلفية (بدون JobQueue)
# ============================================
async def auto_monitor_loop(app: Application):
    await asyncio.sleep(5)  # انتظار بدء التشغيل
    logger.info("🔄 بدأت حلقة الرصد التلقائي 24/7...")
    
    while True:
        try:
            dm = app.bot_data['dm']
            engine = app.bot_data['engine']

            articles = await asyncio.to_thread(engine.scan_all)
            if articles:
                for art in articles:
                    dm.data["stats"]["total_alerts"] += 1
                    dm.save_data()

                    msg = f"""
🚨 **خبر عاجل - رصد فوري** 🇸🇩

📌 **{art['title']}**

📰 **المصدر:** {art['source']}
🔑 **الكلمة المطابقة:** `{art['keyword']}`
⏰ **وقت الرصد:** `{art['time']}`

🔗 [قراءة الخبر بالكامل من المصدر]({art['link']})
"""
                    for chat_id in list(dm.data["subscribers"].keys()):
                        try:
                            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                            await asyncio.sleep(0.05)
                        except Exception as e:
                            logger.error(f"Error sending alert to {chat_id}: {e}")
        except Exception as e:
            logger.error(f"Error in auto_monitor_loop: {e}")

        # تكرار الرصد كل 60 ثانية
        await asyncio.sleep(60)

async def post_init(application: Application):
    # تشغيل الرصد الخلفي تلقائياً فور بدء البوت
    asyncio.create_task(auto_monitor_loop(application))

# ============================================
# 🚀 التشغيل الرئيسي
# ============================================
def main():
    # 1. تشغيل سيرفر الويب في خيط منفصل
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    dm = DataManager()
    engine = SearchEngine(dm)

    # 2. بناء تطبيق التلجرام مع ربط دالة التشغيل التلقائي post_init
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.bot_data['dm'] = dm
    app.bot_data['engine'] = engine

    # 3. إسناد الأوامر
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("✅ البوت شغال ومتصل على Render!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
