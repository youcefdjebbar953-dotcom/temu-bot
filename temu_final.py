import logging
import re
import json
import aiohttp
import sqlite3
import base64
import anthropic
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ============================================================
# ⚙️  إعدادات — عدّل هذا فقط
# ============================================================
BOT_TOKEN    = "8717436486:AAFba4CGLY2kot8fwhrrFmYbodo718UMFY4"
OWNER_ID     = None        # يتحدد تلقائياً أول مرة تكتب /start
DOLLAR_RATE  = 260
PROFIT_FIXED = 300
SHOP_NAME    = "Youcef Shop DZ 🛍️"
BOT_USERNAME = "@youcef_shop_bot"
OWNER_TG     = "@youcef2333"
WHATSAPP     = "213560560835"
INSTAGRAM    = "youcef_shop_dz"
DELIVERY_MIN = 15
DELIVERY_MAX = 25
DB_FILE      = "orders.db"
CLAUDE_KEY   = "YOUR_CLAUDE_API_KEY"  # ← احصل عليه من console.anthropic.com
# ============================================================

WAITING_PROOF = 1
WAITING_MSG   = 2

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

STATUS_LABELS = {
    "pending":    "⏳ في الانتظار",
    "confirmed":  "✅ مؤكد",
    "processing": "🔄 قيد المعالجة",
    "shipped":    "🚚 تم الشحن",
    "delivered":  "📦 وصل",
    "cancelled":  "❌ ملغي",
}

# ============================================================
# 🗄️  قاعدة البيانات
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_num TEXT UNIQUE,
        user_id INTEGER,
        username TEXT,
        full_name TEXT,
        product_price_usd REAL,
        total_dzd INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        updated_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )''')
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_setting(key, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()

def create_order(user_id, username, full_name, usd, total):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM orders")
    num = c.fetchone()[0] + 1
    order_num = f"YS{num:04d}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute('''INSERT INTO orders
        (order_num,user_id,username,full_name,product_price_usd,total_dzd,status,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (order_num, user_id, username or "—", full_name, usd, total, "pending", now, now))
    conn.commit()
    conn.close()
    return order_num

def get_order(order_num):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE order_num=?", (order_num.upper(),))
    row = c.fetchone()
    conn.close()
    return row

def get_user_orders(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 5", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def update_order_status(order_num, status):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute("UPDATE orders SET status=?,updated_at=? WHERE order_num=?",
              (status, now, order_num.upper()))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM orders"); total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'"); pending = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='processing'"); processing = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='shipped'"); shipped = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='delivered'"); delivered = c.fetchone()[0]
    c.execute("SELECT SUM(total_dzd) FROM orders WHERE status='delivered'"); revenue = c.fetchone()[0] or 0
    conn.close()
    return total, pending, processing, shipped, delivered, revenue

# ============================================================
# 🧮  حساب السعر
# ============================================================
def calc(usd: float) -> dict:
    base  = usd * DOLLAR_RATE
    total = base + PROFIT_FIXED
    return {
        "usd": usd,
        "base": round(base),
        "profit": PROFIT_FIXED,
        "total": round(total / 50) * 50,
    }

# ============================================================
# ⌨️  لوحات المفاتيح
# ============================================================
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧮 احسب سعر منتج", callback_data="calc_price")],
        [
            InlineKeyboardButton("💳 معلومات الدفع",  callback_data="payment"),
            InlineKeyboardButton("📦 طلباتي",         callback_data="my_orders"),
        ],
        [
            InlineKeyboardButton("❓ كيفاش نطلب؟",   callback_data="how_to"),
            InlineKeyboardButton("📞 تواصل معنا",     callback_data="contact"),
        ],
        [InlineKeyboardButton("⭐ قيّم خدمتنا",      callback_data="rate_us")],
    ])

def back_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="main_menu")]
    ])

def order_kb(usd, total):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ إيه، نطلب!", callback_data=f"confirm_{usd}_{total}")],
        [InlineKeyboardButton("❌ لا شكراً",   callback_data="main_menu")],
    ])

def contact_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 واتساب",    url=f"https://wa.me/{WHATSAPP}")],
        [InlineKeyboardButton("📸 انستغرام",  url=f"https://instagram.com/{INSTAGRAM}")],
        [InlineKeyboardButton("✉️ راسلنا هنا", callback_data="send_msg")],
        [InlineKeyboardButton("🔙 رجوع",      callback_data="main_menu")],
    ])

