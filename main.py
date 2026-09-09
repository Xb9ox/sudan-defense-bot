import os
import json
import logging
import asyncio
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import feedparser
import urllib.parse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import Forbidden, BadRequest

# ============================================
# ⚙️ الإعدادات الأساسية
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# دالة لجلب توقيت السودان الدقيق (GMT+2) لتجنب توقيت السيرفرات الأجنبية
def get_sudan_time():
    utc_now = datetime.utcnow()
    sudan_time = utc_now + timedelta(hours=2)
    return sudan_time.strftime('%Y-%m-%d %H:%M:%S')

# ============================================
# 🌐 خادم لمنع إيقاف السيرفر (Render Keep-Alive)
# ============================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ministry of Defense - Operations Room is Active")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ============================================
# 📡 دالة جلب الأخبار وتجاوز الحظر
# ============================================
def fetch_rss(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=12)
        if response.status_code == 200:
            return feedparser.parse(response.content)
    except Exception as e:
        pass
    return feedparser.parse("")

# ============================================
# 🗄️ إدارة قواعد البيانات (مع حماية ضد التلف)
# ============================================
class DataManager:
    def __init__(self, filename="radar_data.json"):
        self.filename = filename
        self.data = self.load_data()

    def load_data(self):
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {
                "subscribers": {},
                "seen_articles": [],
                "stats": {
                    "alerts_sent": 0,
                    "articles_scanned": 0,
                    "start_time": get_sudan_time()
                }
            }

    def save_data(self):
        try:
            # الحفظ الآمن لمنع تلف الملف
            temp_file = self.filename + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, self.filename)
        except Exception as e:
            logger.error(f"Save Data Error: {e}")

    def add_sub(self, chat_id, first_name):
        self.data["subscribers"][str(chat_id)] = {"name": first_name, "joined": get_sudan_time()}
        self.save_data()
        
    def remove_sub(self, chat_id):
        if str(chat_id) in self.data["subscribers"]:
            del self.data["subscribers"][str(chat_id)]
            self.save_data()

# ============================================
# 🔍 محرك الرصد الدقيق
# ============================================
class RadarEngine:
    def __init__(self, dm):
        self.dm = dm
        
        self.exact_keywords = [
            "وزير الدفاع السوداني", "وزارة الدفاع السودانية", "يس إبراهيم", "حسن داؤود كبرون", "حسن كبرون",
            "عبدالفتاح البرهان", "عبد الفتاح البرهان", "البرهان", "شمس الدين كباشي", "ياسر العطا", "إبراهيم جابر", 
            "القوات المسلحة السودانية", "الجيش السوداني", "القيادة العامة للجيش", "الناطق الرسمي باسم القوات المسلحة"
        ]

        self.direct_sources = {
            "قناة الحدث": "https://www.alhadath.net/.mrss/ar.xml",
            "قناة العربية": "https://www.alarabiya.net/.mrss/ar.xml",
            "الجزيرة": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8",
            "BBC عربي": "http://feeds.bbci.co.uk/arabic/rss.xml",
            "سكاي نيوز": "https://www.skynewsarabia.com/web/rss",
            "راديو دبنقا": "https://www.dabangasudan.org/ar/feed",
            "سودان تربيون": "https://sudantribune.net/feed/",
            "صحيفة التغيير": "https://www.altagheer.info/ar/feed/"
        }

        self.google_queries = [
            '"وزير الدفاع السوداني"', '"وزارة الدفاع السودانية"', '"يس إبراهيم"', '"حسن داؤود كبرون"',
            '"عبد الفتاح البرهان"', '"ياسر العطا"', '"الجيش السوداني"'
        ]

    def scan_all(self):
        found_articles = []
        
        for source_name, url in self.direct_sources.items():
            feed = fetch_rss(url)
            for entry in feed.entries[:10]:
                self.dm.data["stats"]["articles_scanned"] += 1
                art = self._analyze_entry(entry, source_name)
                if art: found_articles.append(art)

        for query in self.google_queries:
            encoded_query = urllib.parse.quote(query)
            google_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ar&gl=SD&ceid=SD:ar"
            feed = fetch_rss(google_url)
            for entry in feed.entries[:5]:
                self.dm.data["stats"]["articles_scanned"] += 1
                art = self._analyze_entry(entry, "رصد المحرك الدولي (Google)")
                if art: found_articles.append(art)

        self.dm.save_data()
        return found_articles

    def _analyze_entry(self, entry, source_name):
        # تنظيف الرابط من الشوائب لمنع التكرار
        raw_link = entry.get('link', '')
        clean_link = raw_link.split('?')[0] if '?' in raw_link else raw_link
        
        title = entry.get('title', '')
        summary = entry.get('summary', '')

        if not clean_link or clean_link in self.dm.data["seen_articles"]:
            return None

        full_text = f"{title} {summary}"
        matched_keyword = None
        
        for kw in self.exact_keywords:
            if kw in full_text:
                matched_keyword = kw
                break

        if matched_keyword:
            self.dm.data["seen_articles"].append(clean_link)
            if len(self.dm.data["seen_articles"]) > 4000:
                self.dm.data["seen_articles"].pop(0)
            
            return {
                "title": title, "link": raw_link, "source": source_name,
                "keyword": matched_keyword, "time": get_sudan_time()
            }
        return None

