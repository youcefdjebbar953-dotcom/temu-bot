import logging
import re
import json
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ============================================================
# ⚙️  إعدادات
# ============================================================
BOT_TOKEN = "8717436486:AAFba4CGLY2kot8fwhrrFmYbodo718UMFY4"
DOLLAR_RATE = 260
PROFIT_FIXED = 300
DELIVERY_DAYS_MIN = 15
DELIVERY_DAYS_MAX = 25
OWNER_USERNAME = "@youcef2333"
WHATSAPP_NUMBER = "213560560835"
INSTAGRAM = "Youcef Shop DZ"
CCP = "0028902673 كلي 80"
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# -------- حساب السعر --------
def calculate_price(usd_price: float) -> dict:
    price_dzd = usd_price * DOLLAR_RATE
    final = price_dzd + PROFIT_FIXED
    return {
        "usd": usd_price,
        "dzd_base": round(price_dzd),
        "profit": PROFIT_FIXED,
        "final": round(final),
        "final_rounded": round(final / 50) * 50,
    }

# -------- استخراج سعر من رابط تيمو --------
async def fetch_temu_price(url: str) -> float | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

        ld_matches = re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
        for block in ld_matches:
            try:
                data = json.loads(block)
                offers = data.get("offers") or data.get("Offers")
                if isinstance(offers, dict):
                    price = offers.get("price") or offers.get("lowPrice")
                    if price:
                        return float(price)
                if isinstance(offers, list) and offers:
                    price = offers[0].get("price")
                    if price:
                        return float(price)
            except Exception:
                pass

        m = re.search(r'property="product:price:amount"\s+content="([\d.]+)"', html)
        if m:
            return float(m.group(1))

        m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
        if m:
            return float(m.group(1))

    except Exception as e:
        logger.warning(f"Fetch error: {e}")
    return None

# -------- بناء رسالة السعر --------
def build_price_message(prices: dict, url: str = None) -> tuple:
    url_line = f"🔗 {url}\n\n" if url else ""
    msg = (
        f"{url_line}"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 *سعر تيمو:* `${prices['usd']:.2f}`\n"
        f"💱 *سعر الدولار:* `{DOLLAR_RATE} دج`\n"
        f"📊 *السعر بالدينار:* `{prices['dzd_base']:,} دج`\n"
        f"💰 *رسوم الخدمة:* `+{prices['profit']:,} دج`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ *السعر الإجمالي: `{prices['final_rounded']:,} دج`*\n\n"
        f"🚚 مدة التوصيل: {DELIVERY_DAYS_MIN}–{DELIVERY_DAYS_MAX} يوم\n"
        f"📍 التوصيل لكل ولايات الجزائر\n\n"
        f"*تحب تطلب؟* 👇"
    )
    kb = [[
        InlineKeyboardButton("✅ إيه، نطلب!", callback_data="order_yes"),
        InlineKeyboardButton("❌ لا، شكراً", callback_data="order_no"),
    ]]
    return msg, InlineKeyboardMarkup(kb)

# -------- رسالة الترحيب --------
WELCOME_MSG = (
    "👋 *أهلاً بك في Youcef Shop DZ* 🛍️\n\n"
    "نطلب ليك أي منتج من تيمو ويوصلك لباب دارك! 🚀\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📌 *كيفاش تطلب؟*\n"
    "1️⃣ دور على المنتج في [temu.com](https://www.temu.com)\n"
    "2️⃣ شوف السعر من السلة وابعثه هكذا: `سعر 12.99`\n"
    "3️⃣ اضغط *إيه نطلب* وحوّل عبر CCP ✅\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "⚡ جاهز — ابعث السعر!"
)

# -------- /start --------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🛍️ كيفاش نطلب؟", callback_data="how"),
         InlineKeyboardButton("📞 تواصل معنا", callback_data="contact")],
        [InlineKeyboardButton("💱 سعر الدولار", callback_data="rate")],
    ]
    await update.message.reply_text(
        WELCOME_MSG,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
        disable_web_page_preview=True,
    )

# -------- /help --------
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 *مساعدة*\n\n"
        "• ابعث `سعر 15.99` → يحسبلك السعر الإجمالي\n"
        "• /start → الصفحة الرئيسية\n"
        "• /rate → سعر الدولار\n"
        "• /contact → بياناتنا\n\n"
        f"📞 تواصل مباشر: {OWNER_USERNAME}",
        parse_mode="Markdown",
    )