def owner_kb(user_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تم الشراء",    callback_data=f"done_{user_id}"),
            InlineKeyboardButton("🔄 قيد المعالجة", callback_data=f"processing_{user_id}"),
        ],
        [InlineKeyboardButton("❌ رفض الطلب",       callback_data=f"reject_{user_id}")],
    ])

# ============================================================
# 🤖  رسالة الترحيب
# ============================================================
WELCOME = (
    "╔══════════════════════╗\n"
    "   🛍️  {shop}  \n"
    "╚══════════════════════╝\n\n"
    "مرحباً {name}! 👋\n\n"
    "نجيبلك أي منتج من تيمو\n"
    "ويوصلك لباب دارك! 🚀\n\n"
    "💡 ابعث سكرينشوت السلة وشوف السعر فوراً! 📸\n\n"
    "اختر من القائمة 👇"
)

# ============================================================
# 📨  Handlers
# ============================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not get_setting("admin_id"):
        set_setting("admin_id", user.id)
        logger.info(f"Admin set: {user.id}")
    admin_id = get_setting("admin_id")
    try:
        await ctx.bot.send_message(
            int(admin_id),
            f"👤 *مستخدم جديد!*\nالاسم: {user.full_name}\n@{user.username or '—'}\nID: `{user.id}`",
            parse_mode="Markdown",
        )
    except Exception:
        pass
    await update.message.reply_text(
        WELCOME.format(shop=SHOP_NAME, name=user.first_name),
        reply_markup=main_kb(),
    )
    return ConversationHandler.END

async def cmd_my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    orders = get_user_orders(update.effective_user.id)
    if not orders:
        await update.message.reply_text(
            "📭 ما عندكش طلبات بعد!\n\nاضغط 🧮 احسب سعر منتج لتبدأ 😊",
            reply_markup=main_kb(),
        )
        return
    msg = "📦 *آخر طلباتك:*\n\n"
    for o in orders:
        msg += f"🔖 `{o[1]}` — {STATUS_LABELS.get(o[7], o[7])}\n💰 {o[6]:,} دج | 📅 {o[8]}\n━━━━━━━\n"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=back_kb())

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admin_id = get_setting("admin_id")
    if str(update.effective_user.id) != str(admin_id):
        await update.message.reply_text("❌ ما عندكش صلاحية!")
        return
    total, pending, processing, shipped, delivered, revenue = get_stats()
    await update.message.reply_text(
        f"👨‍💼 *لوحة الأدمن — {SHOP_NAME}*\n\n"
        f"📋 إجمالي الطلبات: `{total}`\n"
        f"⏳ في الانتظار: `{pending}`\n"
        f"🔄 قيد المعالجة: `{processing}`\n"
        f"🚚 مشحونة: `{shipped}`\n"
        f"📦 موصّلة: `{delivered}`\n"
        f"💰 الإيرادات: `{revenue:,} دج`\n\n"
        f"لتحديث حالة طلب:\n`/status YS0001 shipped`",
        parse_mode="Markdown",
    )

async def cmd_update_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admin_id = get_setting("admin_id")
    if str(update.effective_user.id) != str(admin_id):
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("الاستخدام: `/status YS0001 shipped`", parse_mode="Markdown")
        return
    order_num, new_status = args[0].upper(), args[1].lower()
    if new_status not in STATUS_LABELS:
        await update.message.reply_text(f"الحالات: {', '.join(STATUS_LABELS.keys())}")
        return
    order = get_order(order_num)
    if not order:
        await update.message.reply_text(f"❌ ما لقيتش `{order_num}`", parse_mode="Markdown")
        return
    update_order_status(order_num, new_status)
    try:
        await ctx.bot.send_message(
            order[2],
            f"📦 *تحديث طلبك `{order_num}`*\n\n"
            f"الحالة: {STATUS_LABELS[new_status]}\n\n"
            f"للمتابعة: {OWNER_TG}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Notify error: {e}")
    await update.message.reply_text(
        f"✅ تم تحديث `{order_num}` → {STATUS_LABELS[new_status]}", parse_mode="Markdown"
    )

