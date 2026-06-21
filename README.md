# 📰 TR-RSS · Toplumsal Hassasiyet Odaklı Haber İzleme

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Termux%20%2F%20Linux-orange.svg)](https://termux.dev/)

Türkçe haber kaynaklarından (RSS) **adli, hukuk ve asayiş** odaklı haberleri çekip, akıllı bir filtreden geçirerek Telegram grup/kanalınıza otomatik gönderen bot. 7/24 bir telefonda (Termux) veya Linux sunucuda çalışacak şekilde tasarlanmıştır.

---

## ✨ Özellikler

- **~23 haber kaynağı** — ana akım, muhalif ve uluslararası Türkçe RSS akışları
- **Akıllı filtreleme** — önce `exclude` (gürültü: spor, magazin, dış politika…) eler, sonra `include` (gözaltı, dava, operasyon, cinayet, örgüt…) tutar; skor + kategori atar
- **Türkçe-duyarlı eşleşme** — büyük/küçük harf ve diakritiklerden (ı/İ, ş, ğ, ç…) bağımsız
- **Mükerrer tespiti** — başlık parmak izi + Jaccard benzerliği ile aynı haberin tekrarını önler
- **Kategori sınıflandırma** — Operasyon, Yargılama, Cinayet, Terör, Soruşturma…
- **Geri bildirim sistemi** — her habere 💚 / 🚫 SPAM / 📝 puan / 💬 yorum butonları; veriler SQLite'a kaydedilir
- **Haftalık otomatik rapor** — 7 günde bir yönetici hesabına Telegram'dan özet (gönderim, eleme nedenleri, kaynak performansı, kategori, en etkin keyword'ler, feedback)
- **Tam denetim kaydı** — taranan her haber `article_log` tablosuna işlenir (durum, neden, skor, kategori)
- **Dayanıklılık** — runit ile otomatik yeniden başlatma, WiFi izleyici (watchdog), tekrar-deneme, log rotasyonu

---

## 🗂️ Proje Yapısı

```
.
├── src/
│   ├── bot.py          # Ana döngü: RSS çekme, filtre, gönderim, haftalık rapor
│   ├── utils.py        # Filtre motoru, skorlama, DB şeması, rapor üretimi
│   ├── feedback.py     # Telegram buton/geri bildirim işleyici (ayrı thread)
│   └── cities.json     # Konum çıkarımı için şehir listesi
├── scripts/            # start / stop / restart / status / logs / install / watchdog
├── sv/rssbot/          # runit servis tanımı (otomatik yeniden başlatma + log)
├── config.sample.yaml  # Örnek yapılandırma (kopyalayıp config.yaml yapın)
├── requirements.txt
├── diagnose.py         # Hızlı tanı aracı (DB + log sağlık kontrolü)
└── README.md
```

---

## 📋 Gereksinimler

