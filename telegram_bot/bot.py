"""
Arkhe Enerjimetre — Telegram → DWIN Köprüsü
Sıra sistemi + geri sayım + kullanım raporu.

Gereksinim:
    pip install "python-telegram-bot[job-queue]"
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
TIMEOUT_SEC  = 60
TICK_SEC     = 10
DEFAULT_PAGE = 11              # Her disconnection'da dönülecek sayfa (barchart)
LIVE_URL     = "https://vdo.ninja/?view=arkhesunum&room=azad&solo"
PENDING_FILE = "pending_users.json"  # Restart sonrası bildirim için
LOG_FILE     = "kullanim_raporu.md"
LOG_JSON     = "kullanim_raporu.json"
LOG_FILE_REL = "telegram_bot/kullanim_raporu.md"
LOG_JSON_REL = "telegram_bot/kullanim_raporu.json"
# ────────────────

import json
import logging
import socket
import time
import os
import subprocess
import threading
import signal
import asyncio
from datetime import datetime
from collections import deque, Counter

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

with open(MENU_FILE, "r", encoding="utf-8") as f:
    MENU = json.load(f)
log.info(f"{len(MENU)} sayfa yüklendi.")

ser = None
if not USE_TCP:
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    except Exception as e:
        log.warning(f"Serial port açılamadı: {e}")

# ─── STATE ───
active_user = None          # (chat_id, username, start_time, last_activity)
active_msg = None           # (chat_id, message_id, page_key)
session_log = []            # [(page_key, page_title, timestamp), ...]
queue = deque()
tick_job = None
session_counter = 0


def is_active(cid): return active_user and active_user[0] == cid

def queue_pos(cid):
    for i, (c, _) in enumerate(queue):
        if c == cid: return i + 1
    return 0

def remaining():
    if not active_user: return 0
    return max(0, TIMEOUT_SEC - int(time.time() - active_user[3]))

def touch():
    global active_user
    if active_user:
        active_user = (active_user[0], active_user[1], active_user[2], time.time())

def activate(cid, name):
    global active_user, session_log
    active_user = (cid, name, time.time(), time.time())
    session_log = []
    log.info(f"Aktif: {name} ({cid})")

def log_page(page_key):
    page = MENU.get(page_key)
    title = page.get("title", page_key) if page else page_key
    session_log.append((page_key, title, time.time()))

def release(reason="bilinmiyor"):
    global active_user, active_msg, tick_job
    old = active_user
    if old:
        write_report(old, reason)
    active_user = None
    active_msg = None
    if tick_job:
        tick_job.schedule_removal()
        tick_job = None
    send_dwin(DEFAULT_PAGE)
    return old


# ─── RAPORLAMA ───
def write_report(user_info, reason):
    global session_counter
    session_counter += 1

    cid, name, start_time, last_act = user_info
    end_time = time.time()
    duration = int(end_time - start_time)

    start_str = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.fromtimestamp(end_time).strftime("%H:%M:%S")

    # Sayfa istatistikleri
    pages_visited = [title for (_, title, _) in session_log]
    page_count = len(pages_visited)
    page_counter = Counter(pages_visited)
    most_visited = page_counter.most_common(1)[0] if page_counter else ("—", 0)

    # Sayfa bazlı süre hesapla
    page_durations = {}
    for i, (pkey, title, ts) in enumerate(session_log):
        if i + 1 < len(session_log):
            dur = session_log[i + 1][2] - ts
        else:
            dur = end_time - ts
        page_durations[title] = page_durations.get(title, 0) + dur

    top_duration = sorted(page_durations.items(), key=lambda x: -x[1])

    # Geçiş sırası
    journey = " → ".join(pages_visited) if pages_visited else "—"

    # ─── MD Rapor ───
    md = f"""
---

## Oturum #{session_counter} — {start_str}

