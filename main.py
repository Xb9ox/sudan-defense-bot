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
# ⚙️ الإعدادات
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA")
ADMIN_ID = 6414385813
SECRET_PASSWORD = "Sudani"
START_CUTOFF_DATE = datetime(2024, 9, 10, 0, 0, 0)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

def get_sudan_time():
    return (datetime.utcnow() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

# ============================================
# 🌐 سيرفر الويب (ضروري لـ Render - يفتح البورت فوراً)
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
        # تقليل الضوضاء في اللوج
        return

def run_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"✅ HEALTH SERVER LISTENING ON 0.0.0.0:{port}", flush=True)
    logger.info(f"Health server bound to port {port}")
    server.serve_forever()

def self_ping_loop():
    time.sleep(20)
    url = os.environ.get("RENDER_EXTERNAL_URL")
    while True:
        try:
            if url:
                r = requests.get(url, timeout=10)
                print(f"⏰ Self-ping: {r.status_code}", flush=True)
        except Exception as e:
            print(f"Self-ping error: {e}", flush=True)
        time.sleep(240)

# ============================================
# 📡 جلب الأخبار
# ============================================
def fetch_rss(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return feedparser.parse(r.content)
    except Exception:
        pass
    return feedparser.parse("")

# ============================================
# 🗄️ قاعدة البيانات
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
                "stats": {
                    "alerts_sent": 0,
                    "articles_scanned": 0,
                    "start_time": get_sudan_time(),
                },
            }

    def save_data(self):
        try:
            tmp = self.filename + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.filename)
        except Exception as e:
            logger.error(f"save error: {e}")

    def is_authorized(self, user_id):
        return user_id == ADMIN_ID or user_id in self.data.get("authorized_users", [])

    def authorize_user(self, user_id):
        if user_id not in self.data["authorized_users"]:
            self.data["authorized_users"].append(user_id)
            self.save_data()

    def add_sub(self, chat_id, first_name):
        self.data["subscribers"][str(chat_id)] = {
            "name": first_name or "عضو",
            "joined": get_sudan_time(),
        }
        self.save_data()

    def remove_sub(self, chat_id):
        if str(chat_id) in self.data["subscribers"]:
            del self.data["subscribers"][str(chat_id)]
            self.save_data()

# ============================================
# 🔍 محرك الرصد
# ============================================
class RadarEngine:
    def __init__(self, dm):
        self.dm = dm
        self.exact_keywords = [
            "وزير الدفاع السوداني", "وزارة الدفاع السودانية",
            "حسن داؤود كبرون", "حسن داوود كبرون", "يس إبراهيم",
            "عبد الفتاح البرهان", "عبدالفتاح البرهان", "البرهان",
            "شمس الدين كباشي", "الكباشي", "ياسر العطا",
            "الجيش السوداني", "القوات المسلحة السودانية",
            "القيادة العامة للجيش السوداني",
            "الناطق الرسمي باسم القوات المسلحة السودانية",
        ]
        self.direct_sources = {
            "الحدث": "https://www.alhadath.net/.mrss/ar.xml",
            "الجزيرة": "https://www.aljazeera.net/aljazeerarss/a7c18667-7117-4555-9130-031986811977/73d0e1b4-532f-45ef-b135-bfd3d2cf09c8",
            "BBC": "http://feeds.bbci.co.uk/arabic/rss.xml",
            "سودان تربيون": "https://sudantribune.net/feed/",
            "دبنقا": "https://www.dabangasudan.org/ar/feed",
        }
        self.google_queries = [
            '"وزارة الدفاع السودانية"',
            '"وزير الدفاع السوداني"',
            '"عبد الفتاح البرهان" السودان',
            '"ياسر العطا" السودان',
            '"شمس الدين كباشي" السودان',
            '"الجيش السوداني"',
        ]

    def scan_all(self):
        found = []
        for name, url in self.direct_sources.items():
            feed = fetch_rss(url)
            for entry in feed.entries[:10]:
                self.dm.data["stats"]["articles_scanned"] += 1
                art = self._analyze_entry(entry, name)
                if art:
                    found.append(art)

        for q in self.google_queries:
            gurl = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=ar&gl=SD&ceid=SD:ar"
            feed = fetch_rss(gurl)
            for entry in feed.entries[:5]:
                self.dm.data["stats"]["articles_scanned"] += 1
                art = self._analyze_entry(entry, "رصد صحافة العالم (Google)")
                if art:
                    found.append(art)

        self.dm.save_data()
        return found

    def _analyze_entry(self, entry, source_name):
        raw_link = entry.get("link", "")
        if not raw_link or raw_link.startswith("#"):
            return None
        clean_link = raw_link.split("?")[0] if "?" in raw_link and "google" not in raw_link else raw_link
        if clean_link in self.dm.data["seen_articles"]:
            return None

        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        if pub:
            try:
                if datetime(*pub[:6]) < START_CUTOFF_DATE:
                    return None
            except Exception:
                pass

        full = f"{entry.get('title', '')} {entry.get('summary', '')}"
        matched = next((kw for kw in self.exact_keywords if kw in full), None)
        if not matched:
            return None

        self.dm.data["seen_articles"].append(clean_link)
        if len(self.dm.data["seen_articles"]) > 4000:
            self.dm.data["seen_articles"].pop(0)

        return {
            "title": entry.get("title", ""),
            "link": raw_link,
            "source": source_name,
            "keyword": matched,
            "time": get_sudan_time(),
        }

