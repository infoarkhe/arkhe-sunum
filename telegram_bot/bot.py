"""
Arkhe Enerjimetre — Telegram → DWIN Köprüsü
Sıra sistemi + timeout ile çoklu kullanıcı desteği.

Gereksinim:
    pip install python-telegram-bot pyserial
"""

# ─── AYARLAR ───
BOT_TOKEN    = "8157710360:AAH0gG6KcYzMwYgrboU4v9hjhV_kJchZF4o"
USE_TCP      = True
TCP_HOST     = "127.0.0.1"
TCP_PORT     = 8888
SERIAL_PORT  = "COM14"
BAUD_RATE    = 115200
MENU_FILE    = "menu_tree.json"
START_PAGE   = 1
TIMEOUT_SEC  = 60              # Kullanıcı 60 sn sessiz kalırsa sıra geçer
LIVE_URL     = "https://vdo.ninja/?view=arkhesunum&room=azad&solo"  # Canlı yayın linki
# ────────────────

import json
import logging
import socket
import time
from collections import deque

try:
    import serial
except ImportError:
    serial = None

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger("ArkheBot")

# ─── MENÜ YÜKLE ───
with open(MENU_FILE, "r", encoding="utf-8") as f:
    MENU = json.load(f)
log.info(f"{len(MENU)} sayfa yüklendi.")

# ─── BAĞLANTI ───
ser = None
if not USE_TCP:
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        log.info(f"Serial port açıldı: {SERIAL_PORT} @ {BAUD_RATE}")
    except Exception as e:
        log.warning(f"Serial port açılamadı: {e}")
        ser = None

# ─── SIRA SİSTEMİ ───
active_user = None          # (chat_id, username, last_activity_time)
queue = deque()             # [(chat_id, username), ...]


def is_active(chat_id: int) -> bool:
    return active_user is not None and active_user[0] == chat_id


def get_queue_position(chat_id: int) -> int:
    """Sıradaki pozisyon (1-based), 0 = sırada değil."""
    for i, (cid, _) in enumerate(queue):
        if cid == chat_id:
            return i + 1
    return 0


def activate_user(chat_id: int, username: str):
    global active_user
    active_user = (chat_id, username, time.time())
    log.info(f"Aktif kullanıcı: {username} ({chat_id})")


def touch_activity():
    """Aktif kullanıcının son etkinlik zamanını güncelle."""
    global active_user
    if active_user:
        active_user = (active_user[0], active_user[1], time.time())


def release_active():
    """Aktif kullanıcıyı serbest bırak."""
    global active_user
    old = active_user
    active_user = None
    if old:
        log.info(f"Kullanıcı serbest bırakıldı: {old[1]} ({old[0]})")
    return old


async def promote_next(context: ContextTypes.DEFAULT_TYPE):
    """Sıradaki kişiyi aktif yap ve bilgilendir."""
    global active_user
    if not queue:
        return
    next_id, next_name = queue.popleft()
    activate_user(next_id, next_name)
    try:
        keyboard = build_keyboard(str(START_PAGE))
        page = MENU.get(str(START_PAGE))
        title = page.get("title", "Ana Ekran") if page else "Ana Ekran"
        send_to_dwin(START_PAGE)
        await context.bot.send_message(
            chat_id=next_id,
            text=f"🟢 *Sıra sizde!* Enerjimetre'yi kontrol edebilirsiniz.\n\n📺 *{title}*",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception as e:
        log.error(f"Sıradaki kullanıcıya mesaj gönderilemedi: {e}")
        release_active()
        await promote_next(context)


# ─── TIMEOUT KONTROLÜ ───
async def check_timeout(context: ContextTypes.DEFAULT_TYPE):
    global active_user
    if active_user is None:
        return
    chat_id, username, last_time = active_user
    elapsed = time.time() - last_time
    if elapsed >= TIMEOUT_SEC:
        log.info(f"Timeout: {username} ({chat_id}) — {elapsed:.0f} sn")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ *{TIMEOUT_SEC} saniye* boyunca işlem yapmadığınız için sıra bir sonraki kullanıcıya geçti.\n\nTekrar denemek için /start gönderin.",
                parse_mode="Markdown"
            )
        except:
            pass
        release_active()
        await promote_next(context)


# ─── DWIN ───
def dwin_page_command(page_id: int) -> bytes:
    return bytes([0x5A, 0xA5, 0x07, 0x82, 0x00, 0x84, 0x5A, 0x01, 0x00, page_id])


