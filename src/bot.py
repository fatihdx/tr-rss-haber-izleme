
#!/usr/bin/env python3
"""
Telegram RSS Bot (Termux 7/24)
- Crash-proof main loop
- SIGTERM graceful shutdown, SIGHUP config hot-reload
- requests.Session with retry
- Telegram rate limiting + send retry
- Pre-normalized keyword filtering (KeywordIndex)
- SQLite WAL mode, single connection, auto-cleanup
- Health logging (gc, memory, uptime)
- Smart sleep (active hours aware)
- Feedback sistemi: 💚 WhatsApp / 🚫 SPAM / 📝 Puan (0 ve 10) + yorum
"""
import os, sys, time, re, html, json, signal, atexit, gc
from urllib.parse import quote
import feedparser, requests, yaml
from datetime import datetime
from dateutil import tz
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from utils import (setup_logger, norm, item_hash, within_hours, seconds_until_active,
                   parse_published, ensure_db, cleanup_db, save_meta, get_meta,
                   KeywordIndex, filter_entry_advanced,
                   extract_location, extract_legal_summary,
                   is_duplicate_title, save_title_fp,
                   clean_summary, smart_truncate,
                   save_article_keywords, save_message_map,
                   save_feed_stats, write_weekly_report, log_article)
from feedback import make_main_buttons, start_feedback_thread

# ── Globals for signal handlers ──
_running = True
_reload_requested = False

# ── Kategori emoji ve görüntüleme haritası ──
_CATEGORY_EMOJI = {
    "Cinayet & Şiddet": "🔴",
    "Kadına & Çocuğa Şiddet": "🟣",
    "Terör & Güvenlik": "⚫",
    "Organize Suç & Yolsuzluk": "🟠",
    "Operasyon & Yakalama": "🔵",
    "Yargılama & Karar": "🟤",
    "Soruşturma": "🟡",
    "Hukuk & Yargı Sistemi": "🟢",
    "Emniyet & Kolluk": "⚪",
    "Gündem": "📰",
}

_CATEGORY_DISPLAY = {
    "Cinayet & Şiddet": "CİNAYET",
    "Kadına & Çocuğa Şiddet": "KADINA ŞİDDET",
    "Terör & Güvenlik": "TERÖR",
    "Organize Suç & Yolsuzluk": "ORGANİZE SUÇ",
    "Operasyon & Yakalama": "OPERASYON",
    "Yargılama & Karar": "YARGILAMA",
    "Soruşturma": "SORUŞTURMA",
    "Hukuk & Yargı Sistemi": "HUKUK",
    "Emniyet & Kolluk": "EMNİYET",
    "Gündem": "GÜNDEM",
}


def handle_sigterm(signum, frame):
    global _running
    _running = False


def handle_sighup(signum, frame):
    global _reload_requested
    _reload_requested = True


