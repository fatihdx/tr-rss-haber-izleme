#!/usr/bin/env python3
"""
Feedback sistemi — Telegram inline keyboard callback handling.

Ana butonlar (tek satır):
  ➡️ WhatsApp'a Gönder  (url — doğrudan WA açar)
  💚 Beğendim           (lk: → rating=10)
  💔 Beğenmedim         (nl: → neden menüsü)

Beğenmedim nedenleri:
  🌍 Yurtdışı | ❌ Alakasız | 📢 Reklam | 📋 Site Tebligatı
  🚫 SPAM | ✏️ Diğer | 🔙 Menüye Dön (kayıt yok)

Callback data formatı (64 byte limit):
  lk:{hash8}        → Beğendim (rating=10)
  nl:{hash8}        → Beğenmedim — neden menüsünü göster
  nr:{hash8}:{tag}  → Neden seçildi (rating=-1, tag kaydedilir)
  bk:{hash8}        → Menüye dön (kayıt yok)
"""

import time
import json
import threading
import requests
from utils import (
    save_feedback, toggle_feedback, save_message_map,
    get_meta, save_meta, smart_truncate
)


# ── Conversation state: serbest yorum bekleyen kullanıcılar ──
# { user_id: {"hash": "a1b2c3d4", "rating": 3, "ts": timestamp} }
_pending_comments = {}
_pending_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════
# BUTON OLUŞTURMA
# ══════════════════════════════════════════════════════════════

def make_main_buttons(hash8, wa_url):
    """Ana butonlar: WA (url), Beğendim, Beğenmedim — tek satır."""
    return {
        "inline_keyboard": [
            [
                {"text": "➡️ WhatsApp'a Gönder", "url": wa_url},
                {"text": "💚 Beğendim", "callback_data": f"lk:{hash8}"},
                {"text": "💔 Beğenmedim", "callback_data": f"nl:{hash8}"},
            ]
        ]
    }


def _make_dislike_buttons(hash8):
    """Beğenmedim neden menüsü."""
    return {
        "inline_keyboard": [
            [
                {"text": "🌍 Yurtdışı",       "callback_data": f"nr:{hash8}:geo"},
                {"text": "❌ Alakasız",        "callback_data": f"nr:{hash8}:irr"},
            ],
            [
                {"text": "📢 Reklam",          "callback_data": f"nr:{hash8}:ad"},
                {"text": "📋 Site Tebligatı",  "callback_data": f"nr:{hash8}:blt"},
            ],
            [
                {"text": "🚫 SPAM",            "callback_data": f"nr:{hash8}:spam"},
                {"text": "✏️ Diğer",           "callback_data": f"nr:{hash8}:other"},
            ],
            [
                {"text": "🔙 Menüye Dön",      "callback_data": f"bk:{hash8}"},
            ],
        ]
    }


def _make_done_buttons(hash8, wa_url, confirmation_text):
    """İşlem tamamlandı sonrası: onay + WhatsApp."""
    return {
        "inline_keyboard": [
            [{"text": confirmation_text, "callback_data": "noop"}],
            [{"text": "💚 WhatsApp'a Gönder", "url": wa_url}],
        ]
    }


# ══════════════════════════════════════════════════════════════
# TELEGRAM API YARDIMCILARI
# ══════════════════════════════════════════════════════════════

def _answer_callback(session, token, callback_query_id, text="", show_alert=False, url=None):
    """Callback query'yi yanıtla (spinner'ı kapat). url verilirse Telegram o URL'yi açar."""
    api_url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert,
    }
    if url:
        payload["url"] = url
    try:
        session.post(api_url, json=payload, timeout=10)
    except Exception:
        pass


def _edit_reply_markup(session, token, chat_id, message_id, reply_markup):
    """Mesajın butonlarını güncelle."""
    url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
    try:
        session.post(url, json={
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
        }, timeout=10)
    except Exception:
        pass


def _send_force_reply(session, token, chat_id, text, reply_to_message_id=None):
    """ForceReply ile kullanıcıdan metin iste."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Yorumunuzu yazın...",
        },
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    try:
        r = session.post(url, json=payload, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _delete_message(session, token, chat_id, message_id):
    """Mesajı sil (ForceReply temizliği için)."""
    url = f"https://api.telegram.org/bot{token}/deleteMessage"
    try:
        session.post(url, json={
            "chat_id": chat_id,
            "message_id": message_id,
        }, timeout=10)
    except Exception:
        pass


def _set_reaction(session, token, chat_id, message_id, emoji="👍"):
    """Mesaja reaction koy."""
    url = f"https://api.telegram.org/bot{token}/setMessageReaction"
    try:
        session.post(url, json={
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": [{"type": "emoji", "emoji": emoji}],
        }, timeout=10)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════

def _get_msg_info(db_con, hash8):
    """hash8 ile message_map'ten bilgi çek."""
    row = db_con.execute(
        "SELECT hash, chat_id, message_id, title, category, wa_url "
        "FROM message_map WHERE hash LIKE ?", (hash8 + "%",)
    ).fetchone()
    if row:
        return {
            "hash": row[0], "chat_id": row[1], "message_id": row[2],
            "title": row[3], "category": row[4], "wa_url": row[5],
        }
    return None