| Bilgi | Değer |
|-------|-------|
| **Kullanıcı** | {name} |
| **Telegram ID** | `{cid}` |
| **Başlangıç** | {start_str} |
| **Bitiş** | {end_str} |
| **Süre** | {duration} sn ({duration // 60} dk {duration % 60} sn) |
| **Sonuç** | {reason} |
| **Toplam basış** | {page_count} |
| **En çok ziyaret** | {most_visited[0]} ({most_visited[1]}x) |

**Sayfa geçişleri:**
{journey}

**Sayfa bazlı süre:**
"""
    for title, dur in top_duration:
        md += f"- {title}: {int(dur)} sn\n"

    # Dosyaya ekle
    try:
        header_needed = not os.path.exists(LOG_FILE)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            if header_needed:
                f.write("# Enerjimetre — Kullanım Raporu\n\n")
                f.write("Her oturum ayrı ayrı kaydedilir.\n")
            f.write(md)
        log.info(f"Rapor yazıldı: oturum #{session_counter}")
    except Exception as e:
        log.error(f"Rapor yazma hatası: {e}")

    # ─── JSON Log ───
    json_entry = {
        "session": session_counter,
        "user": name,
        "telegram_id": cid,
        "start": start_str,
        "end": end_str,
        "duration_sec": duration,
        "reason": reason,
        "total_clicks": page_count,
        "most_visited": {"page": most_visited[0], "count": most_visited[1]},
        "journey": pages_visited,
        "page_durations": {k: round(v, 1) for k, v in page_durations.items()}
    }
    try:
        with open(LOG_JSON, "a", encoding="utf-8") as f:
            f.write(json.dumps(json_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"JSON log hatası: {e}")

    # ─── GitHub Push (background thread) ───
    git_push_async()


_git_lock = threading.Lock()

def _do_git_push():
    """Git add + commit + push (lock ile)."""
    with _git_lock:
        try:
            repo_dir = os.path.dirname(os.path.abspath(__file__))
            parent_dir = os.path.dirname(repo_dir)
            cmds = [
                ["git", "-C", parent_dir, "add", LOG_FILE_REL, LOG_JSON_REL],
                ["git", "-C", parent_dir, "-c", "user.email=info@arkhe.com",
                 "-c", "user.name=arkhe", "commit", "-m",
                 f"rapor: oturum #{session_counter}"],
                ["git", "-C", parent_dir, "push"],
            ]
            for cmd in cmds:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0 and "nothing to commit" not in result.stdout:
                    log.warning(f"Git: {' '.join(cmd[:4])}... → {result.stderr[:100]}")
                    break
            else:
                log.info("GitHub push başarılı.")
        except Exception as e:
            log.error(f"GitHub push hatası: {e}")


def git_push_sync():
    """Senkron push — bot kapanmadan tamamlanır."""
    _do_git_push()

def git_push_async():
    """Rapor dosyalarını background'da GitHub'a push et."""
    threading.Thread(target=_do_git_push, daemon=True).start()


# ─── DWIN ───
def dwin_cmd(pid): return bytes([0x5A, 0xA5, 0x07, 0x82, 0x00, 0x84, 0x5A, 0x01, 0x00, pid])

def send_dwin(pid):
    log.info(f"DWIN → {pid}")
    try:
        if USE_TCP:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2); s.connect((TCP_HOST, TCP_PORT)); s.sendall(dwin_cmd(pid))
        elif ser and ser.is_open:
            ser.write(dwin_cmd(pid))
    except Exception as e:
        log.error(f"DWIN hata: {e}")


# ─── KEYBOARD ───
def build_kb(page_key):
    page = MENU.get(page_key)
    if not page: return []
    rows = []
    row = []
    for btn in page["buttons"]:
        row.append(InlineKeyboardButton(btn["text"], callback_data=str(btn["target"])))
        if len(row) == 2: rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("🔴 Bırak", callback_data="_release")])
    return rows

def timer_label(rem):
    if rem <= 15: return f"🔴 {rem} sn"
    if rem <= 30: return f"🟡 {rem} sn"
    return f"⏱ {rem} sn"

def make_markup(page_key, rem):
    rows = build_kb(page_key)
    rows.insert(0, [InlineKeyboardButton(timer_label(rem), callback_data="_timer")])
    return InlineKeyboardMarkup(rows)

def page_text(page_key):
    page = MENU.get(page_key)
    return f"📺 *{page.get('title', page_key)}*" if page else "⚠"


# ─── TICK ───
async def on_tick(context):
    if not active_user or not active_msg: return
    rem = remaining()
    cid, mid, pkey = active_msg

    if rem <= 0:
        try:
            await context.bot.send_message(cid,
                f"⏰ *{TIMEOUT_SEC} sn* süreniz doldu.\nTekrar: /start",
                parse_mode="Markdown")
        except: pass
        release("timeout")
        await promote(context)
        return

    try:
        await context.bot.edit_message_reply_markup(
            chat_id=cid, message_id=mid,
            reply_markup=make_markup(pkey, rem))
    except: pass

def start_tick(context):
    global tick_job
    if tick_job: tick_job.schedule_removal()
    tick_job = context.job_queue.run_repeating(on_tick, interval=TICK_SEC, first=TICK_SEC)


# ─── PROMOTE ───
async def promote(context):
    if not queue: return
    nid, nname = queue.popleft()
    activate(nid, nname)
    page = MENU.get(str(START_PAGE))
    dp = page.get("dwin_page", START_PAGE) if page else START_PAGE
    send_dwin(dp)
    log_page(str(START_PAGE))
    try:
        msg = await context.bot.send_message(nid,
            f"🟢 *Sıra sizde {nname}!*\n\n{page_text(str(START_PAGE))}",
            reply_markup=make_markup(str(START_PAGE), TIMEOUT_SEC),
            parse_mode="Markdown")
        global active_msg
        active_msg = (nid, msg.message_id, str(START_PAGE))
        start_tick(context)
    except Exception as e:
        log.error(f"Promote hata: {e}")
        release("hata")
        await promote(context)


# ─── HANDLERS ───
async def show_page(update, page_key, edit=False):
    page = MENU.get(page_key)
    if not page:
        t = "⚠ Sayfa bulunamadı."
        if edit: await update.callback_query.edit_message_text(t)
        else: await update.message.reply_text(t)
        return

    send_dwin(page.get("dwin_page", int(page_key)))
    touch()
    log_page(page_key)

    text = page_text(page_key)
    markup = make_markup(page_key, remaining())

    global active_msg
    if edit:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
        active_msg = (update.effective_user.id, update.callback_query.message.message_id, page_key)
    else:
        msg = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        active_msg = (update.effective_user.id, msg.message_id, page_key)


async def cmd_start(update, context):
    cid = update.effective_user.id
    name = update.effective_user.first_name or str(cid)

    if is_active(cid):
        touch()
        await show_page(update, str(START_PAGE), edit=False)
        start_tick(context)
        return

    if active_user is None:
        activate(cid, name)
        await update.message.reply_text(
            f"🟢 *Hoş geldiniz {name}!*\n"
            f"Enerjimetre'yi kontrol edebilirsiniz.\n"
            f"Her tuşa basışta _{TIMEOUT_SEC} sn_ süre sıfırlanır.",
            parse_mode="Markdown")
        await show_page(update, str(START_PAGE), edit=False)
        start_tick(context)
        return

    pos = queue_pos(cid)
    if pos == 0:
        queue.append((cid, name))
        pos = len(queue)

    await update.message.reply_text(
        f"⏳ *Cihaz şu an kullanılıyor.*\n"
        f"Sıranız: *{pos}*\n\n"
        f"Beklerken canlı yayından izleyebilirsiniz:\n"
        f"🔴 [{LIVE_URL}]({LIVE_URL})",
        parse_mode="Markdown", disable_web_page_preview=True)


async def on_button(update, context):
    q = update.callback_query
    await q.answer()
    cid = update.effective_user.id

    if q.data == "_timer":
        await q.answer(f"⏱ {remaining()} sn kaldı", show_alert=False)
        return

    if q.data == "_release":
        if is_active(cid):
            release("manuel bırakma")
            await q.edit_message_text("✅ Cihazı bıraktınız. Tekrar: /start")
            await promote(context)
        return

    if not is_active(cid):
        pos = queue_pos(cid)
        await q.answer(f"⏳ Sıranız: {pos}" if pos else "Önce /start gönderin.", show_alert=True)
        return

    log.info(f"Click: {update.effective_user.first_name} → {q.data}")
    await show_page(update, q.data, edit=True)
    start_tick(context)


async def cmd_stop(update, context):
    cid = update.effective_user.id
    if is_active(cid):
        release("kullanıcı /stop")
        await update.message.reply_text("✅ Cihazı bıraktınız.")
        await promote(context)
    else:
        pos = queue_pos(cid)
        if pos:
            queue.remove((cid, update.effective_user.first_name or str(cid)))
            await update.message.reply_text("✅ Sıradan çıktınız.")
        else:
            await update.message.reply_text("Zaten aktif değilsiniz.")


async def shutdown_notify(app):
    """Ctrl+C yapıldığında herkese haber ver, ID'leri kaydet."""
    all_users = []

    # Aktif kullanıcıyı bilgilendir + rapor yaz
    if active_user:
        cid = active_user[0]
        all_users.append(cid)
        try:
            await app.bot.send_message(cid,
                "🔧 *Bot güncelleniyor.* Tekrar açıldığında size haber vereceğim.\nLütfen bekleyin.",
                parse_mode="Markdown")
        except: pass
        release("bot kapatıldı")
        # Push'u senkron yap — bot kapanmadan tamamlansın
        git_push_sync()

    # Sıradakileri bilgilendir
    for cid, name in list(queue):
        all_users.append(cid)
        try:
            await app.bot.send_message(cid,
                "🔧 *Bot güncelleniyor.* Tekrar açıldığında size haber vereceğim.\nSıranız korunacak.",
                parse_mode="Markdown")
        except: pass
    queue.clear()

    # ID'leri dosyaya kaydet (restart'ta bildirim için)
    if all_users:
        try:
            with open(PENDING_FILE, "w") as f:
                json.dump(all_users, f)
            log.info(f"Pending users kaydedildi: {all_users}")
        except: pass

    send_dwin(DEFAULT_PAGE)
    log.info("Bot kapatılıyor...")


async def startup_notify(app):
    """Bot açıldığında önceki oturumdan bekleyenlere haber ver."""
    if not os.path.exists(PENDING_FILE):
        return
    try:
        with open(PENDING_FILE, "r") as f:
            user_ids = json.load(f)
        os.remove(PENDING_FILE)
        for cid in user_ids:
            try:
                await app.bot.send_message(cid,
                    "🟢 *Bot tekrar aktif!* Cihazı kullanmak için /start gönderin.",
                    parse_mode="Markdown")
            except: pass
        log.info(f"Startup bildirimi gönderildi: {len(user_ids)} kullanıcı")
    except: pass


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(on_button))

    # Startup: bekleyenlere bildirim
    app.post_init = startup_notify

    # Shutdown: herkese haber ver
    app.post_stop = shutdown_notify

    log.info("Bot başlatıldı (sıra + geri sayım + raporlama).")
    app.run_polling()


if __name__ == "__main__":
    main()