def load_cfg(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_session(retries=3):
    """requests.Session with automatic retry on 5xx errors."""
    session = requests.Session()
    retry = Retry(total=retries, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ── Message formatting ──

def format_message_new(entry, msg_format, max_len=3800, category=""):
    title = entry.get("title", "").strip()
    link = entry.get("link", "")
    summary = clean_summary(entry.get("summary", "") or "")

    emoji = _CATEGORY_EMOJI.get(category, "📰")
    display = _CATEGORY_DISPLAY.get(category, category.upper() if category else "GÜNDEM")

    parts = []

    # Kategori başlığı
    parts.append(f"<b>{emoji} {html.escape(display)}</b>")

    # Başlık (bold)
    parts.append(f"<b>{html.escape(title)}</b>")

    # Kısa Açıklama (düz metin)
    if summary:
        parts.append(html.escape(smart_truncate(summary, 500)))

    # Konum
    location = extract_location(f"{title} {summary}")
    if location:
        parts.append(f"📍 <b>Konum:</b> {html.escape(location)}")

    # Adli Süreç Özeti
    legal_summary = extract_legal_summary(title, summary)
    if legal_summary:
        parts.append(f"⚖️ <b>Adli Süreç:</b> {html.escape(legal_summary)}")

    # Link
    parts.append(f"🔗 {html.escape(link)}")

    body = "\n\n".join(parts)
    if len(body) > max_len:
        body = body[:max_len-100] + f"...\n\n🔗 {html.escape(link)}"
    return body


def format_message_old(entry, title_prefix="", footer_text="", max_len=3800):
    title = entry.get("title", "").strip()
    link = entry.get("link", "")
    summary = clean_summary(entry.get("summary", "") or "")

    parts = [f"<b>{html.escape(title_prefix + title)}</b>"]
    if summary:
        parts.append(html.escape(smart_truncate(summary, 500)))

    # Konum
    location = extract_location(f"{title} {summary}")
    if location:
        parts.append(f"📍 <b>Konum:</b> {html.escape(location)}")

    # Adli Surec Ozeti
    legal_summary = extract_legal_summary(title, summary)
    if legal_summary:
        parts.append(f"⚖️ <b>Adli Süreç:</b> {html.escape(legal_summary)}")

    parts.append(html.escape(link))
    if footer_text:
        parts.append(f"<i>{html.escape(footer_text)}</i>")

    body = "\n\n".join(parts)
    if len(body) > max_len:
        body = body[:max_len-50] + "...\n" + html.escape(link)
    return body


def format_message(entry, cfg, max_len=3800, category=""):
    msg_format = cfg.get("message_format", {})
    if msg_format.get("enabled", False):
        return format_message_new(entry, msg_format, max_len, category=category)
    else:
        title_prefix = cfg.get("title_prefix", "")
        footer_text = cfg.get("footer_text", "")
        return format_message_old(entry, title_prefix, footer_text, max_len)


def format_whatsapp_message(entry, category=""):
    """WhatsApp paylaşım mesajı — Telegram ile aynı yapıda, WhatsApp markdown (*bold*)."""
    title = entry.get("title", "").strip()
    link = entry.get("link", "")
    summary = clean_summary(entry.get("summary", "") or "")

    emoji = _CATEGORY_EMOJI.get(category, "📰")
    display = _CATEGORY_DISPLAY.get(category, category.upper() if category else "GÜNDEM")

    parts = []

    # Kategori başlığı
    parts.append(f"*{emoji} {display}*")

    # Başlık (bold)
    parts.append(f"*{title}*")

    # Kısa Açıklama (düz metin)
    if summary:
        parts.append(smart_truncate(summary, 500))

    # Konum
    location = extract_location(f"{title} {summary}")
    if location:
        parts.append(f"📍 *Konum:* {location}")

    # Adli Süreç Özeti
    legal_summary = extract_legal_summary(title, summary)
    if legal_summary:
        parts.append(f"⚖️ *Adli Süreç:* {legal_summary}")

    # Link
    parts.append(f"🔗 {link}")

    return "\n\n".join(parts)


# ── Telegram send with retry ──

def send_message(session, token, chat_id, text, disable_preview=True,
                 logger=None, max_retries=3, retry_delay=2, reply_markup=None):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": bool(disable_preview),
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    for attempt in range(1, max_retries + 1):
        try:
            r = session.post(url, json=payload, timeout=30)
            if r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", 5)
                if logger:
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                continue
            if r.status_code == 200:
                if logger:
                    logger.info(f"Sent to {chat_id}")
                result = r.json().get("result", {})
                return result.get("message_id", True)
            if logger:
                logger.error(f"Telegram error {r.status_code}: {r.text}")
        except requests.exceptions.RequestException as e:
            if logger:
                logger.warning(f"Send attempt {attempt}/{max_retries} failed: {e}")
        if attempt < max_retries:
            time.sleep(retry_delay * attempt)
    return False


# ── Health logging ──

def log_health(logger, cycle_count, start_time):
    collected = gc.collect()
    try:
        import resource
        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        mem_mb = 0
    uptime_hrs = (time.time() - start_time) / 3600
    logger.info(f"HEALTH: cycle={cycle_count}, uptime={uptime_hrs:.1f}h, "
                f"mem={mem_mb:.1f}MB, gc_collected={collected}")


# ── Main cycle ──

def cycle(cfg, kw_index, db_con, session, logger):
    token = cfg["telegram_token"]
    chat_ids = cfg.get("chat_ids", [])
    feeds = cfg.get("feeds", [])
    max_per = int(cfg.get("max_per_cycle", 10))
    disable_preview = bool(cfg.get("disable_link_preview", True))
    feed_timeout = int(cfg.get("feed_timeout", 30))
    max_retries = int(cfg.get("retry_count", 3))
    retry_delay = int(cfg.get("retry_delay", 2))

    total_checked = 0
    filtered_count = 0
    scored_entries = []
    include_matches_map = {}  # hash → [keyword, ...] feedback analizi icin
    cycle_ts = int(time.time())

    for feed in feeds:
        feed_stat = {"checked": 0, "passed": 0,
                     "r_opinion": 0, "r_foreign": 0, "r_no_include": 0,
                     "r_weak_only": 0, "r_exclude": 0, "r_duplicate": 0}
        try:
            logger.info(f"Fetching {feed}")
            resp = session.get(feed, timeout=feed_timeout)
            resp.raise_for_status()
            d = feedparser.parse(resp.text)

            for entry in d.entries[:30]:
                total_checked += 1
                feed_stat["checked"] += 1

                entry_title = entry.get("title", "")
                published_ts = parse_published(entry)
                h = item_hash(entry_title, entry.get("link", ""), published_ts)

                passes, score, category, matched_kws, reject_reason = filter_entry_advanced(entry, kw_index)
                if not passes:
                    filtered_count += 1
                    feed_stat[f"r_{reject_reason}"] = feed_stat.get(f"r_{reject_reason}", 0) + 1
                    log_article(db_con, h, feed_url=feed, title=entry_title,
                                score=score or None, category=category or None,
                                matched_kws=matched_kws, status="rejected",
                                reject_reason=reject_reason, logger=logger)
                    continue

                # Ayni kaynak tekrar kontrolu (hash)
                cur = db_con.execute("SELECT 1 FROM seen WHERE hash=?", (h,)).fetchone()
                if cur:
                    feed_stat["r_duplicate"] += 1
                    log_article(db_con, h, feed_url=feed, title=entry_title,
                                status="duplicate", reject_reason="duplicate", logger=logger)
                    continue

                # Ayni link tekrar kontrolu
                link = entry.get("link", "")
                if link:
                    cur = db_con.execute("SELECT 1 FROM seen WHERE link=?", (link,)).fetchone()
                    if cur:
                        feed_stat["r_duplicate"] += 1
                        log_article(db_con, h, feed_url=feed, title=entry_title,
                                    status="duplicate", reject_reason="duplicate_link", logger=logger)
                        continue

                # Capraz kaynak mukerrer kontrolu (baslik benzerlik)
                if is_duplicate_title(db_con, entry.get("title", "")):
                    logger.debug(f"Duplicate title: {entry_title[:50]}...")
                    feed_stat["r_duplicate"] += 1
                    log_article(db_con, h, feed_url=feed, title=entry_title,
                                status="duplicate", reject_reason="duplicate_title", logger=logger)
                    continue

                scored_entries.append((entry, score, h, published_ts, category, feed))
                include_matches_map[h] = matched_kws

        except requests.exceptions.Timeout:
            logger.warning(f"Feed timeout: {feed}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Feed fetch error: {feed} -> {e}")
        except Exception as fe:
            logger.exception(f"Feed error: {feed} -> {fe}")

        try:
            save_feed_stats(db_con, feed, cycle_ts, feed_stat)
            db_con.commit()
        except Exception:
            logger.exception(f"Feed stats save error: {feed}")

    scored_entries.sort(key=lambda x: x[1], reverse=True)
    logger.info(f"Checked: {total_checked}, Filtered: {filtered_count}, Passed: {len(scored_entries)}")

    new_count = 0
    for entry, score, h, published_ts, category, feed_url in scored_entries[:max_per]:
        try:
            text = format_message(entry, cfg, category=category)
            entry_title = entry.get("title", "")

            # WhatsApp paylasim butonu + SPAM + Puan
            wa_text = quote(format_whatsapp_message(entry, category=category), safe="")
            wa_url = f"https://wa.me/?text={wa_text}"
            hash8 = h[:8]
            reply_markup = make_main_buttons(hash8, wa_url)

            for chat_id in chat_ids:
                msg_id = send_message(session, token, chat_id, text,
                                      disable_preview=disable_preview, logger=logger,
                                      max_retries=max_retries, retry_delay=retry_delay,
                                      reply_markup=reply_markup)
                # Feedback sistemi icin mesaj↔hash eslemesi kaydet
                if msg_id and msg_id is not True:
                    save_message_map(db_con, h, chat_id, msg_id,
                                     title=entry_title, category=category, wa_url=wa_url)
                time.sleep(1)  # Rate limit: 1 msg/sec

            db_con.execute("INSERT OR IGNORE INTO seen(hash, link, title, ts) VALUES(?,?,?,?)",
                           (h, entry.get("link", ""), entry_title, int(time.time())))
            save_title_fp(db_con, entry_title)

            # Haberi geciren keyword'leri kaydet (feedback analizi icin)
            kws = [m for m in include_matches_map.get(h, [])]
            save_article_keywords(db_con, h, kws)
            log_article(db_con, h, feed_url=feed_url, title=entry_title,
                        score=score, category=category, matched_kws=kws,
                        status="sent", logger=logger)

            db_con.commit()
            new_count += 1
            save_meta(db_con, "last_sent_ts", int(time.time()))
            logger.info(f"Sent (score={score}): {entry_title[:60]}...")
        except Exception as se:
            logger.exception(f"Send error: {se}")
            log_article(db_con, h, feed_url=feed_url, title=entry.get("title", ""),
                        status="error", reject_reason="send_error", logger=logger)

    logger.info(f"Cycle finished. New sent: {new_count}/{len(scored_entries)}")


# ── Main ──

def main():
    global _running, _reload_requested

    cfg_path = os.environ.get("TRB_CONFIG", "config.yaml")
    if not os.path.exists(cfg_path):
        print("ERROR: config.yaml not found. Copy config.sample.yaml to config.yaml and edit.")
        sys.exit(1)

    cfg = load_cfg(cfg_path)
    logger = setup_logger(cfg.get("log_path", "logs/bot.log"),
                          max_mb=float(cfg.get("max_log_size_mb", 5)),
                          backups=int(cfg.get("log_backups", 3)))

    logger.info("Telegram RSS Bot v4 (cigm) starting...")

    # Pre-normalize keywords once
    kw_index = KeywordIndex(cfg.get("include_keywords", []),
                            cfg.get("exclude_keywords", []),
                            cfg.get("clickbait_keywords", []))
    logger.info(f"Keywords: {len(kw_index.include)} include, "
                f"{len(kw_index.exclude)} exclude, {len(kw_index.clickbait)} clickbait")

    # Single DB connection
    db_con = ensure_db(cfg.get("database_path", "state.db"))
    atexit.register(db_con.close)

    # Session with retry
    session = create_session()

    # Signal handlers
    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        signal.signal(signal.SIGHUP, handle_sighup)
    except (OSError, AttributeError):
        pass  # SIGHUP not available on Windows

    logger.info(f"Feeds: {len(cfg.get('feeds', []))}; Interval: {cfg.get('interval_minutes')} min")

    # Feedback polling thread
    fb_thread = start_feedback_thread(cfg, db_con, session, logger,
                                       running_flag=lambda: _running)

    start_time = time.time()
    cycle_count = 0
    cleanup_days = int(cfg.get("cleanup_days", 30))
    health_interval = int(cfg.get("health_log_interval", 6))

    while _running:
        # Hot-reload config on SIGHUP
        if _reload_requested:
            _reload_requested = False
            try:
                cfg = load_cfg(cfg_path)
                kw_index = KeywordIndex(cfg.get("include_keywords", []),
                                        cfg.get("exclude_keywords", []),
                                        cfg.get("clickbait_keywords", []))
                cleanup_days = int(cfg.get("cleanup_days", 30))
                health_interval = int(cfg.get("health_log_interval", 6))
                logger.info(f"Config reloaded: {len(kw_index.include)} include, "
                            f"{len(kw_index.exclude)} exclude keywords")
            except Exception:
                logger.exception("Config reload failed, keeping old config")

        # Active hours check — smart sleep
        tzname = cfg.get("tz", "Europe/Istanbul")
        local_tz = tz.gettz(tzname)
        now_local = datetime.now(tz=local_tz)
        active = cfg.get("active_hours", "08:00-23:40")

        if not within_hours(now_local, active):
            wait = seconds_until_active(now_local, active)
            wait = min(wait, 3600)  # Cap 1 saat
            logger.info(f"Outside active hours; sleeping {wait}s until next active period")
            time.sleep(wait)
            continue

        # Run cycle — crash-proof
        try:
            cycle(cfg, kw_index, db_con, session, logger)
            cycle_count += 1
        except Exception:
            logger.exception("Unhandled error in cycle - will retry next interval")

        # Periodic DB cleanup
        if cycle_count > 0 and cycle_count % 10 == 0:
            try:
                deleted = cleanup_db(db_con, cleanup_days)
                if deleted:
                    logger.info(f"Cleaned {deleted} old entries from DB")
            except Exception:
                logger.exception("DB cleanup error")

        # Health logging
        if cycle_count > 0 and cycle_count % health_interval == 0:
            log_health(logger, cycle_count, start_time)

        # Haftalık rapor — son rapordan 7 gün geçmişse üret ve Telegram'a gönder
        last_report_ts = int(get_meta(db_con, "last_weekly_report_ts", "0") or "0")
        if time.time() - last_report_ts >= 7 * 86400:
            try:
                admin_chat_id = cfg.get("admin_chat_id")
                token = cfg["telegram_token"]
                report_text = write_weekly_report(db_con, logger=logger)

                if admin_chat_id and report_text:
                    # Telegram mesaj limiti 4096 karakter — gerekirse böl
                    chunk_size = 4000
                    chunks = [report_text[i:i+chunk_size]
                              for i in range(0, len(report_text), chunk_size)]
                    for chunk in chunks:
                        send_message(session, token, admin_chat_id,
                                     f"<pre>{chunk}</pre>",
                                     disable_preview=True, logger=logger)
                        time.sleep(1)
                    logger.info(f"Haftalik rapor Telegram'a gonderildi -> {admin_chat_id}")
                else:
                    # admin_chat_id yoksa dosyaya yaz (yedek)
                    report_path = cfg.get("weekly_report_path", "logs/haftalik_rapor.log")
                    write_weekly_report(db_con, report_path=report_path, logger=logger)

                save_meta(db_con, "last_weekly_report_ts", int(time.time()))
            except Exception:
                logger.exception("Haftalik rapor uretme hatasi")

        # Sleep until next cycle
        interval_sec = int(cfg.get("interval_minutes", 20)) * 60
        time.sleep(interval_sec)

    # Graceful shutdown
    logger.info("SIGTERM received - shutting down gracefully")
    db_con.close()


if __name__ == "__main__":
    main()
