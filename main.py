import os
import sys
import json
import logging
import asyncio
import threading
import time
import traceback
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import feedparser
import urllib.parse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.error import Forbidden, BadRequest

# ============================================
# ⚙️ الإعدادات الأساسية وكلمة المرور
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA")
ADMIN_ID = 6414385813  # ID المدير
SECRET_PASSWORD = "Sudani"  # كلمة المرور

# 📅 تاريخ بداية الرصد الصارم (من 10 سبتمبر 2024)
START_CUTOFF_DATE = datetime(2024, 9, 10, 0, 0, 0)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

def get_sudan_time():
    utc_now = datetime.utcnow()
    sudan_time = utc_now + timedelta(hours=2)
    return sudan_time.strftime("%Y-%m-%d %H:%M:%S")

# ============================================
# 🌐 خادم الويب ومنع النوم تلقائياً (Anti-Sleep)
# ============================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Sudan MoD Radar is ALIVE 24/7")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return

def run_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"✅ HEALTH SERVER LISTENING ON 0.0.0.0:{port}", flush=True)
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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return feedparser.parse(response.content)
    except Exception:
        pass
    return feedparser.parse("")

# ============================================
# 🗄️ إدارة قواعد البيانات والصلاحيات
# ============================================
class DataManager:
    def __init__(self, filename="radar_data.json"):
        self.filename = filename
        self.data = self.load_data()

    def load_data(self):
        try:
            with open(self.filename, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "authorized_users" not in data:
                    data["authorized_users"] = [ADMIN_ID]
                return data
        except Exception:
            return {
                "authorized_users": [ADMIN_ID],
                "subscribers": {},
                "seen_articles": [],
                "stats": {"alerts_sent": 0, "articles_scanned": 0, "start_time": get_sudan_time()}
            }

    def save_data(self):
        try:
            temp_file = self.filename + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, self.filename)
        except Exception as e:
            logger.error(f"Save error: {e}")

    def is_authorized(self, user_id):
        return user_id == ADMIN_ID or user_id in self.data.get("authorized_users", [])

    def authorize_user(self, user_id):
        if user_id not in self.data["authorized_users"]:
            self.data["authorized_users"].append(user_id)
            self.save_data()

    def add_sub(self, chat_id, first_name):
        self.data["subscribers"][str(chat_id)] = {"name": first_name or "عضو", "joined": get_sudan_time()}
        self.save_data()

    def remove_sub(self, chat_id):
        if str(chat_id) in self.data["subscribers"]:
            del self.data["subscribers"][str(chat_id)]
            self.save_data()

