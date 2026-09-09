import os
import json
import logging
import asyncio
import threading
import time
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import feedparser
import urllib.parse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import Forbidden, BadRequest

# ============================================
# ⚙️ الإعدادات الأساسية وتاريخ بداية الرصد
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA")

# 📅 تاريخ بداية الرصد الصارم (الأخبار التلقائية لن تأتي إلا بما هو أحدث من هذا التاريخ)
START_CUTOFF_DATE = datetime(2024, 9, 10, 0, 0, 0)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def get_sudan_time():
    utc_now = datetime.utcnow()
    sudan_time = utc_now + timedelta(hours=2)
    return sudan_time.strftime('%Y-%m-%d %H:%M:%S')

# ============================================
# 🌐 خادم الويب ومنع النوم تلقائياً (Anti-Sleep)
# ============================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sudan MoD & Armed Forces Radar 24/7 is Active")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

def self_ping_loop():
    time.sleep(15)
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    
    while True:
        try:
            if render_url:
                requests.get(render_url, timeout=10)
        except Exception:
            pass
        time.sleep(240)

# ============================================
# 📡 دالة جلب الأخبار وتجاوز الحظر
# ============================================
def fetch_rss(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
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
                "stats": {"alerts_sent": 0, "articles_scanned": 0, "start_time": get_sudan_time()}
            }

    def save_data(self):
        try:
            temp_file = self.filename + ".tmp"
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, self.filename)
        except Exception:
            pass

    def add_sub(self, chat_id, first_name):
        self.data["subscribers"][str(chat_id)] = {"name": first_name, "joined": get_sudan_time()}
        self.save_data()
        
    def remove_sub(self, chat_id):
        if str(chat_id) in self.data["subscribers"]:
            del self.data["subscribers"][str(chat_id)]
            self.save_data()

