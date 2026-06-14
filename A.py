import json
import os
import logging
from typing import Optional

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
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
)
from telethon.tl.types import DocumentAttributeFilename

# ══════════════════════════════════════════════════════════════════════
# الإعدادات
# ══════════════════════════════════════════════════════════════════════
BOT_TOKEN = "8495856594:AAGIPL1HzGg80haAn5cnYzWbt6CpWqIfPwY"
OWNER_ID  = 6668195885
API_ID    = 32801472
API_HASH  = "80947f2a32a377b50e2e55a83ae0cd9e"

DATA_FILE        = "data.json"
RESULTS_PER_PAGE = 10

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# حالات المحادثة
(S_START_MSG, S_CHANNEL, S_RELAY,
 S_PHONE, S_CODE, S_TWO_FA) = range(6)

# ══════════════════════════════════════════════════════════════════════
# البيانات
# ══════════════════════════════════════════════════════════════════════
def _default() -> dict:
    return {
        "start_msg":      "📚 مرحباً! ابحث عن أي كتاب وسأجده لك.",
        "channels":       [],
        "relay_id":       None,
        "session_string": None,
    }

def load() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        # تأكد من وجود كل المفاتيح
        for k, v in _default().items():
            d.setdefault(k, v)
        return d
    return _default()

def save(d: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

# ══════════════════════════════════════════════════════════════════════
# Telethon (userbot)
# ══════════════════════════════════════════════════════════════════════
tele: Optional[TelegramClient] = None

async def _connect_tele(session_str: str) -> TelegramClient:
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()
    # تحميل كاش الـ peers
    try:
        n = 0
        async for _ in client.iter_dialogs():
            n += 1
        log.info("Telethon: حُمّل %d dialog", n)
    except Exception as e:
        log.warning("Telethon dialogs: %s", e)
    return client

async def on_start(app: Application) -> None:
    global tele
    d = load()
    if d["session_string"]:
        try:
            tele = await _connect_tele(d["session_string"])
            log.info("Telethon متصل ✅")
        except Exception as e:
            log.warning("Telethon فشل عند البدء: %s", e)
    else:
        log.info("لا توجد جلسة Telethon — استخدم لوحة التحكم لتسجيل الدخول")

async def on_stop(app: Application) -> None:
    if tele and tele.is_connected():
        await tele.disconnect()

def connected() -> bool:
    return tele is not None and tele.is_connected()

# ══════════════════════════════════════════════════════════════════════
# البحث والريلاي
# ══════════════════════════════════════════════════════════════════════
async def do_search(query: str, channels: list) -> list:
    if not connected():
        return []
    results = []
    for ch in channels:
        try:
            ent = await tele.get_entity(ch)
            async for msg in tele.iter_messages(ent, search=query, limit=300):
                if not (msg.document or msg.audio or msg.video):
                    continue
                name = ""
                if msg.document:
                    for a in msg.document.attributes:
                        if isinstance(a, DocumentAttributeFilename):
                            name = a.file_name
                            break
                if not name and msg.message:
                    name = msg.message.split("\n")[0].strip()
                name = name or "ملف"
                results.append({"name": name[:80], "ch": ch, "id": msg.id})
        except Exception as e:
            log.warning("بحث (%s): %s", ch, e)
    return results

async def send_via_relay(uid: int, ch: str, mid: int, bot) -> tuple[bool, str]:
    d = load()
    relay = d["relay_id"]
    if not relay:
        return False, "الريلاي غير مضبوط"
    if not connected():
        return False, "Telethon غير متصل"
    fwd_id = None
    try:
        src = await tele.get_entity(ch)
        dst = await tele.get_entity(relay)
        fwds = await tele.forward_messages(dst, [mid], from_peer=src)
        fwd_id = fwds[0].id
        await bot.copy_message(chat_id=uid, from_chat_id=relay,
                               message_id=fwd_id, caption="")
        return True, ""
    except Exception as e:
        log.error("relay: %s", e)
        return False, str(e)
    finally:
        if fwd_id:
            try:
                await tele.delete_messages(relay, [fwd_id])
            except Exception:
                pass

# ══════════════════════════════════════════════════════════════════════
# لوحات المفاتيح
# ══════════════════════════════════════════════════════════════════════
def kb_main(is_owner: bool) -> Optional[InlineKeyboardMarkup]:
    if not is_owner:
        return None
    d = load()
    relay_lbl = f"✅ الريلاي ({d['relay_id']})" if d["relay_id"] else "⚙️ ضبط الريلاي"
    login_lbl = "🔑 الحساب (متصل ✅)" if d["session_string"] else "🔑 تسجيل الدخول برقم الهاتف"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تعديل رسالة الترحيب", callback_data="edit_start")],
        [InlineKeyboardButton("📚 إدارة القنوات",        callback_data="manage_ch")],
        [InlineKeyboardButton(relay_lbl,                 callback_data="set_relay")],
        [InlineKeyboardButton(login_lbl,                 callback_data="login")],
    ])

