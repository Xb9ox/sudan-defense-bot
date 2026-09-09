import os
import json
import logging
import asyncio
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import feedparser
import urllib.parse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ============================================
# ⚙️ الإعدادات الأساسية والتوكن
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# 🌐 خادم لمنع إيقاف السيرفر (Render Keep-Alive)
# ============================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Sudan Defense Ministry Radar Bot 24/7 is Active")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

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
        logger.error(f"Error fetching {url}: {e}")
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
                "stats": {"alerts": 0}
            }

    def save_data(self):
        try:
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Save error: {e}")

    def add_sub(self, chat_id, first_name):
        self.data["subscribers"][str(chat_id)] = {"name": first_name, "active": True}
        self.save_data()

# ============================================
# 🔍 محرك الرصد الدقيق جداً
# ============================================
class RadarEngine:
    def __init__(self, dm):
        self.dm = dm
        
        # 🎯 الكلمات المفتاحية الدقيقة لوزارة الدفاع والقيادة العامة
        self.exact_keywords = [
            "وزير الدفاع السوداني", "وزارة الدفاع السودانية", "يس إبراهيم", "حسن داؤود كبرون", "حسن داوود كبرون",
            "الفريق أول ركن عبدالفتاح البرهان", "عبد الفتاح البرهان", "البرهان", 
            "شمس الدين كباشي", "ياسر العطا", "إبراهيم جابر", 
            "القوات المسلحة السودانية", "الجيش السوداني", "القيادة العامة للجيش"
        ]

        # 📡 المصادر المباشرة
        self.direct_sources = {
            "قناة الحدث": "https://www.alhadath.net/.mrss/ar.xml",
            "قناة العربية": "https://www.alarabiya.net/.mrss/ar.xml",
            "الجزيرة الإخبارية": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8",
            "BBC عربي": "http://feeds.bbci.co.uk/arabic/rss.xml",
            "سكاي نيوز عربية": "https://www.skynewsarabia.com/web/rss",
            "فرانس 24": "https://www.france24.com/ar/rss",
            "راديو دبنقا": "https://www.dabangasudan.org/ar/feed",
            "سودان تربيون": "https://sudantribune.net/feed/",
            "صحيفة التغيير": "https://www.altagheer.info/ar/feed/"
        }

        # 🌐 روابط بحث جوجل الإخباري المتقدمة لكافة الصحف العالمية
        self.google_queries = [
            '"وزير الدفاع السوداني"',
            '"وزارة الدفاع السودانية"',
            '"عبد الفتاح البرهان"',
            '"ياسر العطا" OR "شمس الدين كباشي"',
            '"الجيش السوداني"'
        ]

    def scan_all(self):
        found_articles = []
        
        for source_name, url in self.direct_sources.items():
            feed = fetch_rss(url)
            for entry in feed.entries[:10]:
                art = self._analyze_entry(entry, source_name)
                if art: found_articles.append(art)

        for query in self.google_queries:
            encoded_query = urllib.parse.quote(query)
            google_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ar&gl=SD&ceid=SD:ar"
            feed = fetch_rss(google_url)
            for entry in feed.entries[:5]:
                art = self._analyze_entry(entry, "رصد الصحافة العالمية والمحلية (Google)")
                if art: found_articles.append(art)

        return found_articles

    def _analyze_entry(self, entry, source_name):
        link = entry.get('link', '')
        title = entry.get('title', '')
        summary = entry.get('summary', '')

        if not link or link in self.dm.data["seen_articles"]:
            return None

        full_text = f"{title} {summary}"
        matched_keyword = None
        
        for kw in self.exact_keywords:
            if kw in full_text:
                matched_keyword = kw
                break

        if matched_keyword:
            self.dm.data["seen_articles"].append(link)
            if len(self.dm.data["seen_articles"]) > 2000:
                self.dm.data["seen_articles"].pop(0)
            self.dm.save_data()
            
            return {
                "title": title,
                "link": link,
                "source": source_name,
                "keyword": matched_keyword,
                "time": datetime.now().strftime('%H:%M:%S')
            }
        return None

# ============================================
# 🤖 الرسالة الترحيبية المخصصة لأعضاء الوزارة
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    dm = context.bot_data['dm']
    dm.add_sub(chat_id, user.first_name)

    welcome_msg = f"""
🦅 **جمهورية السودان - وزارة الدفاع** 🇸🇩
**نظام الرصد الإعلامي والاستخباري الآلي (24/7)**

سعادة / **{user.first_name}** المحترم، 
أهلاً وسهلاً بك في المنصة المخصصة لرصد ومتابعة كافة الأخبار والتصريحات والمقابلات الخاصة بوزارة الدفاع والقوات المسلحة.

📡 **طبيعة عمل المنصة:**
• **متابعة مباشرة لـ:** (معالي وزير الدفاع - القائد العام رئيس مجلس السيادة الفريق أول ركن عبد الفتاح البرهان - أعضاء القيادة العامة).
• **مسح شامل لكل من:** الصحف السودانية المحلية، الوكالات العربية، والقنوات الإخبارية العالمية (BBC، الحدث، الجزيرة، فرانس 24، إلخ).
• **تغطية المقابلات والبيانات:** التقاط أي ذكر رسمي أو صحفي يخص الجيش ووزارة الدفاع فور نشره في أي مكان حول العالم.

🟢 **الحالة:** تم تسجيل حسابكم بنجاح في التنبيهات الفورية، وسيتم إرسال أي خبر عاجل فور التقاطه.

*وفقكم الله لخدمة الوطن والقوات المسلحة السودانية.* 🇸🇩
"""
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

# ============================================
# 🔄 حلقة الرصد التلقائي في الخلفية (كل دقيقتين)
# ============================================
async def radar_loop(app: Application):
    await asyncio.sleep(5)
    logger.info("🔄 بدأت حلقة الرصد الاستخباري...")
    
    while True:
        try:
            dm = app.bot_data['dm']
            radar = app.bot_data['radar']

            articles = await asyncio.to_thread(radar.scan_all)
            if articles:
                for art in articles:
                    dm.data["stats"]["alerts"] += 1
                    dm.save_data()

                    msg = f"""
🚨 **رصد إعلامي عاجل** 🇸🇩

📌 **العنوان:** {art['title']}

📰 **المصدر:** {art['source']}
🎯 **الكلمة الملتقطة:** `{art['keyword']}`
⏰ **التوقيت:** {art['time']}

🔗 [قراءة الخبر / التفاصيل الكاملة]({art['link']})
"""
                    for chat_id in dm.data["subscribers"].keys():
                        try:
                            await app.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                            await asyncio.sleep(0.1)
                        except:
                            pass
        except Exception as e:
            logger.error(f"Radar Error: {e}")

        await asyncio.sleep(120)

async def post_init(application: Application):
    asyncio.create_task(radar_loop(application))

# ============================================
# 🚀 التشغيل
# ============================================
def main():
    threading.Thread(target=run_health_server, daemon=True).start()

    dm = DataManager()
    radar = RadarEngine(dm)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.bot_data['dm'] = dm
    app.bot_data['radar'] = radar

    app.add_handler(CommandHandler("start", start))

    logger.info("✅ الرادار يعمل بسلام...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