- Python 3.11+
- `sqlite3` (CLI — durum/yedek scriptleri için)
- Bağımlılıklar: `feedparser`, `requests`, `python-dateutil`, `pytz`, `pyyaml`
- (Telefonda) [Termux](https://termux.dev/) + isteğe bağlı `runit`, `termux-boot`

---

## 🚀 Kurulum (Termux)

```bash
# 1) Paketler
pkg update && pkg install python sqlite git -y

# 2) Projeyi al
git clone https://github.com/fatihdx/tr-rss-haber-izleme.git
cd tr-rss-haber-izleme

# 3) Sanal ortam + bağımlılıklar
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4) Yapılandırma
cp config.sample.yaml config.yaml
nano config.yaml      # token, chat_ids, admin_chat_id doldurun
```

### config.yaml — doldurmanız gerekenler
| Alan | Açıklama |
|---|---|
| `telegram_token` | [@BotFather](https://t.me/BotFather) → `/newbot` ile alınan token |
| `chat_ids` | Haberlerin gideceği grup/kanal ID'si (`-100…` ile başlar) |
| `admin_chat_id` | Haftalık raporun gideceği kendi kullanıcı ID'niz |

> Botu hedef gruba **yönetici** olarak ekleyin (mesaj gönderebilmesi için).

---

## ▶️ Çalıştırma

**Hızlı test (ön planda):**
```bash
.venv/bin/python src/bot.py
```

**Kalıcı servis (runit ile, 7/24):**
```bash
bash scripts/install.sh     # servisi kurar
bash scripts/start.sh       # başlatır
bash scripts/status.sh      # durum + son loglar
bash scripts/logs.sh        # canlı log akışı (tail -f)
bash scripts/stop.sh        # durdurur
```

> ⚠️ `sv/rssbot/run` içindeki çalışma dizini yolu kuruluma göre düzenlenmelidir
> (varsayılan: `~/tr-rss-haber-izleme`).

---

## ⚙️ Filtreleme Nasıl Çalışır?

`config.yaml` içindeki üç liste haberin kaderini belirler:

1. **`exclude_keywords`** — bu kelimeleri içeren haber **engellenir** (önce kontrol edilir). Spor, magazin, ekonomi, dış politika, astroloji vb. gürültü.
2. **`include_keywords`** — sadece bu kelimeleri içeren haber **geçer**. Adli/hukuk/asayiş terimleri ve isteğe bağlı önemli dava adları (konu filtresi; takip/fişleme listesi değildir).
3. **`clickbait_keywords`** — süreç kelimesiyle birlikte geçerse **önceliklendirilir**.

Geçen haberlere bir **skor** ve **kategori** atanır; eşik altı kalanlar elenir. Tüm kararlar `article_log` tablosuna yazılır (sonradan analiz için).

---

## 💬 Geri Bildirim ve 📊 Haftalık Rapor

- Gönderilen her haberin altında **💚 / 🚫 SPAM / 📝 puan / 💬 yorum** butonları görünür. Tıklamalar `feedback` tablosuna kaydedilir → filtre kalitesini iyileştirmek için kullanılır.
- Her 7 günde bir bot, `admin_chat_id`'ye otomatik bir **haftalık özet** gönderir. (`last_weekly_report_ts` meta anahtarıyla izlenir.)

ÖRNEK
```
====================================================
  HAFTALIK RAPOR — 2026-05-16 23:44
====================================================

GENEL OZET
----------------------------------------------------
  Taranan:2660  Gonderilen:9(0.3%)
  Filtrelenen:2467  Mukerrer:184  Hata:0

ELEME NEDENLERI
----------------------------------------------------
  exclude              1020
  foreign              723
  no_include           468
  opinion              256

KAYNAK PERFORMANSI
----------------------------------------------------
  www.birgun.net/rss/home
    Taranan:210  Gonderilen:3(1%)  Filtrelenen:192  Mukerrer:15
  www.trthaber.com/sondakika.rss
    Taranan:210  Gonderilen:2(1%)  Filtrelenen:187  Mukerrer:21
  halktv.com.tr/service/rss.php
    Taranan:210  Gonderilen:1(0%)  Filtrelenen:201  Mukerrer:8
  www.cnnturk.com/feed/rss/all/news
    Taranan:210  Gonderilen:1(0%)  Filtrelenen:207  Mukerrer:2
  www.cumhuriyet.com.tr/rss/son_dakika.xml
    Taranan:210  Gonderilen:1(0%)  Filtrelenen:196  Mukerrer:13
  www.diken.com.tr/feed/
    Taranan:210  Gonderilen:1(0%)  Filtrelenen:154  Mukerrer:55
  bianet.org/biamag.rss
    Taranan:210  Gonderilen:0(0%)  Filtrelenen:196  Mukerrer:14
  haber.sol.org.tr/rss.xml
    Taranan:70  Gonderilen:0(0%)  Filtrelenen:70  Mukerrer:0
  www.ahaber.com.tr/rss/gundem.xml
    Taranan:210  Gonderilen:0(0%)  Filtrelenen:210  Mukerrer:0
  www.gazeteduvar.com.tr/export/rss
    Taranan:210  Gonderilen:0(0%)  Filtrelenen:210  Mukerrer:0
  www.haberturk.com/rss
    Taranan:210  Gonderilen:0(0%)  Filtrelenen:189  Mukerrer:21
  www.mynet.com/haber/rss/sondakika
    Taranan:210  Gonderilen:0(0%)  Filtrelenen:189  Mukerrer:21
  www.ntv.com.tr/gundem.rss
    Taranan:140  Gonderilen:0(0%)  Filtrelenen:126  Mukerrer:14
  www.sabah.com.tr/rss/gundem.xml
    Taranan:70  Gonderilen:0(0%)  Filtrelenen:70  Mukerrer:0
  www.star.com.tr/rss/rss.asp
    Taranan:70  Gonderilen:0(0%)  Filtrelenen:70  Mukerrer:0

KATEGORI DAGILIMI (gonderilen)
----------------------------------------------------
  Yargılama & Karar                2
  Gündem                           2
  Cinayet & Şiddet                 2
  Terör & Güvenlik                 1
  Soruşturma                       1
  Operasyon & Yakalama             1

EN ETKIN KEYWORDLER
----------------------------------------------------
  yakalandi                    2 haber
  tahliye                      2 haber
  supheli                      2 haber
  tutuklandi                   1 haber
  teror orgutu                 1 haber
  teror                        1 haber
  tahliye edildi               1 haber
  sorusturma                   1 haber
  polis                        1 haber
  operasyon                    1 haber
  oldurdu                      1 haber
  jandarma                     1 haber
  gozaltina alindi             1 haber
  gozaltina                    1 haber

FEEDBACK
----------------------------------------------------
  Toplam:19  Begendi:17  Begenmedim:2
  liked: 13
  whatsapp: 3
  spam: 2

FEEDBACK x KATEGORI
----------------------------------------------------
  Soruşturma                   +4 / -0
  Yargılama & Karar            +3 / -1
  Operasyon & Yakalama         +3 / -0
  Gündem                       +3 / -1
  Cinayet & Şiddet             +2 / -0
  Terör & Güvenlik             +1 / -0
  Organize Suç & Yolsuzluk     +1 / -0

====================================================
```
---

## 🗄️ Veri ve Loglar

- `state.db` (SQLite, WAL modu): `article_log`, `feedback`, `message_map`, `article_keywords`, `seen`, `meta`, `feed_stats`
- `logs/bot.log`: çalışma logu (rotasyonlu)
- Eski kayıtlar `cleanup_days` (varsayılan 30) sonrası temizlenir (yalnızca `seen` + `title_fps`).

---

## ⚖️ Kapsam, Sınırlar & Sorumlu Kullanım

- Bot yalnızca **kamuya açık RSS başlıklarını** izler; özel/kişisel veri toplamaz, bireyleri gözetlemez.
- **Suç tespiti veya hukuki tayin yapmaz**, kanaat üretmez. Kategoriler kural tabanlı konu etiketidir.
- `include`/`exclude` listeleri **konu filtresidir.
- Kaynak teliflerine saygı: yalnızca **başlık + bağlantı** iletilir/saklanır, tam metin değil.
- Amaç, toplumsal açıdan hassas (adli/asayiş/kamu düzeni) haberleri *derli toplu izlemektir*; infial/dezenformasyonu YAYILMADAN ÖNLEME AMACI TAŞIR.
- Yasal ve etik kullanım sorumluluğu işletici kullanıcıya aittir.

## 📄 Lisans

[Apache License 2.0](LICENSE) — bkz. `LICENSE` ve `NOTICE`.