def send_to_dwin(page_id: int):
    cmd = dwin_page_command(page_id)
    hex_str = " ".join(f"{b:02X}" for b in cmd)
    log.info(f"DWIN → page {page_id} | {hex_str}")
    try:
        if USE_TCP:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((TCP_HOST, TCP_PORT))
                s.sendall(cmd)
            return
        elif ser and ser.is_open:
            ser.write(cmd)
    except Exception as e:
        log.error(f"Yazma hatası: {e}")


# ─── TELEGRAM ───
def build_keyboard(page_key: str) -> InlineKeyboardMarkup:
    page = MENU.get(page_key)
    if not page:
        return InlineKeyboardMarkup([])
    rows = []
    row = []
    for btn in page["buttons"]:
        row.append(InlineKeyboardButton(btn["text"], callback_data=str(btn["target"])))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    # Bırak butonu ekle
    rows.append([InlineKeyboardButton("🔴 Bırak", callback_data="_release")])
    return InlineKeyboardMarkup(rows)


async def show_page(update: Update, page_key: str, edit: bool = False):
    page = MENU.get(page_key)
    if not page:
        text = "⚠ Sayfa bulunamadı."
        if edit:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    title = page.get("title", page_key)
    dwin_page = page.get("dwin_page", int(page_key))
    remaining = TIMEOUT_SEC - int(time.time() - active_user[2]) if active_user else TIMEOUT_SEC
    text = f"📺 *{title}*\n⏱ _{remaining} sn kaldı_"
    keyboard = build_keyboard(page_key)

    send_to_dwin(dwin_page)
    touch_activity()

    if edit:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    username = update.effective_user.first_name or str(chat_id)

    # Zaten aktif mi?
    if is_active(chat_id):
        await show_page(update, str(START_PAGE), edit=False)
        return

    # Aktif kullanıcı yok → hemen aktif yap
    if active_user is None:
        activate_user(chat_id, username)
        await update.message.reply_text(
            f"🟢 *Hoş geldiniz {username}!*\nEnerjimetre'yi kontrol edebilirsiniz.\n⏱ _{TIMEOUT_SEC} sn_ süreniz var, her tuşa basışta süre sıfırlanır.",
            parse_mode="Markdown"
        )
        await show_page(update, str(START_PAGE), edit=False)
        return

    # Aktif kullanıcı var → sıraya ekle
    pos = get_queue_position(chat_id)
    if pos == 0:
        queue.append((chat_id, username))
        pos = len(queue)

    await update.message.reply_text(
        f"⏳ *Cihaz şu an kullanılıyor.*\n"
        f"Sıranız: *{pos}*\n\n"
        f"Beklerken canlı yayından cihazı izleyebilirsiniz:\n"
        f"🔴 [{LIVE_URL}]({LIVE_URL})",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_user.id

    # Bırak butonu
    if query.data == "_release":
        if is_active(chat_id):
            release_active()
            await query.edit_message_text("✅ Cihazı bıraktınız. Tekrar kullanmak için /start gönderin.")
            await promote_next(context)
        return

    # Aktif kullanıcı değilse engelle
    if not is_active(chat_id):
        pos = get_queue_position(chat_id)
        if pos > 0:
            await query.answer(f"⏳ Sıranız: {pos}. Bekleyin.", show_alert=True)
        else:
            await query.answer("Önce /start gönderin.", show_alert=True)
        return

    target_page = query.data
    log.info(f"Click: {update.effective_user.first_name} → page {target_page}")
    await show_page(update, target_page, edit=True)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_user.id
    if is_active(chat_id):
        release_active()
        await update.message.reply_text("✅ Cihazı bıraktınız.")
        await promote_next(context)
    else:
        # Sıradan çık
        pos = get_queue_position(chat_id)
        if pos > 0:
            queue.remove((chat_id, update.effective_user.first_name or str(chat_id)))
            await update.message.reply_text("✅ Sıradan çıktınız.")
        else:
            await update.message.reply_text("Zaten aktif değilsiniz.")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(on_button))

    # Timeout kontrolü — her 10 saniyede bir
    app.job_queue.run_repeating(check_timeout, interval=10, first=10)

    log.info("Bot başlatıldı (sıra sistemi aktif).")
    app.run_polling()


if __name__ == "__main__":
    main()