# ============================================
# 🔍 محرك الرصد العسكري السوداني
# ============================================
class RadarEngine:
    def __init__(self, dm):
        self.dm = dm
        self.exact_keywords = [
            "وزير الدفاع السوداني", "وزارة الدفاع السودانية", "حسن داؤود كبرون", "يس إبراهيم",
            "عبد الفتاح البرهان", "عبدالفتاح البرهان", "شمس الدين كباشي", "ياسر العطا",
            "الجيش السوداني", "القيادة العامة للجيش السوداني", "القوات المسلحة السودانية"
        ]
        self.direct_sources = {
            "الحدث": "https://www.alhadath.net/.mrss/ar.xml",
            "الجزيرة": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8",
            "BBC": "http://feeds.bbci.co.uk/arabic/rss.xml",
            "سودان تربيون": "https://sudantribune.net/feed/",
            "دبنقا": "https://www.dabangasudan.org/ar/feed"
        }
        self.google_queries = [
            '"وزارة الدفاع السودانية"', '"وزير الدفاع السوداني"', '"عبد الفتاح البرهان" السودان',
            '"الجيش السوداني"', '"ياسر العطا" السودان', '"شمس الدين كباشي" السودان'
        ]

    def scan_all(self):
        found_articles = []
        for source_name, url in self.direct_sources.items():
            feed = fetch_rss(url)
            for entry in feed.entries[:10]:
                self.dm.data["stats"]["articles_scanned"] += 1
                art = self._analyze_entry(entry, source_name)
                if art:
                    found_articles.append(art)

        for query in self.google_queries:
            encoded_query = urllib.parse.quote(query)
            google_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ar&gl=SD&ceid=SD:ar"
            feed = fetch_rss(google_url)
            for entry in feed.entries[:5]:
                self.dm.data["stats"]["articles_scanned"] += 1
                art = self._analyze_entry(entry, "رصد الصحافة الشامل (Google)")
                if art:
                    found_articles.append(art)

        self.dm.save_data()
        return found_articles

    def _analyze_entry(self, entry, source_name):
        raw_link = entry.get('link', '')
        if not raw_link or raw_link.startswith('#'):
            return None
        clean_link = raw_link.split('?')[0] if '?' in raw_link and 'google' not in raw_link else raw_link

        if clean_link in self.dm.data["seen_articles"]:
            return None

        pub_parsed = entry.get('published_parsed') or entry.get('updated_parsed')
        if pub_parsed:
            try:
                if datetime(*pub_parsed[:6]) < START_CUTOFF_DATE:
                    return None
            except Exception:
                pass

        full_text = f"{entry.get('title', '')} {entry.get('summary', '')}"
        matched_keyword = next((kw for kw in self.exact_keywords if kw in full_text), None)

        if matched_keyword:
            self.dm.data["seen_articles"].append(clean_link)
            if len(self.dm.data["seen_articles"]) > 4000:
                self.dm.data["seen_articles"].pop(0)
            return {
                "title": entry.get('title', ''),
                "link": raw_link,
                "source": source_name,
                "keyword": matched_keyword,
                "time": get_sudan_time()
            }
        return None