# ============================================
# 🤖 أوامر التلجرام
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    dm = context.bot_data["dm"]

    if not dm.is_authorized(chat_id):
        await update.message.reply_text(
            "🔒 **نظام رصد عسكري مغلق** 🇸🇩\n━━━━━━━━━━━━━\n"
            "مخصص لأعضاء وزارة الدفاع والقوات المسلحة السودانية فقط.\n\n"
            "✍️ اكتب كلمة المرور وأرسلها للدخول:",
            parse_mode="Markdown",
        )
        return

    dm.add_sub(chat_id, user.first_name)
    await show_main_menu(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    dm = context.bot_data["dm"]

    if dm.is_authorized(chat_id):
        return

    if text.lower() == SECRET_PASSWORD.lower():
        dm.authorize_user(chat_id)
        dm.add_sub(chat_id, update.effective_user.first_name)
        await update.message.reply_text(
            "✅ **تم التحقق بنجاح**\nمرحباً بك في نظام الرصد.",
            parse_mode="Markdown",
        )
        await show_main_menu(update, context)
    else:
        await update.message.reply_text(
            "❌ كلمة المرور غير صحيحة. أعد المحاولة.",
            parse_mode="Markdown",
        )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    msg = f"""
━━━━━━━━━━━━━━━━━━━━━━
🦅 **نظام الرصد الإعلامي - وزارة الدفاع** 🇸🇩
━━━━━━━━━━━━━━━━━━━━━━

سعادة / **{user.first_name}** المحترم،
أهلاً بك في غرفة العمليات.

📡 الرادار نشط 24/7 — الإشعارات تصلك تلقائياً.

اختر من القائمة: 👇
"""
    keyboard = [
        [InlineKeyboardButton("📝 موجز الرصد (اليوم)", callback_data="command_briefing")],
        [InlineKeyboardButton("⏪ أرشيف 48 ساعة", callback_data="yesterday_news")],
        [
            InlineKeyboardButton("⚙️ حالة النظام", callback_data="system_status"),
            InlineKeyboardButton("🎯 الأهداف", callback_data="show_keywords"),
        ],
    ]
    if chat_id == ADMIN_ID:
        keyboard.append(
            [InlineKeyboardButton("👥 المشتركين (المدير)", callback_data="admin_users")]
        )

    target = update.message or update.callback_query.message
    await target.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dm = context.bot_data["dm"]
    radar = context.bot_data["radar"]

    if not dm.is_authorized(query.message.chat_id):
        await query.message.reply_text("⛔ غير مصرح.")
        return

    if query.data == "command_briefing":
        await query.message.reply_text("⏳ جاري سحب الموجز...")
        q = urllib.parse.quote('"وزارة الدفاع السودانية" OR "البرهان" OR "الجيش السوداني"')
        feed = await asyncio.to_thread(
            fetch_rss, f"https://news.google.com/rss/search?q={q}&hl=ar&gl=SD&ceid=SD:ar"
        )
        res = [f"📌 {e.title}\n🔗 [رابط]({e.link})" for e in feed.entries[:5]]
        text = "📑 **موجز اليوم:**\n━━━━━━━━━━━━━\n" + (
            "\n\n".join(res) if res else "لا توجد نتائج حالياً."
        )
        await query.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

    elif query.data == "yesterday_news":
        await query.message.reply_text("⏳ جاري سحب أرشيف 48 ساعة...")
        q = urllib.parse.quote(
            '("وزير الدفاع السوداني" OR "البرهان" OR "الجيش السوداني") when:2d'
        )
        feed = await asyncio.to_thread(
            fetch_rss, f"https://news.google.com/rss/search?q={q}&hl=ar&gl=SD&ceid=SD:ar"
        )
        res = [f"⏪ **{e.title}**\n🔗 [رابط]({e.link})" for e in feed.entries[:5]]
        text = "⏪ **أرشيف 48 ساعة:**\n━━━━━━━━━━━━━\n" + (
            "\n\n".join(res) if res else "لا توجد نتائج."
        )
        await query.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

    elif query.data == "system_status":
        s = dm.data["stats"]
        msg = (
            "⚙️ **حالة السيرفر:**\n━━━━━━━━━━━━━\n"
            f"🔍 المسح: `{s.get('articles_scanned', 0)}`\n"
            f"🚨 التنبيهات: `{s.get('alerts_sent', 0)}`\n"
            f"⏱️ التشغيل: `{s.get('start_time', '-')}`\n"
            f"👥 المشتركين: `{len(dm.data.get('subscribers', {}))}`\n"
            "🟢 البورت مفتوح — منع الخمول مفعل"
        )
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif query.data == "admin_users":
        if query.message.chat_id != ADMIN_ID:
            return
        subs = dm.data.get("subscribers", {})
        if not subs:
            await query.message.reply_text("لا يوجد مشتركون.")
            return
        lines = [
            f"👤 **{info.get('name')}**\n🔑 `{uid}`\n⏰ {info.get('joined')}"
            for uid, info in subs.items()
        ]
        await query.message.reply_text(
            f"👥 **المشتركين ({len(subs)}):**\n━━━━━━━━━━━━━\n" + "\n\n".join(lines),
            parse_mode="Markdown",
        )

    elif query.data == "show_keywords":
        kws = "\n".join([f"🎯 `{k}`" for k in radar.exact_keywords])
        await query.message.reply_text(
            f"📋 **الأهداف:**\n━━━━━━━━━━━━━\n{kws}", parse_mode="Markdown"
        )

async def radar_loop(app: Application):
    await asyncio.sleep(8)
    print("🔄 Radar loop started", flush=True)
    while True:
        try:
            dm = app.bot_data["dm"]
            radar = app.bot_data["radar"]
            articles = await asyncio.to_thread(radar.scan_all)
            if articles:
                for art in articles:
                    dm.data["stats"]["alerts_sent"] += 1
                    dm.save_data()
                    msg = (
                        "🚨 **[ رصد أمني عاجل ]** 🇸🇩\n━━━━━━━━━━━━━\n"
                        f"📌 **العنوان:** {art['title']}\n"
                        f"📰 **الجهة:** {art['source']}\n"
                        f"🎯 **الهدف:** `{art['keyword']}`\n"
                        f"⏰ **الوقت:** {art['time']}\n\n"
                        f"🔗 [الرابط]({art['link']})"
                    )
                    for cid in list(dm.data["subscribers"].keys()):
                        try:
                            await app.bot.send_message(
                                chat_id=int(cid),
                                text=msg,
                                parse_mode="Markdown",
                                disable_web_page_preview=True,
                            )
                            await asyncio.sleep(0.1)
                        except (Forbidden, BadRequest):
                            dm.remove_sub(cid)
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"radar loop error: {e}")
        await asyncio.sleep(120)

async def post_init(app: Application):
    asyncio.create_task(radar_loop(app))

# ============================================
# 🚀 التشغيل — مع حماية من الإغلاق المبكر
# ============================================
def main():
    # 1) افتح البورت فوراً قبل أي شيء (حل No open ports)
    t_web = threading.Thread(target=run_health_server, daemon=True)
    t_web.start()
    time.sleep(1.5)  # إعطاء وقت لـ Render يكتشف البورت

    # 2) منبه منع النوم
    threading.Thread(target=self_ping_loop, daemon=True).start()

    print("🚀 Starting Telegram bot...", flush=True)

    try:
        dm = DataManager()
        radar 