def _get_keywords_for_hash(db_con, full_hash):
    """Bir haberin eşleşen keyword'lerini getir."""
    rows = db_con.execute(
        "SELECT keyword FROM article_keywords WHERE hash=?", (full_hash,)
    ).fetchall()
    return [r[0] for r in rows]


def handle_callback(session, token, db_con, callback_query, logger):
    """Tek bir callback_query'yi işle."""
    cq_id = callback_query["id"]
    data = callback_query.get("data", "")
    user = callback_query.get("from", {})
    user_id = user.get("id", 0)

    # noop — hiçbir şey yapma
    if data == "noop":
        _answer_callback(session, token, cq_id)
        return

    # Callback data parse
    parts = data.split(":")
    if len(parts) < 2:
        _answer_callback(session, token, cq_id, "Geçersiz işlem")
        return

    action = parts[0]
    hash8 = parts[1]

    msg_info = _get_msg_info(db_con, hash8)
    if not msg_info:
        _answer_callback(session, token, cq_id, "Haber bulunamadı")
        return

    full_hash = msg_info["hash"]
    chat_id = msg_info["chat_id"]
    message_id = msg_info["message_id"]
    wa_url = msg_info["wa_url"] or ""
    title = msg_info["title"] or ""

    # ── lk: Beğendim → rating=10 (toggle: aynı oy → geri al) ──
    if action == "lk":
        result = toggle_feedback(db_con, full_hash, user_id, 10, "score", tag="liked")
        db_con.commit()

        keywords = _get_keywords_for_hash(db_con, full_hash)
        logger.info(f"[FB] 💚10:{result} hash={hash8} kw={keywords} \"{title[:50]}\"")

        if result == "deleted":
            _edit_reply_markup(session, token, chat_id, message_id,
                               make_main_buttons(hash8, wa_url))
            _answer_callback(session, token, cq_id, "Beğeni geri alındı.")
        else:
            _edit_reply_markup(session, token, chat_id, message_id,
                               _make_done_buttons(hash8, wa_url, "💚 Beğenildi"))
            _answer_callback(session, token, cq_id, "💚 Beğenildi, teşekkürler!")

    # ── nl: Beğenmedim → neden menüsü ──
    elif action == "nl":
        _edit_reply_markup(session, token, chat_id, message_id,
                           _make_dislike_buttons(hash8))
        _answer_callback(session, token, cq_id, "Neden beğenmediniz?")

    # ── nr: Neden seçildi → rating=-1, tag kaydet ──
    elif action == "nr" and len(parts) >= 3:
        tag = parts[2]
        tag_labels = {
            "geo":   "🌍 Yurtdışı",
            "irr":   "❌ Alakasız",
            "ad":    "📢 Reklam",
            "blt":   "📋 Site Tebligatı",
            "spam":  "🚫 SPAM",
            "other": "✏️ Diğer",
        }
        tag_label = tag_labels.get(tag, tag)

        toggle_feedback(db_con, full_hash, user_id, -1, "score", tag=tag)
        db_con.commit()

        keywords = _get_keywords_for_hash(db_con, full_hash)
        logger.info(f"[FB] 💔-1:{tag} hash={hash8} kw={keywords} \"{title[:50]}\"")

        _edit_reply_markup(session, token, chat_id, message_id,
                           _make_done_buttons(hash8, wa_url, f"💔 {tag_label}"))
        _answer_callback(session, token, cq_id, f"✓ Kaydedildi: {tag_label}")

    # ── bk: Menüye dön — kayıt yok ──
    elif action == "bk":
        _edit_reply_markup(session, token, chat_id, message_id,
                           make_main_buttons(hash8, wa_url))
        _answer_callback(session, token, cq_id)

    else:
        _answer_callback(session, token, cq_id, "Bilinmeyen işlem")


# ══════════════════════════════════════════════════════════════
# SERBEST YORUM HANDLER (ForceReply yanıtları)
# ══════════════════════════════════════════════════════════════