# ============================================
# 🤖 أوامر البوت ونظام كلمة المرور
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    dm = context.bot_data['dm']

    if not dm.is_authorized(chat_id):
        await update.message.reply_text(
            "🔒 **نظام رصد عسكري مغلق** 🇸🇩\n━━━━━━━━━━━━━\n"
            "هذا البوت مخصص لأعضاء وزارة الدفاع والقوات المسلحة السودانية فقط.\n\n"
            "✍️ **يرجى كتابة كلمة المرور الخاصة بالمشاركين هنا وإرسالها للدخول:**",
            parse_mode='Markdown'
        )
        return

    dm.add_sub(chat_id, user.first_name)
    await show_main_menu(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = (update.message.text or "").strip()
    dm = context.bot_data['dm']

    if dm.is_authorized(chat_id):
        return

    if user_text.lower() == SECRET_PASSWORD.lower():
        dm.authorize_user(chat_id)
        dm.add_sub(chat_id, update.effective_user.first_name)
        await update.message.reply_text("✅ **تم التحقق من كلمة المرور بنجاح!**\nمرحباً بك في نظام الرصد العسكري.", parse_mode='Markdown')
        await show_main_menu(update, context)
    else:
        await update.message.reply_text("❌ **كلمة المرور غير صحيحة.**\nيرجى إعادة المحاولة.", parse_mode='Markdown')

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    msg = f"""
━━━━━━━━━━━━━━━━━━━━━━
🦅 **نظام الرصد الإعلامي - وزارة الدفاع** 🇸🇩
━━━━━━━━━━━━━━━━━━━━━━

سعادة / **{user.first_name}** المحترم، 
أهلاً بك في غرفة العمليات الإعلامية.

📡 **حالة الرادار:** نشط 24/7 (يتم المسح التلقائي وإرسال الإشعارات إليك فوراً).

**اختر من القائمة أدناه:** 👇
"""
    keyboard = [
        [InlineKeyboardButton("📝 موجز الرصد العسكري (اليوم)", callback_data="command_briefing")],
        [InlineKeyboardButton("⏪ أرشيف الـ 48 ساعة الماضية", callback_data="yesterday_news")],
        [InlineKeyboardButton("⚙️ حالة النظام", callback_data="system_status"), InlineKeyboardButton("🎯 الأهداف", callback_data="show_keywords")]
    ]

    if chat_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👥 عدد ونشاط المشتركين (خاص بالمدير)", callback_data="admin_users")])

    target = update.message or update.callback_query.message
    await target.reply_text(msg, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dm = context.bot_data['dm']
    radar = context.bot_data['radar']

    if not dm.is_authorized(query.message.chat_id):
        await query.message.reply_text("⛔ غير مصرح لك.")
        return

    if query.data == "command_briefing":
        await query.message.reply_text("⏳ جاري سحب أحدث الأخبار العسكرية...")
        encoded = urllib.parse.quote('"وزارة الدفاع السودانية" OR "البرهان" OR "الجيش السوداني"')
        feed = await asyncio.to_thread(fetch_rss, f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar")
        res = [f"📌 {e.title}\n🔗 [الرابط]({e.link})" for e in feed.entries[:5]]
        text = "📑 **موجز رصد القيادة (اليوم):**\n━━━━━━━━━━━━━\n" + ("\n\n".join(res) if res else "لا توجد نتائج حالياً.")
        await query.message.reply_text(text, parse_mode='Markdown', disable_web_page_preview=True)

    elif query.data == "yesterday_news":
        await query.message.reply_text("⏳ جاري سحب أرشيف الأخبار السابقة...")
        encoded = urllib.parse.quote('("وزير الدفاع السوداني" OR "البرهان" OR "الجيش السوداني") when:2d')
        feed = await asyncio.to_thread(fetch_rss, f"https://news.google.com/rss/search?q={encoded}&hl=ar&gl=SD&ceid=SD:ar")
        res = [f"⏪ **{e.title}**\n🔗 [الرابط]({e.link})" for e in feed.entries[:5]]
        text = "⏪ **أرشيف الأخبار (الـ 48 ساعة):**\n━━━━━━━━━━━━━\n" + ("\n\n".join(res) if res else "لا توجد نتائج سابقة.")
        await query.message.reply_text(text, parse_mode='Markdown', disable_web_page_preview=True)

    elif query.data == "system_status":
        s = dm.data["stats"]
        stat_msg = f"⚙️ **تقرير حالة السيرفر:**\n━━━━━━━━━━━━━\n🔍 **حجم المسح:** `{s.get('articles_scanned', 0)}` مقال.\n🚨 **الإنذارات:** `{s.get('alerts_sent', 0)}`\n⏱️ **بدء النظام:** `{s.get('start_time', '-')}`\n🛡️ **الحماية:** مفعلة بكلمة مرور."
        await query.message.reply_text(stat_msg, parse_mode='Markdown')

    elif query.data == "admin_users":
        if query.message.chat_id != ADMIN_ID:
            return
        subs = dm.data.get("subscribers", {})
        if not subs:
            await query.message.reply_text("لا يوجد مشتركون حالياً.")
            return
        users_list = [f"👤 **{info.get('name')}**\n🔑 ID: `{uid}`\n⏰ انضم: {info.get('joined')}" for uid, info in subs.items()]
        final_msg = f"👥 **إجمالي المشتركين بالباسورد ({len(subs)}):**\n━━━━━━━━━━━━━\n" + "\n\n".join(users_list)
        await query.message.reply_text(final_msg, parse_mode='Markdown')

    elif query.data == "show_keywords":
        kws = "\n".join([f"🎯 `{kw}`" for kw in radar.exact_keywords])
        await query.message.reply_text(f"📋 **الأهداف المرصودة آلياً:**\n━━━━━━━━━━━━━\n{kws}", parse_mode='Markdown')

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
                    msg = f"🚨 **[ رصد أمني عاجل ]** 🇸🇩\n━━━━━━━━━━━━━\n📌 **العنوان:** {art['title']}\n📰 **الجهة:** {art['source']}\n🎯 **الهدف:** `{art['keyword']}`\n⏰ **الوقت:** {art['time']}\n\n🔗 [الرابط للتحقق]({art['link']})"

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

    try:
        app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

        app.bot_data['dm'] = dm
        app.bot_data['radar'] = radar

        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(CallbackQueryHandler(button_handler))

        logger.info("✅ النظام مفعل بكلمة المرور Sudani...")
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