# -------- /rate --------
async def cmd_rate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"💱 *سعر الدولار المعتمد*\n\n`1 USD = {DOLLAR_RATE} دج`",
        parse_mode="Markdown",
    )

# -------- /contact --------
async def cmd_contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📲 *تواصل معنا*\n\n"
        f"📱 تلغرام: {OWNER_USERNAME}\n"
        f"💬 واتساب: [اضغط هنا](https://wa.me/{WHATSAPP_NUMBER})\n"
        f"📸 انستغرام: {INSTAGRAM}\n\n"
        f"⏰ أوقات الرد: 8 صباحاً – 11 مساءً\n"
        f"📍 الخدمة: كل ولايات الجزائر",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

# -------- Callbacks --------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "order_yes":
        await q.message.reply_text(
            f"🎉 *ممتاز! إليك بيانات الدفع:*\n\n"
            f"💳 *CCP:* `{CCP}`\n\n"
            f"📌 حوّل المبلغ وابعث صورة الوصل هنا\n"
            f"سيتم تأكيد طلبك في أقرب وقت ✅",
            parse_mode="Markdown",
        )

    elif q.data == "order_no":
        await q.message.reply_text(
            f"لا بأس 😊 — إذا غيّرت رأيك ابعث السعر مرة أخرى!\n"
            f"أي سؤال: {OWNER_USERNAME}",
        )

    elif q.data == "how":
        await q.message.reply_text(
            "📦 *طريقة الطلب خطوة خطوة*\n\n"
            "1️⃣ روح لـ temu.com\n"
            "2️⃣ دور على المنتج وشوف سعره من السلة\n"
            "3️⃣ ابعث السعر هنا: `سعر 12.99`\n"
            "4️⃣ اضغط ✅ إيه نطلب\n"
            "5️⃣ حوّل عبر CCP وابعث الوصل 📸\n"
            "6️⃣ يوصلك خلال 15-25 يوم 🚚",
            parse_mode="Markdown",
        )

    elif q.data == "contact":
        await cmd_contact(update, ctx)

    elif q.data == "rate":
        await cmd_rate(update, ctx)

# -------- معالجة الرسائل --------
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # سعر يدوي: "سعر 12.99" أو "price 12.99"
    manual = re.match(r'^(?:سعر|price)\s+([\d.,]+)$', text, re.IGNORECASE)
    if manual:
        try:
            usd = float(manual.group(1).replace(",", "."))
            if usd <= 0:
                raise ValueError
            prices = calculate_price(usd)
            msg, kb = build_price_message(prices)
            await update.message.reply_text(
                f"🧮 *حساب السعر*\n\n{msg}",
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except (ValueError, OverflowError):
            await update.message.reply_text(
                "❌ السعر غير صحيح.\nمثال: `سعر 12.99`",
                parse_mode="Markdown",
            )
        return

    # رابط تيمو — نطلب السعر من السلة
    if "temu.com" in text.lower():
        await update.message.reply_text(
            "✅ *شفت الرابط!*\n\n"
            "📌 افتح الرابط، حط المنتج في السلة وشوف السعر النهائي `$`\n"
            "بعدها ابعثهولي هكذا:\n\n"
            "`سعر 12.99`\n\n"
            "مثال: إذا شفت `$8.50` — ابعث `سعر 8.50` ✅",
            parse_mode="Markdown",
        )
        return

    # رسالة عامة
    await update.message.reply_text(
        "👋 ابعثلي السعر من السلة هكذا:\n`سعر 12.99`\n\nللمساعدة: /help",
        parse_mode="Markdown",
    )

# -------- معالجة الصور (وصل الدفع) --------
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *تم استلام الوصل!*\n\n"
        "📦 لقد أرسلنا طلبك للأدمن\n\n"
        f"تواصل معنا على تلغرام للمتابعة: {OWNER_USERNAME}",
        parse_mode="Markdown",
    )

# -------- Main --------
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("rate", cmd_rate))
    app.add_handler(CommandHandler("contact", cmd_contact))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    logger.info("✅ البوت شغال...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