# ============================================
# 🔍 محرك الرصد العسكري الشامل (السودان فقط)
# ============================================
class RadarEngine:
    def __init__(self, dm):
        self.dm = dm
        
        self.exact_keywords = [
            "وزير الدفاع السوداني", "وزارة الدفاع السودانية", "وزير دفاع السودان",
            "حسن داؤود كبرون", "حسن داوود كبرون", "يس إبراهيم يس", "يس إبراهيم",
            "عبد الفتاح البرهان", "عبدالفتاح البرهان", "البرهان",
            "شمس الدين كباشي", "الكباشي", "ياسر العطا", "العطا",
            "القوات المسلحة السودانية", "الجيش السوداني", "القيادة العامة للجيش السوداني", 
            "القيادة العامة للقوات المسلحة السودانية", "الناطق الرسمي باسم القوات المسلحة السودانية"
        ]

        # المصادر المباشرة السريعة
        self.direct_sources = {
            "الحدث": "https://www.alhadath.net/.mrss/ar.xml",
            "العربية": "https://www.alarabiya.net/.mrss/ar.xml",
            "الجزيرة": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8",
            "BBC": "http://feeds.bbci.co.uk/arabic/rss.xml",
            "سكاي نيوز": "https://www.skynewsarabia.com/web/rss",
            "دبنقا": "https://www.dabangasudan.org/ar/feed",
            "سودان تربيون": "https://sudantribune.net/feed/",
            "التغيير": "https://www.altagheer.info/ar/feed/"
        }

        # توسيع البحث ليشمل الصحف العالمية (رويترز، الأناضول) والمحلية (الراكوبة، سونا)
        self.google_queries = [
            '"وزير الدفاع السوداني"',
            '"وزارة الدفاع السودانية"',
            '"عبد الفتاح البرهان" السودان',
            '"ياسر العطا" السودان',
            '"شمس الدين كباشي" السودان',
            '"الجيش السوداني" OR "القوات المسلحة السودانية"'
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
                art = self._analyze_entry(entry, "رصد الصحافة الشامل (Google)")
                if art: found_articles.append(art)

        self.dm.save_data()
        return found_articles

    def _analyze_entry(self, entry, source_name):
        raw_link = entry.get('link', '')
        
        # 🔗 مُنظّف الروابط: إصلاح الروابط المعطوبة أو الناقصة
        if not raw_link or raw_link.startswith('#'):
            return None
        if raw_link.startswith('/'):
            raw_link = "https://news.google.com" + raw_link
            
        clean_link = raw_link.split('?')[0] if '?' in raw_link and 'google.com' not in raw_link else raw_link
        
        title = entry.get('title', '')
        summary = entry.get('summary', '')

        if clean_link in self.dm.data["seen_articles"]:
            return None

        # 📅 فلتر التاريخ (تجاهل ما قبل 10/09/2024 للرصد التلقائي)
        pub_parsed = entry.get('published_parsed') or entry.get('updated_parsed')
        if pub_parsed:
            try:
                entry_date = datetime(*pub_parsed[:6])
                if entry_date < START_CUTOFF_DATE:
                    return None
            except Exception:
                pass

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
# 🤖 لوحة التحكم والرصد العسكري السوداني
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    dm = context.bot_data['dm']
    dm.add_sub(update.effective_chat.id, user.first_name)

    msg = f"""
🦅 **رادار القوات المسلحة ووزارة الدفاع السودانية** 🇸🇩
🔐 `[نظام رصد عسكري شامل - محلي وعالمي]`

سعادة / **{user.first_name}**، 
الرادار الآن يغطي كل صحف العالم (الوكالات الدولية والمحلية).
تم تفعيل الرصد المباشر لـ:
1. وزارة الدفاع ووزير الدفاع.
2. القيادة العامة (البرهان، كباشي، العطا).
3. بيانات القوات المسلحة السودانية.

*ملاحظة: الإشعارات التلقائية تعمل للأخبار الجديدة فقط بدءاً من 10/09/2024.*
"""
    keyboard = [
        [InlineKeyboardButton("📝 موجز أخبار القيادة والدفاع (اليوم)", callback_data="command_briefing")],
        [InlineKeyboardButton("⏪ أخبار الأمس والأرشيف القريب", callback_data="yesterday_news")],
        [InlineKeyboardButton("⚙️ حالة الرادار والإحصائيات", callback_data="system_status"), InlineKeyboardButton("🎯 الأهداف", callback_data="show_keywords")]
    ]
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dm = context.bot_data['dm']
    dm.remove_sub(update.effective_chat.id)
    await update.message.reply_text("🛑 تم إيقاف الإشعارات بنجاح.", parse_mode='Markdown')

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔍 اكتب للبحث عالمياً، مثال:\n`/search البرهان`", parse_mode='Markdown')
        return
    query_text = " ".join(context.args)
    full_search = f"{query_text} السودان"
    await update.message.reply_text(f"⏳ جاري البحث الشامل عن: **{query_text}**...")
    
    encoded = urllib.parse.quote(full_search)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar"
    feed = await asyncio.to_thread(fetch_rss, url)
    
    res = [f"📌 **{e.title}**\n🔗 [اضغط هنا للخبر]({e.link})" for e in feed.entries[:5]]
    if res:
        await update.message.reply_text(f"🔍 **نتائج المسح الشامل:**\n\n" + "\n\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)
    else:
        await update.message.reply_text("❌ لم يتم التقاط نتائج.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dm = context.bot_data['dm']
    radar = context.bot_data['radar']

    if query.data == "command_briefing":
        await query.message.reply_text("⏳ جاري سحب أحدث الأخبار...")
        encoded = urllib.parse.quote('"وزارة الدفاع السودانية" OR "البرهان" OR "الجيش السوداني"')
        feed = await asyncio.to_thread(fetch_rss, f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar")
        res = [f"📌 {e.title}\n🔗 [الرابط]({e.link})" for e in feed.entries[:5]]
        await query.message.reply_text("📑 **موجز رصد القيادة (اليوم):**\n\n" + "\n\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)

    # --- ⏪ زر أخبار الأمس والأرشيف القريب ---
    elif query.data == "yesterday_news":
        await query.message.reply_text("⏳ جاري البحث في أرشيف الـ 48 ساعة الماضية...")
        # استخدام when:2d في بحث جوجل لجلب أخبار اليومين الماضيين
        encoded = urllib.parse.quote('("وزير الدفاع السوداني" OR "البرهان" OR "الجيش السوداني") when:2d')
        feed = await asyncio.to_thread(fetch_rss, f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar")
        res = [f"⏪ **{e.title}**\n🔗 [الرابط]({e.link})" for e in feed.entries[:5]]
        if res:
            await query.message.reply_text("⏪ **أرشيف أخبار الأمس واليوم السابق:**\n\n" + "\n\n".join(res), parse_mode='Markdown', disable_web_page_preview=True)
        else:
            await query.message.reply_text("لم يتم العثور على أخبار سابقة في هذا النطاق.")

    elif query.data == "system_status":
        scanned = dm.data["stats"]["articles_scanned"]
        alerts = dm.data["stats"]["alerts_sent"]
        uptime = dm.data["stats"]["start_time"]
        subs = len(dm.data["subscribers"])
        
        stat_msg = f"⚙️ **تقرير نظام الرصد:**\n👥 **المستقبلين:** `{subs}`\n🔍 **المقالات المفحوصة:** `{scanned}`\n🚨 **الإشعارات:** `{alerts}`\n⏱️ **بدء التفعيل:** `{uptime}`\n🌐 **النطاق:** بحث شامل لجميع الوكالات العالمية والمحلية."
        await query.message.reply_text(stat_msg, parse_mode='Markdown')

    elif query.data == "show_keywords":
        kws = "\n".join([f"🎯 `{kw}`" for kw in radar.exact_keywords])
        await query.message.reply_text(f"📋 **الجهات المراقبة حصراً:**\n\n{kws}", parse_mode='Markdown')

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
                    msg = f"🚨 **[ رصد عاجل - الجيش السوداني ]** 🇸🇩\n\n📌 **العنوان:** {art['title']}\n📰 **الجهة الناشرة:** {art['source']}\n🎯 **الهدف المرصود:** `{art['keyword']}`\n⏰ **توقيت السودان:** {art['time']}\n\n🔗 [انقر هنا للخبر]({art['link']})"
                    
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
    threading.Thread(target=self_ping_loop, daemon=True).start()

    dm = DataManager()
    radar = RadarEngine(dm)
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.bot_data['dm'] = dm
    app.bot_data['radar'] = radar

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("✅ الرادار يعمل شامل لجميع الصحف وبأرشيف الأمس...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