def kb_channels() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة قناة",   callback_data="add_ch")],
        [InlineKeyboardButton("🗑️ حذف قناة",     callback_data="del_ch")],
        [InlineKeyboardButton("📋 عرض القنوات",  callback_data="list_ch")],
        [InlineKeyboardButton("🔙 رجوع",         callback_data="back")],
    ])

def kb_del_channels(channels: list) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"🗑 {c}", callback_data=f"dx:{c}")] for c in channels]
    rows.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_ch")])
    return InlineKeyboardMarkup(rows)

def kb_results(results: list, page: int) -> InlineKeyboardMarkup:
    s = page * RESULTS_PER_PAGE
    e = s + RESULTS_PER_PAGE
    rows = []
    for i, r in enumerate(results[s:e], start=s):
        rows.append([InlineKeyboardButton(f"{i+1}. {r['name']}"[:64],
                                          callback_data=f"get:{i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"pg:{page-1}"))
    if e < len(results):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"pg:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════════════════
# /start
# ══════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d  = load()
    ow = update.effective_user.id == OWNER_ID
    await update.message.reply_text(d["start_msg"], reply_markup=kb_main(ow))

# ══════════════════════════════════════════════════════════════════════
# تعديل رسالة الترحيب
# ══════════════════════════════════════════════════════════════════════
async def cb_edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    d = load()
    await update.callback_query.message.reply_text(
        f"الرسالة الحالية:\n\n{d['start_msg']}\n\n"
        "أرسل الرسالة الجديدة أو /cancel:"
    )
    return S_START_MSG

async def rx_start_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    d = load()
    d["start_msg"] = update.message.text
    save(d)
    await update.message.reply_text("✅ تم تحديث رسالة الترحيب!")
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════════════════
# إدارة القنوات
# ══════════════════════════════════════════════════════════════════════
async def cb_manage_ch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.edit_text("📚 إدارة القنوات:", reply_markup=kb_channels())

async def cb_add_ch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "أرسل يوزرنيم القناة أو رابطها:\n"
        "مثال: @mychannel أو https://t.me/mychannel\n\n/cancel للإلغاء"
    )
    return S_CHANNEL

async def rx_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()
    if t.startswith("https://t.me/"):
        t = "@" + t[len("https://t.me/"):].split("/")[0]
    elif t.startswith("t.me/"):
        t = "@" + t[5:].split("/")[0]
    elif not t.startswith("@"):
        t = "@" + t
    d = load()
    if t in d["channels"]:
        await update.message.reply_text("⚠️ القناة موجودة مسبقاً.")
    else:
        d["channels"].append(t)
        save(d)
        await update.message.reply_text(f"✅ تمت إضافة {t}")
    return ConversationHandler.END

async def cb_del_ch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = load()
    if not d["channels"]:
        await q.message.edit_text("لا توجد قنوات.", reply_markup=kb_channels())
        return
    await q.message.edit_text("اختر القناة للحذف:", reply_markup=kb_del_channels(d["channels"]))

async def cb_dx(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q  = update.callback_query
    await q.answer()
    ch = q.data.split(":", 1)[1]
    d  = load()
    if ch in d["channels"]:
        d["channels"].remove(ch)
        save(d)
        await q.message.edit_text(f"✅ تم حذف {ch}", reply_markup=kb_channels())
    else:
        await q.message.edit_text("القناة غير موجودة.", reply_markup=kb_channels())

async def cb_list_ch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = load()
    if not d["channels"]:
        txt = "لا توجد قنوات."
    else:
        txt = "📋 القنوات:\n\n" + "\n".join(f"{i+1}. {c}" for i, c in enumerate(d["channels"]))
    await q.message.edit_text(txt, reply_markup=kb_channels())

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q  = update.callback_query
    await q.answer()
    d  = load()
    ow = q.from_user.id == OWNER_ID
    await q.message.edit_text(d["start_msg"], reply_markup=kb_main(ow))

# ══════════════════════════════════════════════════════════════════════
# ضبط الريلاي
# ══════════════════════════════════════════════════════════════════════
async def cb_set_relay(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    d = load()
    cur = f"الريلاي الحالي: `{d['relay_id']}`\n\n" if d["relay_id"] else ""
    await update.callback_query.message.reply_text(
        f"{cur}"
        "أرسل ID مجموعة الريلاي (رقم يبدأ بـ -):\n"
        "مثال: `-1001234567890`\n\n/cancel للإلغاء",
        parse_mode="Markdown",
    )
    return S_RELAY

async def rx_relay(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    t = update.message.text.strip()
    try:
        rid = int(t)
    except ValueError:
        await update.message.reply_text("❌ يجب أن يكون رقماً. حاول مجدداً أو /cancel")
        return S_RELAY
    try:
        chat = await ctx.bot.get_chat(rid)
    except Exception as e:
        await update.message.reply_text(f"❌ البوت لا يصل للمجموعة:\n`{e}`\n\nحاول مجدداً أو /cancel",
                                        parse_mode="Markdown")
        return S_RELAY
    d = load()
    d["relay_id"] = rid
    save(d)
    await update.message.reply_text(
        f"✅ تم ضبط الريلاي!\nالمجموعة: *{chat.title}*",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════════════════
# تسجيل الدخول برقم الهاتف
# ══════════════════════════════════════════════════════════════════════
async def cb_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    d  = load()
    st = "ℹ️ يوجد حساب متصل، الدخول الجديد سيستبدله.\n\n" if d["session_string"] else ""
    await update.callback_query.message.reply_text(
        f"{st}📱 أرسل رقم هاتفك مع رمز الدولة:\n"
        "مثال: `+9665XXXXXXXX`\n\n/cancel للإلغاء",
        parse_mode="Markdown",
    )
    return S_PHONE

async def rx_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.text.strip()
    wait  = await update.message.reply_text("⏳ جاري إرسال الكود...")
    tmp   = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await tmp.connect()
        res = await tmp.send_code_request(phone)
    except FloodWaitError as ex:
        await tmp.disconnect()
        await wait.edit_text(f"❌ انتظر {ex.seconds} ثانية ثم حاول.")
        return S_PHONE
    except Exception as ex:
        await tmp.disconnect()
        await wait.edit_text(f"❌ خطأ: {ex}\n\nتحقق من الرقم وأعد المحاولة أو /cancel")
        return S_PHONE
    ctx.user_data["tmp"]   = tmp
    ctx.user_data["phone"] = phone
    ctx.user_data["hash"]  = res.phone_code_hash
    await wait.edit_text(
        "✅ تم إرسال الكود.\n\n"
        "أرسل الكود (5 أرقام):\n/cancel للإلغاء"
    )
    return S_CODE

async def rx_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    code  = update.message.text.strip().replace(" ", "")
    tmp   = ctx.user_data.get("tmp")
    phone = ctx.user_data.get("phone")
    hash_ = ctx.user_data.get("hash")
    if not tmp:
        await update.message.reply_text("❌ انتهت الجلسة. ابدأ من جديد.")
        return ConversationHandler.END
    try:
        await tmp.sign_in(phone, code, phone_code_hash=hash_)
        return await _finish_login(update, ctx, tmp)
    except SessionPasswordNeededError:
        await update.message.reply_text("🔐 أرسل كلمة مرور التحقق بخطوتين:\n/cancel للإلغاء")
        return S_TWO_FA
    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ الكود غير صحيح. أرسله مجدداً:\n/cancel للإلغاء")
        return S_CODE
    except PhoneCodeExpiredError:
        await _disc(tmp)
        ctx.user_data.clear()
        await update.message.reply_text("❌ انتهت صلاحية الكود. ابدأ من جديد.")
        return ConversationHandler.END
    except Exception as ex:
        await update.message.reply_text(f"❌ خطأ: {ex}\n\nحاول مجدداً أو /cancel")
        return S_CODE

async def rx_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    pw  = update.message.text.strip()
    tmp = ctx.user_data.get("tmp")
    if not tmp:
        await update.message.reply_text("❌ انتهت الجلسة. ابدأ من جديد.")
        return ConversationHandler.END
    try:
        await tmp.sign_in(password=pw)
        return await _finish_login(update, ctx, tmp)
    except PasswordHashInvalidError:
        await update.message.reply_text("❌ كلمة المرور خاطئة. حاول مجدداً:\n/cancel للإلغاء")
        return S_TWO_FA
    except Exception as ex:
        await update.message.reply_text(f"❌ خطأ: {ex}\n\nحاول مجدداً أو /cancel")
        return S_TWO_FA

async def _finish_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         tmp: TelegramClient) -> int:
    global tele
    try:
        sess = tmp.session.save()
        await tmp.disconnect()
    except Exception as ex:
        await update.message.reply_text(f"❌ تعذّر حفظ الجلسة: {ex}")
        return ConversationHandler.END
    ctx.user_data.clear()
    d = load()
    d["session_string"] = sess
    save(d)
    if tele and tele.is_connected():
        try:
            await tele.disconnect()
        except Exception:
            pass
    try:
        tele = await _connect_tele(sess)
        await update.message.reply_text("✅ تم تسجيل الدخول بنجاح!\nالبوت جاهز للبحث.")
    except Exception as ex:
        await update.message.reply_text(
            f"⚠️ حُفظت الجلسة لكن Telethon لم يتصل: {ex}\n"
            "سيتصل تلقائياً عند إعادة التشغيل."
        )
    return ConversationHandler.END

async def _disc(client: Optional[TelegramClient]) -> None:
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════════════
# البحث
# ══════════════════════════════════════════════════════════════════════
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.message.text.strip()
    if not q:
        return
    d = load()
    if not d["channels"]:
        await update.message.reply_text("⚠️ لا توجد قنوات مضافة بعد.")
        return
    if not connected():
        await update.message.reply_text("❌ تعذّر البحث عن الكتب الآن. حاول لاحقاً.")
        return
    m = await update.message.reply_text(f"🔍 جاري البحث عن: {q} ...")
    res = await do_search(q, d["channels"])
    ctx.user_data["res"] = res
    ctx.user_data["q"]   = q
    if not res:
        await m.edit_text(f"❌ لم أجد نتائج لـ: {q}")
        return
    await m.edit_text(
        f"📚 نتائج البحث عن: *{q}*\nالعدد: {len(res)}\n\nاضغط لاستلام الكتاب:",
        reply_markup=kb_results(res, 0),
        parse_mode="Markdown",
    )

async def cb_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[1])
    res  = ctx.user_data.get("res")
    qry  = ctx.user_data.get("q", "")
    if not res:
        await q.message.edit_text("⚠️ انتهت الجلسة. ابحث مجدداً.")
        return
    await q.message.edit_text(
        f"📚 نتائج البحث عن: *{qry}*\nالعدد: {len(res)}\n\nاضغط لاستلام الكتاب:",
        reply_markup=kb_results(res, page),
        parse_mode="Markdown",
    )

async def cb_get(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer("⏳ جاري الإرسال...")
    idx = int(q.data.split(":")[1])
    res = ctx.user_data.get("res", [])
    if not res or idx >= len(res):
        await q.message.reply_text("⚠️ انتهت الجلسة. ابحث مجدداً.")
        return
    r = res[idx]
    ok, err = await send_via_relay(q.from_user.id, r["ch"], r["id"], ctx.bot)
    if not ok:
        d = load()
        if not d["relay_id"]:
            await q.message.reply_text("⚙️ الريلاي غير مضبوط.")
        else:
            await q.message.reply_text(f"❌ تعذّر الإرسال:\n{err}")

# ══════════════════════════════════════════════════════════════════════
# إلغاء
# ══════════════════════════════════════════════════════════════════════
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    tmp = ctx.user_data.pop("tmp", None)
    await _disc(tmp)
    ctx.user_data.clear()
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════════════════
# التشغيل
# ══════════════════════════════════════════════════════════════════════
def main() -> None:
    ow = filters.User(OWNER_ID)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_start)
        .post_shutdown(on_stop)
        .build()
    )

    cancel_cmd = CommandHandler("cancel", cmd_cancel)

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_edit_start, pattern="^edit_start$")],
        states={S_START_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND & ow, rx_start_msg)]},
        fallbacks=[cancel_cmd, CallbackQueryHandler(cb_back, pattern="^back$")],
        per_message=False, allow_reentry=True,
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_add_ch, pattern="^add_ch$")],
        states={S_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND & ow, rx_channel)]},
        fallbacks=[cancel_cmd, CallbackQueryHandler(cb_manage_ch, pattern="^manage_ch$")],
        per_message=False, allow_reentry=True,
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_set_relay, pattern="^set_relay$")],
        states={S_RELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ow, rx_relay)]},
        fallbacks=[cancel_cmd, CallbackQueryHandler(cb_back, pattern="^back$")],
        per_message=False, allow_reentry=True,
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_login, pattern="^login$")],
        states={
            S_PHONE:  [MessageHandler(filters.TEXT & ~filters.COMMAND & ow, rx_phone)],
            S_CODE:   [MessageHandler(filters.TEXT & ~filters.COMMAND & ow, rx_code)],
            S_TWO_FA: [MessageHandler(filters.TEXT & ~filters.COMMAND & ow, rx_2fa)],
        },
        fallbacks=[cancel_cmd, CallbackQueryHandler(cb_back, pattern="^back$")],
        per_message=False, allow_reentry=True,
    ))

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_manage_ch, pattern="^manage_ch$"))
    app.add_handler(CallbackQueryHandler(cb_del_ch,    pattern="^del_ch$"))
    app.add_handler(CallbackQueryHandler(cb_dx,        pattern="^dx:"))
    app.add_handler(CallbackQueryHandler(cb_list_ch,   pattern="^list_ch$"))
    app.add_handler(CallbackQueryHandler(cb_back,      pattern="^back$"))
    app.add_handler(CallbackQueryHandler(cb_page,      pattern="^pg:"))
    app.add_handler(CallbackQueryHandler(cb_get,       pattern="^get:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("🚀 البوت يعمل...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