# ============================================
# 🤖 أوامر البوت ولوحة التحكم
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    dm = context.bot_data['dm']
    dm.add_sub(update.effective_chat.id, user.first_name)

    msg = f"""
🦅 **وزارة الدفاع - غرفة العمليات الإعلامية** 🇸🇩
🔐 `[نظام مؤمن - خاص بمنسوبي الوزارة]`

سعادة / **{user.first_name}** المحترم، 
تم تسجيل دخولكم لمحطة الرصد. يقوم هذا الرادار بمسح دوري للصحافة المحلية والعالمية لالتقاط أي محتوى يخص القيادة العامة والقوات المسلحة.

**[ مهام وحدة التحكم ]**
الرجاء اختيار الأداة المطلوبة من اللوحة أدناه:
"""
    keyboard = [
        [InlineKeyboardButton("📝 إصدار موجز صحفي للقيادة", callback_data="press_briefing")],
        [InlineKeyboardButton("🗂️ رصد القيادة ووزير الدفاع", callback_data="command_news")],
        [InlineKeyboardButton("🌍 الإعلام الخارجي", callback_data="global_media"), InlineKeyboardButton("🇸🇩 الإعلام الداخلي", callback_data="local_media")],
        [InlineKeyboardButton("⚙️ حالة النظام والإحصائيات", callback_data="system_status"), InlineKeyboardButton("🎯 بنك الأهداف", callback_data="show_keywords")]
    ]
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dm = context.bot_data['dm']
    dm.remove_sub(update.effective_chat.id)
    await update.message.reply_text("🛑 **تم إخلاء الطرف:** تم إيقاف الإشعارات وإزالة حسابكم من قاعدة بيانات الرصد بنجاح.", parse_mode='Markdown')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔍 **أداة البحث الاستخباري:**\nالرجاء كتابة كلمة للبحث عنها، مثال:\n`/search الفاشر`", parse_mode='Markdown')
        return
    query_text = " ".join(context.args)
    await update.message.reply_text(f"⏳ جاري إجراء مسح ميداني عن: **{query_text}**...")
    
    encoded = urllib.parse.quote(query_text)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar"
    feed = await asyncio.to_thread(fetch_rss, url)
    
    res = [f"📌 **{e.title}**\n🔗 [الرابط]({e.link})" for e in feed.entries[:5]]
    if res:
        await update.message.reply_text(f"🔍 **نتائج المسح الميداني:**\n\n" + "\n\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)
    else:
        await update.message.reply_text("❌ لم يتم التقاط نتائج حديثة.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    dm = context.bot_data['dm']
    radar = context.bot_data['radar']

    if query.data == "press_briefing":
        await query.message.reply_text("⏳ جاري تجميع الموجز الصحفي الآلي...")
        feed = await asyncio.to_thread(fetch_rss, "https://news.google.com/rss/search?q=%D8%A7%D9%84%D8%AC%D9%8A%D8%B4+%D8%A7%D9%84%D8%B3%D9%88%D8%AF%D8%A7%D9%86%D9%8A+OR+%D9%88%D8%B2%D9%8A%D8%B1+%D8%A7%D9%84%D8%AF%D9%81%D8%A7%D8%B9&hl=ar&gl=SD&ceid=SD:ar")
        res = [f"{i}. {e.title}\n🔗 [المصدر]({e.link})" for i, e in enumerate(feed.entries[:5], 1)]
        briefing = f"📑 **موجز الرصد الإعلامي (جاهز للرفع):**\n\n" + "\n\n".join(res) + f"\n\n🕒 `إعداد آلي - توقيت السودان: {get_sudan_time()}`"
        await query.message.reply_text(briefing, parse_mode='Markdown', disable_web_page_preview=True)

    elif query.data == "command_news":
        await query.message.reply_text("⏳ جاري سحب أرشيف القيادة ووزير الدفاع...")
        encoded = urllib.parse.quote('"وزير الدفاع السوداني" OR "عبد الفتاح البرهان"')
        feed = await asyncio.to_thread(fetch_rss, f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar")
        res = [f"📌 **{e.title}**\n🔗 [رابط]({e.link})" for e in feed.entries[:4]]
        if res:
            await query.message.reply_text("🗂️ **تصريحات وأخبار القيادة العليا:**\n\n" + "\n\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)
        else:
            await query.message.reply_text("لم يتم رصد أخبار حديثة للقيادة.")

    elif query.data == "global_media":
        await query.message.reply_text("⏳ جاري فحص الوكالات الخارجية...")
        feed = await asyncio.to_thread(fetch_rss, radar.direct_sources["الجزيرة"])
        res = [f"🌍 **{e.title}**\n🔗 [رابط]({e.link})" for e in feed.entries[:4]]
        await query.message.reply_text("🌍 **رصد الإعلام الخارجي:**\n\n" + "\n\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)

    elif query.data == "local_media":
        await query.message.reply_text("⏳ جاري فحص الإذاعات والصحف الداخلية...")
        feed = await asyncio.to_thread(fetch_rss, radar.direct_sources["سودان تربيون"])
        res = [f"🇸🇩 **{e.title}**\n🔗 [رابط]({e.link})" for e in feed.entries[:4]]
        await query.message.reply_text("🇸🇩 **رصد الإعلام الداخلي:**\n\n" + "\n\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)

    elif query.data == "system_status":
        scanned = dm.data["stats"]["articles_scanned"]
        alerts = dm.data["stats"]["alerts_sent"]
        uptime = dm.data["stats"]["start_time"]
        subs = len(dm.data["subscribers"])
        
        stat_msg = f"⚙️ **التقرير الفني لغرفة العمليات:**\n\n👥 **الضباط/المشتركين:** `{subs}`\n🔍 **حجم البيانات الممسوحة:** `{scanned}` مقال.\n🚨 **الإنذارات الصادرة:** `{alerts}` تنبيه.\n⏱️ **تاريخ بدء السيرفر:** `{uptime}`\n🟢 **حالة النظام:** اتصال آمن، قاعدة البيانات محمية."
        await query.message.reply_text(stat_msg, parse_mode='Markdown')

    elif query.data == "show_keywords":
        kws = "\n".join([f"🎯 `{kw}`" for kw in radar.exact_keywords])
        await query.message.reply_text(f"📋 **بنك الأهداف (الكلمات المراقبة آلياً):**\n\n{kws}", parse_mode='Markdown')

# ============================================
# 🔄 حلقة الرصد التلقائي الخلفية (الرادار)
# ============================================
async def radar_loop(app: Application):
    await asyncio.sleep(5)
    while True:
        try:
            dm = app.bot_data['dm']
            radar = app.bot_data['radar']
            articles = await asyncio.to_thread(radar.scan_all)
            
            if articles:
                for art in articles:
                    dm.data["stats"]["alerts_sent"] += 1
                    dm.save_data()
                    msg = f"🚨 **[ إشعار أمني - عاجل ]** 🇸🇩\n\n📌 **العنوان:** {art['title']}\n📰 **الجهة الناشرة:** {art['source']}\n🎯 **الهدف المرصود:** `{art['keyword']}`\n⏰ **توقيت السودان:** {art['time']}\n\n🔗 [انقر هنا للتحقق من المصدر]({art['link']})"
                    
                    # إرسال بحذر لتنظيف الحسابات المغلقة
                    for chat_id in list(dm.data["subscribers"].keys()):
                        try:
                            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                            await asyncio.sleep(0.1)
                        except (Forbidden, BadRequest):
                            # إذا قام المستخدم بحظر البوت، يتم مسحه آلياً لحماية السيرفر
                            dm.remove_sub(chat_id)
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Loop Error: {e}")
        
        await asyncio.sleep(120)

async def post_init(application: Application):
    asyncio.create_task(radar_loop(application))

# ============================================
# 🚀 التشغيل الرئيسي
# ============================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    dm = DataManager()
    radar = RadarEngine(dm)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.bot_data['dm'] = dm
    app.bot_data['radar'] = radar

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("✅ غرفة العمليات الإعلامية تعمل الآن بكفاءة...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
