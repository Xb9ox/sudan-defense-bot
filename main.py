import os
import json
import logging
import asyncio
import threading
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
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    logger.info(f"🌐 Server alive on port {port}")
    server.serve_forever()

# ============================================
# 🗄️ إدارة الكلمات والمصادر الشاملة
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
                    # تخص وزير الدفاع باسمه وصفته
                    "وزير الدفاع السوداني", "وزارة الدفاع السودانية", "وزير دفاع السودان",
                    "حسن داؤود كبرون", "حسن داوود كبرون", "حسن كبرون", "الفريق حسن داؤود", "وزير الدفاع المكلف",
                    # الكلمات العسكرية العامة
                    "القوات المسلحة السودانية", "الجيش السوداني", "هيئة العمليات", "القيادة العامة",
                    "البرهان", "حميدتي", "الدعم السريع", "الخرطوم", "بورتسودان",
                    # إنجليزي
                    "Sudan Defense Minister", "Sudan Defence Minister", "Hassan Dawood Kabroon",
                    "Sudan Ministry of Defense", "Sudanese Army", "Sudan Military", "SAF Sudan", "RSF Sudan"
                ],
                "sources": {
                    "قناة الحدث": "https://www.alhadath.net/.mrss/ar.xml",
                    "قناة العربية": "https://www.alarabiya.net/.mrss/ar.xml",
                    "قناة الجزيرة": "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/73d0e1b4-532f-45ef-b135-bfdff8b8cab9",
                    "سكاي نيوز عربية": "https://www.skynewsarabia.com/web/rss",
                    "الشرق للأخبار": "https://asharq.com/rss/",
                    "BBC عربي": "http://feeds.bbci.co.uk/arabic/rss.xml",
                    "فرانس 24": "https://www.france24.com/ar/rss",
                    "سودان تريبيون": "https://sudantribune.com/feed/",
                    "RT عربي": "https://arabic.rt.com/rss/"
                },
                "seen_articles": [],
                "stats": {"total_alerts": 0, "start_date": datetime.now().strftime('%Y-%m-%d')}
            }

    def save_data(self):
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

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
        
        # 1. فحص مصادر القنوات الإخبارية (الحدث، العربية، الجزيرة، وغيرها)
        for name, url in self.dm.data["sources"].items():
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:20]:
                    art = self._check_entry(entry, name, keywords)
                    if art:
                        found.append(art)
            except Exception as e:
                logger.error(f"Error reading {name}: {e}")

        # 2. فحص Google News المباشر عن وزير الدفاع بالاسم والرتبة
        queries = [
            "وزير+الدفاع+السوداني",
            "حسن+داؤود+كبرون",
            "حسن+داوود+كبرون",
            "وزارة+الدفاع+السودانية",
            "الجيش+السوداني",
            "Sudan+Defense+Minister"
        ]
        for q in queries:
            try:
                g_url = f"https://news.google.com/rss/search?q={q}&hl=ar&gl=SD&ceid=SD:ar"
                feed = feedparser.parse(g_url)
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

        if link in self.dm.data["seen_articles"]:
            return None

        full_text = f"{title} {summary}".lower()
        for kw in keywords:
            if kw.lower() in full_text:
                self.dm.data["seen_articles"].append(link)
                if len(self.dm.data["seen_articles"]) > 4000:
                    self.dm.data["seen_articles"] = self.dm.data["seen_articles"][-4000:]
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
🦅 **نظام الرصد الإخباري المباشر - وزارة الدفاع السودانية** 🇸🇩

مرحباً بك يا **{user.first_name}** في المنصة العاجلة لمتابعة أخبار وزارة الدفاع والقوات المسلحة.

📡 **التغطية تشمل الفحص الفوري من:**
قناة الحدث • العربية • الجزيرة • الشرق • سكاي نيوز • سودان تريبيون • وكالات عالمية.

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
    feed = feedparser.parse(url)

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
        await query.message.reply_text("⏳ جاري جلب أحدث الأخبار...")
        feed = feedparser.parse("https://www.alhadath.net/.mrss/ar.xml")
        items = [f"• {e.title}\n🔗 {e.link}" for e in feed.entries[:3]]
        await query.message.reply_text("📰 **آخر أخبار قناة الحدث:**\n\n" + "\n\n".join(items), disable_web_page_preview=True)

# ============================================
# 🔄 حلقة الرصد التلقائي (كل 60 ثانية)
# ============================================
async def auto_monitor(context: ContextTypes.DEFAULT_TYPE):
    dm = context.bot_data['dm']
    engine = context.bot_data['engine']

    articles = engine.scan_all()
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
                    await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode='Markdown')
                    await asyncio.sleep(0.05)
                except:
                    pass

# ============================================
# 🚀 التشغيل الرئيسي
# ============================================
def main():
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    dm = DataManager()
    engine = SearchEngine(dm)

    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data['dm'] = dm
    app.bot_data['engine'] = engine

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.job_queue.run_repeating(auto_monitor, interval=60, first=10)

    logger.info("✅ البوت شغال ومتصل على Render!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