def handle_text_reply(session, token, db_con, message, logger):
    """Kullanıcının ForceReply'a yazdığı serbest yorumu işle."""
    user = message.get("from", {})
    user_id = user.get("id", 0)
    text = message.get("text", "").strip()
    chat_id = message["chat"]["id"]
    msg_id = message["message_id"]

    with _pending_lock:
        pending = _pending_comments.pop(user_id, None)

    if not pending:
        return False  # Bu kullanıcıdan yorum beklemiyorduk

    full_hash = pending["hash"]
    hash8 = pending["hash8"]
    rating = pending["rating"]
    original_chat_id = pending["chat_id"]
    original_message_id = pending["message_id"]
    wa_url = pending["wa_url"]
    title = pending["title"]

    # /gec komutu — yorumsuz kaydet
    if text.lower() in ("/gec", "/geç", "geç", "gec"):
        save_feedback(db_con, full_hash, user_id, rating, "score", tag=None, comment=None)
        logger.info(f"[FB] 📝{rating}/10 hash={hash8} \"{title[:50]}\"")
    else:
        save_feedback(db_con, full_hash, user_id, rating, "score", tag="other", comment=text)
        logger.info(f"[FB] 📝{rating}/10:other hash={hash8} yorum=\"{text[:100]}\" \"{title[:50]}\"")

    db_con.commit()

    # Onay reaction'ı koy ve orijinal mesajı güncelle
    _set_reaction(session, token, chat_id, msg_id, "👍")
    _edit_reply_markup(session, token, original_chat_id, original_message_id,
                       _make_done_buttons(hash8, wa_url,
                                          f"✓ {rating}/10 — ✏️ Yorum"))
    return True


# ══════════════════════════════════════════════════════════════
# POLLING THREAD
# ══════════════════════════════════════════════════════════════

def _cleanup_expired_pendings(timeout=600):
    """10 dakikadan eski bekleyen yorumları temizle."""
    now = time.time()
    with _pending_lock:
        expired = [uid for uid, p in _pending_comments.items()
                   if now - p["ts"] > timeout]
        for uid in expired:
            _pending_comments.pop(uid)


def poll_feedback(session, token, db_con, logger, running_flag):
    """
    Feedback polling döngüsü — daemon thread olarak çalışır.
    callback_query ve message (ForceReply yanıtları) dinler.
    """
    offset = int(get_meta(db_con, "feedback_offset", "0") or "0")
    cleanup_counter = 0
    _net_fail_count = 0  # ağ kopukluğu sayacı (exponential backoff için)

    logger.info("[FEEDBACK] Polling thread started")

    while running_flag():
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {
                "offset": offset,
                "timeout": 10,
                "allowed_updates": json.dumps(["callback_query", "message"]),
            }
            r = session.get(url, params=params, timeout=15)
            _net_fail_count = 0  # başarılı bağlantı — sayacı sıfırla

            if r.status_code != 200:
                logger.warning(f"[FEEDBACK] getUpdates error: {r.status_code}")
                time.sleep(5)
                continue

            updates = r.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1

                # Callback query (buton tıklaması)
                cq = update.get("callback_query")
                if cq:
                    try:
                        handle_callback(session, token, db_con, cq, logger)
                    except Exception:
                        logger.exception("[FEEDBACK] Callback handler error")

                # Message (ForceReply yanıtı)
                msg = update.get("message")
                if msg and msg.get("text") and msg.get("reply_to_message"):
                    try:
                        handle_text_reply(session, token, db_con, msg, logger)
                    except Exception:
                        logger.exception("[FEEDBACK] Reply handler error")

            # Offset kaydet
            save_meta(db_con, "feedback_offset", str(offset))
            db_con.commit()

            # Periyodik temizlik (her ~50 polling'de bir)
            cleanup_counter += 1
            if cleanup_counter % 50 == 0:
                _cleanup_expired_pendings()

        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            # Ağ kopukluğu — exponential backoff (5s → 10s → 20s → ... → 300s)
            _net_fail_count += 1
            wait = min(5 * (2 ** (_net_fail_count - 1)), 300)
            logger.warning(f"[FEEDBACK] Ağ hatası (#{_net_fail_count}), {wait}s bekleniyor")
            time.sleep(wait)

        except Exception:
            logger.exception("[FEEDBACK] Poll loop error")
            time.sleep(5)


def start_feedback_thread(cfg, db_con, session, logger, running_flag):
    """Feedback polling thread'ini başlat."""
    if not cfg.get("feedback_enabled", True):
        logger.info("[FEEDBACK] Disabled in config")
        return None

    token = cfg["telegram_token"]
    thread = threading.Thread(
        target=poll_feedback,
        args=(session, token, db_con, logger, running_flag),
        daemon=True,
        name="feedback-poll",
    )
    thread.start()
    logger.info("[FEEDBACK] Thread started")
    return thread


# ══════════════════════════════════════════════════════════════
# WhatsApp CALLBACK LOGLAMA
# ══════════════════════════════════════════════════════════════

def log_whatsapp_share(db_con, full_hash, user_id, username, title, keywords, logger):
    """WhatsApp butonuna tıklanınca (url butonu callback üretmez,
    bu fonksiyon gerekirse send anında otomatik puan=10 vermek için kullanılabilir)."""
    # NOT: Telegram url butonları callback_query üretmez.
    # WhatsApp = mükemmel sinyali ancak send anında
    # otomatik olarak loglanamaz. Bu fonksiyon gelecekteki
    # bir web-app entegrasyonu için hazır tutuluyor.
    pass
