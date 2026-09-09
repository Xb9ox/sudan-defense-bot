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

# دالة التوقيت المحلي للسودان (GMT+2)
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
        self.wfile.write(b"Sudan MoD & Military Command Radar 24/7 is Active")

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
# 🗄️ إدارة قواعد البيانات
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
# 🔍 محرك رصد (وزارة الدفاع والقيادة العسكرية حصراً)
# ============================================
class RadarEngine:
    def __init__(self, dm):
        self.dm = dm
        
        # 🎯 الكلمات المفتاحية المخصصة حصراً للقائمة المطلوبة فقط
        self.exact_keywords = [
            # 1. وزارة الدفاع ووزير الدفاع
            "وزير الدفاع السوداني", "وزارة الدفاع السودانية", "وزير دفاع السودان", "وزير الدفاع المكلف", 
            "وزير الدفاع", "وزارة الدفاع", "حسن داؤود كبرون", "حسن داوود كبرون", "حسن كبرون", 
            "الفريق حسن داؤود", "يس إبراهيم يس", "يس إبراهيم",
            
            # 2. القائد العام - عبد الفتاح البرهان
            "عبد الفتاح البرهان", "عبدالفتاح البرهان", "البرهان",
            
            # 3. القائد شمس الدين كباشي
            "شمس الدين كباشي", "الكباشي", "كباشي",
            
            # 4. القائد ياسر العطا
            "ياسر العطا", "العطا",
            
            # 5. رئاسة القوات المسلحة ورئاسة الجيش
            "القوات المسلحة السودانية", "الجيش السوداني", "القيادة العامة للجيش", 
            "القيادة العامة للقوات المسلحة", "رئاسة القوات المسلحة", "هيئة الأركان", 
            "الناطق الرسمي باسم القوات المسلحة",
            
            # English Equivalents for global media
            "Sudan Defense Minister", "Sudan Ministry of Defense", "Abdel Fattah al-Burhan", 
            "Yasser al-Atta", "Shams el-Din Kabbashi", "Sudanese Army", "SAF Sudan"
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

        # استعلامات مسح مركزة حصراً على هذه الشخصيات والجهات
        self.google_queries = [
            '"وزير الدفاع السوداني"',
            '"وزارة الدفاع السودانية"',
            '"عبد الفتاح البرهان"',
            '"شمس الدين كباشي"',
            '"ياسر العطا"',
            '"القيادة العامة للقوات المسلحة" OR "الجيش السوداني"'
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
                art = self._analyze_entry(entry, "رصد صحافة العالم (Google)")
                if art: found_articles.append(art)

        self.dm.save_data()
        return found_articles

    def _analyze_entry(self, entry, source_name):
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
# 🤖 لوحة التحكم ورصد قيادة الدفاع والجيش
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    dm = context.bot_data['dm']
    dm.add_sub(update.effective_chat.id, user.first_name)

    msg = f"""
🦅 **رادار وزارة الدفاع وقيادة القوات المسلحة** 🇸🇩
🔐 `[نظام رصد مخصص ومغلق]`

سعادة / **{user.first_name}** المحترم، 
تم تفعيل الرصد المباشر والحصري لـ:
1. **وزارة الدفاع ووزير الدفاع**.
2. **الفريق أول ركن عبد الفتاح البرهان**.
3. **الفريق أول ركن شمس الدين كباشي**.
4. **الفريق أول ركن ياسر العطا**.
5. **رئاسة القوات المسلحة والقيادة العامة للجيش**.

سيتم إرسال أي خبر أو بيان أو تصريح يخص هذه الجهات فوراً إلى حسابكم.
"""
    keyboard = [
        [InlineKeyboardButton("📝 موجز أحدث أخبار القيادة والدفاع", callback_data="command_briefing")],
        [InlineKeyboardButton("🏛️ أخبار وزارة الدفاع والجيش", callback_data="mod_news")],
        [InlineKeyboardButton("⚙️ حالة الرادار والإحصائيات", callback_data="system_status"), InlineKeyboardButton("🎯 القادة والجهات المراقبة", callback_data="show_keywords")]
    ]
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dm = context.bot_data['dm']
    dm.remove_sub(update.effective_chat.id)
    await update.message.reply_text("🛑 تم إيقاف الإشعارات وإزالة حسابكم من قاعدة البيانات.", parse_mode='Markdown')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔍 **أداة البحث المباشر:**\nاكتب اسم قائد أو جهة للبحث، مثال:\n`/search ياسر العطا`", parse_mode='Markdown')
        return
    query_text = " ".join(context.args)
    await update.message.reply_text(f"⏳ جاري إجراء مسح عن: **{query_text}**...")
    
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

    if query.data == "command_briefing":
        await query.message.reply_text("⏳ جاري استخراج الموجز الخاص بالقيادة العليا والدفاع...")
        encoded = urllib.parse.quote('"وزير الدفاع السوداني" OR "عبد الفتاح البرهان" OR "ياسر العطا" OR "شمس الدين كباشي"')
        feed = await asyncio.to_thread(fetch_rss, f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar")
        res = [f"{i}. {e.title}\n🔗 [المصدر]({e.link})" for i, e in enumerate(feed.entries[:5], 1)]
        briefing = f"📑 **موجز رصد القيادة العامة ووزارة الدفاع:**\n\n" + "\n\n".join(res) + f"\n\n🕒 `توقيت السودان: {get_sudan_time()}`"
        await query.message.reply_text(briefing, parse_mode='Markdown', disable_web_page_preview=True)

    elif query.data == "mod_news":
        await query.message.reply_text("⏳ جاري سحب أحدث بيانات القوات المسلحة ووزارة الدفاع...")
        encoded = urllib.parse.quote('"وزارة الدفاع السودانية" OR "القيادة العامة للقوات المسلحة"')
        feed = await asyncio.to_thread(fetch_rss, f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar")
        res = [f"📌 **{e.title}**\n🔗 [رابط]({e.link})" for e in feed.entries[:4]]
        if res:
            await query.message.reply_text("🏛️ **أخبار وبيانات القوات المسلحة ووزارة الدفاع:**\n\n" + "\n\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)
        else:
            await query.message.reply_text("لم يتم رصد بيانات حديثة.")

    elif query.data == "system_status":
        scanned = dm.data["stats"]["articles_scanned"]
        alerts = dm.data["stats"]["alerts_sent"]
        uptime = dm.data["stats"]["start_time"]
        subs = len(dm.data["subscribers"])
        
        stat_msg = f"⚙️ **تقرير نظام الرصد العسكري:**\n\n👥 **المستقبلين:** `{subs}`\n🔍 **المقالات المفحوصة:** `{scanned}` مقال.\n🚨 **الإشعارات الصادرة:** `{alerts}` تنبيه.\n⏱️ **بداية التفعيل:** `{uptime}`\n🟢 **الحالة:** تركيز حاد على وزارة الدفاع وقادة الجيش."
        await query.message.reply_text(stat_msg, parse_mode='Markdown')

    elif query.data == "show_keywords":
        kws = "\n".join([f"🎯 `{kw}`" for kw in radar.exact_keywords])
        await query.message.reply_text(f"📋 **الجهات والشخصيات المراقبة حصراً:**\n\n{kws}", parse_mode='Markdown')

# ============================================
# 🔄 حلقة الرصد التلقائي الخلفية
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
                    msg = f"🚨 **[ رصد عاجل - قيادة الدفاع والجيش ]** 🇸🇩\n\n📌 **العنوان:** {art['title']}\n📰 **الجهة الناشرة:** {art['source']}\n🎯 **الهدف المرصود:** `{art['keyword']}`\n⏰ **توقيت السودان:** {art['time']}\n\n🔗 [انقر هنا للتفاصيل والمصدر]({art['link']})"
                    
                    for chat_id in list(dm.data["subscribers"].keys()):
                        try:
                            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                            await asyncio.sleep(0.1)
                        except (Forbidden, BadRequest):
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

    logger.info("✅ رادار وزارة الدفاع وقيادة الجيش يعمل الآن...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
