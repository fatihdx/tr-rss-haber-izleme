import sqlite3, os, sys

DB = os.path.expanduser("~/telegram_rss_pro/state.db")
BOT = os.path.expanduser("~/telegram_rss_pro/src/bot.py")

print("=" * 50)
print("TANI RAPORU")
print("=" * 50)

# 1. Bot.py kontrol
print("\n[1] bot.py yeni kod mu?")
if os.path.exists(BOT):
    with open(BOT) as f:
        content = f.read()
    checks = {
        "admin_chat_id satiri": "admin_chat_id = cfg.get" in content,
        "log_article import": "log_article" in content,
        "haftalik rapor blogu": "Haftalik rapor" in content or "haftalik rapor" in content.lower(),
    }
    FB = os.path.expanduser("~/telegram_rss_pro/src/feedback.py")
    if os.path.exists(FB):
        with open(FB) as f:
            fb_content = f.read()
        checks["toggle_feedback (feedback.py)"] = "toggle_feedback" in fb_content
    for k, v in checks.items():
        print(f"  {'OK' if v else 'EKSIK'} — {k}")
else:
    print("  HATA: bot.py bulunamadi:", BOT)

# 2. DB kontrol
print("\n[2] Veritabani durumu")
if os.path.exists(DB):
    con = sqlite3.connect(DB)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("  Tablolar:", ", ".join(tables))

    ts = con.execute("SELECT v FROM meta WHERE k='last_weekly_report_ts'").fetchone()
    print("  last_weekly_report_ts:", ts[0] if ts else "YOK")

    admin = con.execute("SELECT v FROM meta WHERE k='admin_chat_id'").fetchone()
    print("  meta.admin_chat_id:", admin[0] if admin else "YOK (config'den okunuyor)")

    if "article_log" in tables:
        cnt = con.execute("SELECT COUNT(*) FROM article_log").fetchone()[0]
        sent = con.execute("SELECT COUNT(*) FROM article_log WHERE status='sent'").fetchone()[0]
        rej = con.execute("SELECT COUNT(*) FROM article_log WHERE status='rejected'").fetchone()[0]
        print(f"  article_log: toplam={cnt}, sent={sent}, rejected={rej}")
    else:
        print("  article_log: TABLO YOK")

    seen = con.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
    print(f"  seen: {seen} kayit")
    con.close()
else:
    print("  HATA: state.db bulunamadi:", DB)

print("\n" + "=" * 50)