# ============================================================
# 🔘  Callback Handler
# ============================================================
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    user = q.from_user
    admin_id = get_setting("admin_id")

    if data == "main_menu":
        await q.edit_message_text(
            WELCOME.format(shop=SHOP_NAME, name=user.first_name),
            reply_markup=main_kb(),
        )
        return ConversationHandler.END

    elif data == "calc_price":
        await q.edit_message_text(
            "🧮 *احسب سعر منتج*\n\n"
            "📸 ابعث سكرينشوت من السلة تاع تيمو\n"
            "والبوت يحسبلك السعر تلقائياً! ✨\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "أو إذا تعرف السعر ابعثه هكذا:\n"
            "`سعر 12.99`",
            reply_markup=back_kb(),
            parse_mode="Markdown",
        )

    elif data == "payment":
        await q.edit_message_text(
            "💳 *معلومات الدفع*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "للدفع وتأكيد طلبك تواصل مع المطور مباشرة:\n\n"
            f"📱 تلغرام: {OWNER_TG}\n"
            f"💬 واتساب: {WHATSAPP[3:]}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "⏰ سنرد عليك في أقرب وقت ✅",
            reply_markup=back_kb(),
            parse_mode="Markdown",
        )

    elif data == "my_orders":
        orders = get_user_orders(user.id)
        if not orders:
            await q.edit_message_text(
                "📭 ما عندكش طلبات بعد!\n\nاضغط 🧮 احسب سعر منتج لتبدأ 😊",
                reply_markup=main_kb(),
            )
            return
        msg = "📦 *آخر طلباتك:*\n\n"
        for o in orders:
            msg += f"🔖 `{o[1]}` — {STATUS_LABELS.get(o[7], o[7])}\n💰 {o[6]:,} دج | 📅 {o[8]}\n━━━━━━━\n"
        await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=back_kb())

    elif data == "how_to":
        await q.edit_message_text(
            "🛒 *كيفاش تطلب؟*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "1️⃣ اضغط 🧮 *احسب سعر منتج*\n\n"
            "2️⃣ شوف السعر من السلة وابعثه:\n"
            "   `سعر 12.99`\n\n"
            "3️⃣ اضغط ✅ *إيه نطلب*\n\n"
            "4️⃣ ادفع عبر بريدي موب 💳\n\n"
            "5️⃣ ابعث صورة الوصل 📸\n\n"
            "6️⃣ انتظر تأكيد الشراء 🎉\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🚚 التوصيل: {DELIVERY_MIN}–{DELIVERY_MAX} يوم\n"
            "📍 كل ولايات الجزائر",
            reply_markup=back_kb(),
            parse_mode="Markdown",
        )

    elif data == "contact":
        await q.edit_message_text(
            "📞 *تواصل معنا*\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📱 تلغرام: {OWNER_TG}\n"
            f"💬 واتساب: {WHATSAPP[3:]}\n"
            f"📸 انستغرام: @{INSTAGRAM}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "⏰ أوقات الرد: 8ص — 11م\n"
            "أو راسلنا مباشرة 👇",
            reply_markup=contact_kb(),
            parse_mode="Markdown",
        )

    elif data == "send_msg":
        await q.edit_message_text(
            "✉️ *اكتب رسالتك*\n\nسنرد عليك في أقرب وقت ✅",
            reply_markup=back_kb(),
            parse_mode="Markdown",
        )
        return WAITING_MSG

    elif data == "rate_us":
        await q.edit_message_text(
            "⭐ *قيّم خدمتنا*\n\nرأيك يهمنا كثيراً!",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⭐",         callback_data="rate_1"),
                    InlineKeyboardButton("⭐⭐",        callback_data="rate_2"),
                    InlineKeyboardButton("⭐⭐⭐",      callback_data="rate_3"),
                ],
                [
                    InlineKeyboardButton("⭐⭐⭐⭐",    callback_data="rate_4"),
                    InlineKeyboardButton("⭐⭐⭐⭐⭐",  callback_data="rate_5"),
                ],
                [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")],
            ]),
        )

    elif data.startswith("rate_"):
        stars = int(data.split("_")[1])
        emojis = ["😞", "😐", "🙂", "😊", "🤩"]
        if admin_id:
            try:
                await ctx.bot.send_message(
                    int(admin_id),
                    f"⭐ تقييم جديد: {'⭐'*stars} {emojis[stars-1]}\n"
                    f"من: {user.full_name} (@{user.username or '—'})",
                )
            except Exception:
                pass
        await q.edit_message_text(
            f"{'⭐'*stars} شكراً على تقييمك {emojis[stars-1]}\n\nرأيك يساعدنا نتحسنوا أكثر! 🙏",
            reply_markup=main_kb(),
        )

    elif data.startswith("confirm_"):
        parts = data.split("_")
        usd   = float(parts[1])
        total = int(parts[2])
        order_num = create_order(user.id, user.username, user.full_name, usd, total)
        if admin_id:
            try:
                await ctx.bot.send_message(
                    int(admin_id),
                    f"🔔 *طلب جديد!*\n\n"
                    f"🔖 رقم الطلب: `{order_num}`\n"
                    f"👤 {user.full_name} (@{user.username or '—'})\n"
                    f"💰 {total:,} دج\n"
                    f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    parse_mode="Markdown",
                    reply_markup=owner_kb(user.id),
                )
            except Exception as e:
                logger.warning(f"Admin notify: {e}")
        await q.edit_message_text(
            f"🎉 *تم تسجيل طلبك!*\n\n"
            f"🔖 رقم طلبك: `{order_num}`\n"
            f"💰 المبلغ: *{total:,} دج*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"للدفع وتأكيد طلبك تواصل معنا:\n\n"
            f"📱 تلغرام: {OWNER_TG}\n"
            f"💬 واتساب: {WHATSAPP[3:]}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"_احفظ رقم طلبك: `{order_num}`_",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    # ── أزرار الأدمن ──
    elif data.startswith("done_") and str(user.id) == str(admin_id):
        uid = int(data.split("_")[1])
        try:
            await ctx.bot.send_message(
                uid,
                "🎉 *تم شراء منتجك بنجاح!*\n\n"
                "طلبيتك في طريقها إليك 📦\n"
                f"للمتابعة: {OWNER_TG}\n\n"
                "شكراً لثقتك في Youcef Shop ❤️",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        await q.edit_message_text("✅ تم إشعار العميل بالشراء")

    elif data.startswith("processing_") and str(user.id) == str(admin_id):
        uid = int(data.split("_")[1])
        try:
            await ctx.bot.send_message(
                uid,
                "🔄 *طلبك قيد المعالجة*\n\n"
                "وصلنا وصل الدفع تاعك ✅\n"
                "سنخبرك فور إتمام الشراء 🛍️",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        await q.edit_message_text("🔄 تم إشعار العميل بالمعالجة")

    elif data.startswith("reject_") and str(user.id) == str(admin_id):
        uid = int(data.split("_")[1])
        try:
            await ctx.bot.send_message(
                uid,
                "⚠️ *مشكلة في طلبك*\n\n"
                f"تواصل معنا للتوضيح: {OWNER_TG}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        await q.edit_message_text("❌ تم إشعار العميل بالرفض")

# ============================================================
# 💬  معالجة الرسائل النصية
# ============================================================
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    manual = re.match(r'^(?:سعر|price)\s+([\d.,]+)$', text, re.IGNORECASE)
    if manual:
        try:
            usd = float(manual.group(1).replace(",", "."))
            if usd <= 0: raise ValueError
            p = calc(usd)
            await update.message.reply_text(
                f"🧮 *حساب السعر*\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💵 سعر تيمو: `${p['usd']:.2f}`\n"
                f"💱 سعر الدولار: `{DOLLAR_RATE} دج`\n"
                f"📊 السعر بالدينار: `{p['base']:,} دج`\n"
                f"💰 رسوم الخدمة: `+{p['profit']:,} دج`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏷️ *السعر الإجمالي: `{p['total']:,} دج`*\n\n"
                f"🚚 التوصيل: {DELIVERY_MIN}–{DELIVERY_MAX} يوم\n"
                f"📍 كل ولايات الجزائر\n\n"
                f"*تحب تطلب؟* 👇",
                parse_mode="Markdown",
                reply_markup=order_kb(p['usd'], p['total']),
            )
        except (ValueError, OverflowError):
            await update.message.reply_text("❌ مثال: `سعر 12.99`", parse_mode="Markdown")
        return

    if "temu.com" in text.lower():
        await update.message.reply_text(
            "✅ *شفت الرابط!*\n\n"
            "📸 حط المنتج في السلة وخذ سكرينشوت\n"
            "وابعثه هنا — البوت يحسب السعر تلقائياً! ✨",
            parse_mode="Markdown",
            reply_markup=back_kb(),
        )
        return

    await update.message.reply_text(
        "👋 ابعثلي سكرينشوت من سلة تيمو\n"
        "والبوت يحسب السعر تلقائياً! 📸\n\n"
        "أو ابعث السعر يدوياً: `سعر 12.99`\n\n"
        "للمساعدة: /help",
        parse_mode="Markdown",
        reply_markup=main_kb(),
    )

# ── وصل الدفع (صورة) ──
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admin_id = get_setting("admin_id")
    user = update.effective_user

    # تحقق إذا الصورة سكرينشوت سلة تيمو أو وصل دفع
    caption = (update.message.caption or "").lower()
    is_cart = any(w in caption for w in ["سلة", "cart", "سعر", "price", "تيمو", "temu"])

    # حاول تقرأ السعر من الصورة بالذكاء الاصطناعي
    thinking_msg = await update.message.reply_text("🔍 جاري قراءة الصورة...")
    usd_price = None

    try:
        # تحويل الصورة لـ base64
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")

        # Claude Vision API
        client = anthropic.Anthropic(api_key=CLAUDE_KEY)
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "This is a screenshot from Temu or a shopping cart. Extract ONLY the product price in USD (numbers only, no $ sign). If multiple prices exist, take the main product price. Reply with ONLY the number like: 12.99 — if no price found reply: NONE"
                    }
                ],
            }]
        )
        result = response.content[0].text.strip()
        if result != "NONE":
            usd_price = float(re.sub(r"[^\d.]", "", result))
    except Exception as e:
        logger.warning(f"Claude Vision error: {e}")

    if usd_price and usd_price > 0:
        p = calc(usd_price)
        await thinking_msg.edit_text(
            f"✅ *قرأت السعر من صورتك!*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💵 السعر المكتشف: `${p['usd']:.2f}`\n"
            f"💱 سعر الدولار: `{DOLLAR_RATE} دج`\n"
            f"📊 السعر بالدينار: `{p['base']:,} دج`\n"
            f"💰 رسوم الخدمة: `+{p['profit']:,} دج`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ *السعر الإجمالي: `{p['total']:,} دج`*\n\n"
            f"🚚 التوصيل: {DELIVERY_MIN}–{DELIVERY_MAX} يوم\n\n"
            f"*تحب تطلب؟* 👇",
            parse_mode="Markdown",
            reply_markup=order_kb(p['usd'], p['total']),
        )
    else:
        # صورة وصل دفع أو ما قدرش يقرأ السعر
        if admin_id:
            try:
                await ctx.bot.forward_message(
                    chat_id=int(admin_id),
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.message_id,
                )
                await ctx.bot.send_message(
                    int(admin_id),
                    f"📸 صورة من: {user.full_name} (@{user.username or '—'})\nID: `{user.id}`",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Forward: {e}")
        await thinking_msg.edit_text(
            "✅ *تم استلام الصورة!*\n\n"
            "📦 أرسلنا طلبك للأدمن\n\n"
            "إذا كانت سكرينشوت سلة تيمو —\n"
            "ابعث السعر يدوياً هكذا: `سعر 12.99`",
            parse_mode="Markdown",
            reply_markup=main_kb(),
        )

# ── رسالة مباشرة للأدمن ──
async def on_direct_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admin_id = get_setting("admin_id")
    user = update.effective_user
    if admin_id:
        try:
            await ctx.bot.send_message(
                int(admin_id),
                f"✉️ رسالة من {user.full_name} (@{user.username or '—'}):\n\n{update.message.text}",
            )
        except Exception:
            pass
    await update.message.reply_text(
        "✅ وصلت رسالتك!\nسنرد عليك قريباً 😊",
        reply_markup=main_kb(),
    )
    return ConversationHandler.END

# ============================================================
# 🚀  Main
# ============================================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(on_callback),
        ],
        states={
            WAITING_PROOF: [MessageHandler(filters.PHOTO, on_photo)],
            WAITING_MSG:   [MessageHandler(filters.TEXT & ~filters.COMMAND, on_direct_msg)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("orders",  cmd_my_orders))
    app.add_handler(CommandHandler("admin",  cmd_admin))
    app.add_handler(CommandHandler("status",  cmd_update_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))

    logger.info("✅ البوت شغال...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
