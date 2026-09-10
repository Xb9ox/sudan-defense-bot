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
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import Forbidden, BadRequest

# ============================================
# ⚙️ الإعدادات الأساسية وكلمة المرور
# ============================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8924603107:AAG82Gb6LIf0GfRgZF-fqW-ugr6zQcbXvkA")
ADMIN_ID = 6414385813  # الـ ID الخاص بك كمدير أعلى للنظام

# 🔑 كلمة المرور الخاصة بالمشتركين
SECRET_PASSWORD = "Sudani"

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
        self.wfile.write(b"Ministry of Defense Secure Radar is Active")

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
# 🗄️ إدارة قواعد البيانات والصلاحيات
# ============================================
class DataManager:
    def __init__(self, filename="radar_data.json"):
        self.filename = filename
        self.data = self.load_data()

    def load_data(self):
        try:
            with open(self.filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "authorized_users" not in data:
                    data["authorized_users"] = [ADMIN_ID]
                return data
        except:
            return {
                "authorized_users": [ADMIN_ID],
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

    def is_authorized(self, user_id):
        return user_id == ADMIN_ID or user_id in self.data.get("authorized_users", [])

    def authorize_user(self, user_id):
        if user_id not in self.data["authorized_users"]:
            self.data["authorized_users"].append(user_id)
            self.save_data()

    def add_sub(self, chat_id, first_name):
        self.data["subscribers"][str(chat_id)] = {"name": first_name, "joined": get_sudan_time()}
        self.save_data()
        
    def remove_sub(self, chat_id):
        if str(chat_id) in self.data["subscribers"]:
            del self.data["subscribers"][str(chat_id)]
            self.save_data()

# ============================================
# 📡 دالة جلب الأخبار وتجاوز الحظر
# ============================================
def fetch_rss(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return feedparser.parse(response.content)
    except Exception:
        pass
    return feedparser.parse("")

# ============================================
# 🔍 محرك الرصد العسكري المفلتر لوزارة الدفاع
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
            "سودان تربيون": "https://sudantribune.net/feed/"
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
                if art: found_articles.append(art)

        for query in self.google_queries:
            encoded_query = urllib.parse.quote(query)
            
