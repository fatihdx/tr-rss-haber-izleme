
import os, sys, time, re, html, hashlib, sqlite3, logging, logging.handlers, json
from datetime import datetime, time as dtime, timedelta
from dateutil import tz
from email.utils import parsedate_to_datetime


def setup_logger(log_path, max_mb=5, backups=3):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger("rssbot")
    logger.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=int(max_mb*1024*1024), backupCount=backups, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    return logger


def norm(s: str) -> str:
    # Buyuk harfleri ONCE degistir, sonra lower — İ.lower()='i\u0307' sorununu onler
    repl = {"ı":"i","İ":"i","ğ":"g","Ğ":"g","ş":"s","Ş":"s","ç":"c","Ç":"c","ö":"o","Ö":"o","ü":"u","Ü":"u"}
    s = "".join(repl.get(ch, ch) for ch in (s or ""))
    s = s.lower()
    # Combining dot above (U+0307) kalintisi temizle
    s = s.replace("\u0307", "")
    return s


# ══════════════════════════════════════════════════════════════
# KEYWORD INDEX + FILTRE
# ══════════════════════════════════════════════════════════════

class KeywordIndex:
    """Keyword'leri boot'ta bir kere normalize et, her entry'de sifir alloc."""

    # Include: muhafazakar esik — kisa keyword'ler kelime siniri ile eslesmeli
    # "ceza" sadece bagimsiz kelime olarak eslesmeli, "cezasi" eslesmemeli
    INCLUDE_BOUNDARY_THRESHOLD = 6

    # Exclude: agresif esik — Turkce ek'li formlar da yakalanmali
    # "iran" -> "irandan", "plaka" -> "plakalar", "sel" -> "selden" eslesmeli
    EXCLUDE_BOUNDARY_THRESHOLD = 4

    def __init__(self, include_kw, exclude_kw, clickbait_kw=None):
        self.include = sorted([norm(k) for k in (include_kw or [])], key=len, reverse=True)
        self.exclude = sorted([norm(k) for k in (exclude_kw or [])], key=len, reverse=True)
        self.clickbait = [norm(k) for k in (clickbait_kw or [])]

        self._include_patterns = [self._make_pattern(kw, self.INCLUDE_BOUNDARY_THRESHOLD) for kw in self.include]
        self._exclude_patterns = [self._make_pattern(kw, self.EXCLUDE_BOUNDARY_THRESHOLD) for kw in self.exclude]
        self._clickbait_patterns = [self._make_pattern(kw, self.INCLUDE_BOUNDARY_THRESHOLD) for kw in self.clickbait]

    @staticmethod
    def _make_pattern(kw, threshold):
        escaped = re.escape(kw)
        if len(kw) < threshold:
            return re.compile(r'(?<!\w)' + escaped + r'(?!\w)')
        return re.compile(escaped)


def _kw_in_text(pattern, text):
    return pattern.search(text) is not None


# ── Baglam diskalifiye ──
# Bu kelimeler haberde geciyorsa, bazi keyword eslesmelerini iptal eder.
# Ornek: "tahliye" + "kiraci" = ev tahliyesi, adli degil.

_CONTEXT_DISQUALIFIERS = {
    # keyword (normalized) -> bu kelimeler varsa keyword'u diskalifiye et
    norm("tahliye"): [norm(w) for w in [
        "kiracı", "kira", "ev sahibi", "eşya", "barınma", "konut",
        "emlak", "daire", "apartman", "kira artışı", "kira sözleşmesi",
        # Denizcilik / gemi tahliyesi
        "gemi", "liman", "hürmüz", "mürettebat", "personel", "boğaz",
    ]],
    norm("ifade"): [norm(w) for w in [
        # "ifade etti" = belirtti anlaminda kullanildiginda diskalifiye
        # Sadece "ifade etti/ettiği/ediliyor" formlarini yakala
    ]],
    # "savunma" = avukat savunmasi vs savunma sanayii / hava savunma
    norm("savunma"): [norm(w) for w in [
        "savunma sanayii", "savunma sanayi", "savunma bakanlığı",
        "milli savunma", "hava savunma", "savunma sistemi",
        "füze savunma", "silahlı kuvvetler", "savunma hamlesi",
    ]],
    # "mahkum" = hukumlu vs "mahkum olmak" (bagli kalmak / bagimli)
    norm("mahkum"): [norm(w) for w in [
        "kaynağa mahkum", "mahkum olmaktan", "mahkum kalmaktan",
        "bağımlılıktan", "tek kaynağa",
    ]],
    # "operasyon" = polis operasyonu vs askeri / tibbi operasyon
    norm("operasyon"): [norm(w) for w in [
        "askeri operasyon", "harekat", "tatbikat",
        "insani yardım", "ameliyat",
    ]],
}

# ── Zayif keyword'ler ──
# Bu keyword'ler tek baslarina haber gecirmek icin YETERLI DEGIL.
# En az 1 guclu (spesifik) keyword eslesmedikce haber reddedilir.
# Ornek: tek basina "ceza" = idari para cezasi olabilir -> RED
#         "ceza" + "gozaltina alindi" = adli haber -> GECERLI
_WEAK_KEYWORDS = {
    norm(k) for k in [
        "ceza", "dava", "suç", "yasak",
        "savunma", "mahkum", "operasyon",
        "hakim", "af", "tahliye", "hüküm",
    ]
}

# "ifade etti/ediliyor" = belirtti (siyasi/genel baglam), adli degil
_IFADE_NON_LEGAL = re.compile(norm("ifade et") + "|" + norm("ifade ed"))  # "ifade etti/ettiği/ediliyor/edildi"
_IFADE_LEGAL = re.compile(norm("ifade ver") + "|" + norm("ifadeye") + "|" + norm("ifadesi alın"))

# ── Yurt dışı filtresi ──
# Türkiye bağlamı varsa yurt dışı işareti görmezden gelinir.
_TURKEY_ANCHORS = frozenset(norm(k) for k in [
    "türkiye", "türk", "ankara", "istanbul", "izmir", "türkiye'de",
    "türk hükümeti", "cumhurbaşkanı", "tbmm", "meclis", "chp", "akp",
    "mhp", "hdp", "dem parti", "emniyet", "jandarma", "savcı",
    "mahkeme", "anadolu", "istanbul adliyesi",
])

_FOREIGN_ONLY_MARKERS = frozenset(norm(k) for k in [
    "abd", "white house", "beyaz saray", "pentagon", "nato üssü",
    "ukrayna", "rusya", "putin", "zelenski", "kremlin",
    "çin", "xi jinping", "beijing", "pekin",
    "fransa", "almanya", "ingiltere", "birleşik krallık", "avrupa birliği",
    "paris", "berlin", "londra", "brüksel", "washington",
    "kolombiya", "brezilya", "arjantin", "meksika", "küba", "venezüela",
    "irak", "suriye", "iran", "suudi arabistan", "israel", "israil",
    "hindistan", "pakistan", "japonya", "güney kore",
    "trump", "biden", "macron", "scholz",
])


def _is_foreign_only(full_text_norm):
    """Yalnızca yurt dışı içeriği mi? Türkiye bağlamı varsa False döner."""
    for anchor in _TURKEY_ANCHORS:
        if anchor in full_text_norm:
            return False
    for marker in _FOREIGN_ONLY_MARKERS:
        if marker in full_text_norm:
            return True
    return False


def _is_disqualified(kw, full_text):
    """Keyword baglam diskalifiye kontrolu."""
    # "ifade" ozel kontrolu
    if kw == norm("ifade"):
        # "ifade etti" mi (belirtti) yoksa "ifade verdi" mi (adli)?
        if _IFADE_LEGAL.search(full_text):
            return False  # adli baglam, gecerli
        if _IFADE_NON_LEGAL.search(full_text):
            return True  # "ifade etti" = belirtti, diskalifiye
        return False
    # "ifade verdi", "ifade özgürlüğü" gibi compound'lar her zaman gecerli
    if norm("ifade") in kw:
        return False

    # Genel diskalifiye kontrolleri
    disq_words = _CONTEXT_DISQUALIFIERS.get(kw)
    if disq_words:
        for dw in disq_words:
            if dw in full_text:
                return True
    return False


# ── Haber turu tespiti ──

# Kose yazisi / yorum / analiz kaliplari
_OPINION_TITLE_RE = re.compile(
    r'^[A-ZÇĞİÖŞÜa-zçğıöşü\s\.]+'  # Kisi adi
    r':\s+'                            # iki nokta + bosluk
    r'.+',                             # gorüs/alinti
)

_OPINION_URL_PATTERNS = [
    "/yorum/", "/analiz/", "/kose-yazisi/", "/yazarlar/",
    "/gorush/", "/makale/", "/opinion/",
]

_ADVISORY_KEYWORDS = [
    norm(w) for w in [
        "bu tuzağa dikkat", "uzmanlar uyarıyor", "uzmanlar uyardı",
        "sakın yapmayın", "dikkat edin", "aman dikkat",
        "nasıl korunursunuz", "bunlara dikkat",
        "püf noktası", "ipuçları",
    ]
]

# Bakanlik istatistik / aciklama kaliplari — haber degil
_STATS_PATTERNS = [
    re.compile(norm(p)) for p in [
        r"sayısı\s+\d+",          # "sayısı 170'e"
        r"sayıya\s+ulaştı",       # "sayıya ulaştı"
        r"\d+.e\s+yükseldi",      # "170'e yükseldi"
        r"yüzde\s+\d+",           # "yüzde 50"
        r"bütçe\s+açıklandı",
    ]
]

# RSS boilerplate kaliplari
_BOILERPLATE_PATTERNS = [
    re.compile(r'The post .+? appeared first on .+?\.', re.IGNORECASE),
    re.compile(r'Devamı için tıklayınız\.?', re.IGNORECASE),
    re.compile(r'\.{3,}Devamı için tıklayınız', re.IGNORECASE),
]


def _detect_article_type(title, summary, link=""):
    """
    Haber turunu tespit et.
    Returns: "news" | "opinion" | "advisory"
    """
    # 1. Kose yazisi: "Kisi Adi: gorusu" formati
    # Bakan/yetkili aciklamalari ve haber basliklarindaki iki noktayi eleme
    _NOT_OPINION_WORDS = (
        "bakan", "cumhurbaşkanı", "başkan", "vali", "müdür", "komutan",
        "genel başkan", "milletvekili", "lideri", "sözcüsü", "parti",
        "chp", "akp", "ak parti", "mhp", "hdp", "dem", "tbmm",
        # Konum/olay ifadeleri
        "'da", "'de", "'ta", "'te", "da ", "de ", "ta ", "te ",
    )
    if _OPINION_TITLE_RE.match(title) and ":" in title:
        before_colon = title.split(":")[0].strip()
        before_lower = before_colon.lower()
        # Yetkili/konum/parti baslikları kose yazisi degil
        if not any(w in before_lower for w in _NOT_OPINION_WORDS):
            word_count = len(before_colon.split())
            if 2 <= word_count <= 3:  # Sadece 2-3 kelime = kisi adi
                return "opinion"

    # 2. URL'de yorum/analiz patterni
    link_lower = link.lower()
    for pattern in _OPINION_URL_PATTERNS:
        if pattern in link_lower:
            return "opinion"

    # 3. Uyari/tavsiye yazisi
    full_norm = norm(f"{title} {summary}")
    for kw in _ADVISORY_KEYWORDS:
        if kw in full_norm:
            return "advisory"

    # 4. Bakanlik istatistik haberi — SADECE kapsam disi bakanliklar
    # Adalet/Icisleri bakanligi haberleri HER ZAMAN gecerli
    _RELEVANT_MINISTRIES = (
        norm("adalet"), norm("içişleri"), norm("emniyet"),
    )
    title_norm = norm(title)
    if norm("bakan") in title_norm:
        is_relevant_ministry = any(m in full_norm for m in _RELEVANT_MINISTRIES)
        if not is_relevant_ministry:
            for pat in _STATS_PATTERNS:
                if pat.search(full_norm):
                    return "stats"

    return "news"


_CATEGORY_MAP = [
    # (kategori_adi, keyword listesi) — ilk eslesen kategori secilir
    ("Cinayet & Şiddet", [
        "cinayet", "olduruldu", "oldurdu", "oldurmeye tesebbus", "katil", "katliam",
        "aile katliami", "kiskanclık cinayeti", "namus cinayeti", "tore cinayeti",
        "kadin cinayeti", "seri katil", "suikast", "bicaklandi", "vuruldu",
        "kursunlandi", "silahli saldiri", "ates ac", "darp", "darbedildi", "dayak",
        "agir yaralama", "kasten yaralama", "kasten oldurme",
    ]),
    ("Kadına & Çocuğa Şiddet", [
        "kadina siddet", "kadina yonelik siddet", "kadin olduruldu",
        "tecavuz", "cinsel saldiri", "cinsel istismar", "cinsel taciz", "taciz",
        "cocuk istismari", "cocuga taciz", "cocuga tecavuz", "pedofil",
    ]),
    ("Terör & Güvenlik", [
        "teror", "teror orgutu", "teror saldirisi", "terorist", "bombali saldiri",
        "bomba patladi", "patlama", "canli bomba", "pkk", "feto", "darbe",
    ]),
    ("Organize Suç & Yolsuzluk", [
        "orgut", "suc orgutu", "organize suc", "cete", "mafya", "rusvet",
        "zimmet", "yolsuzluk", "kara para", "dolandiricilik", "dolandirici",
        "sahtecilik", "evrak sahteciligi", "guveni kotuye kullanma",
    ]),
    ("Operasyon & Yakalama", [
        "gozaltina alindi", "gozalti", "yakalandi", "yakalama",
        "operasyon duzenlendi", "operasyon", "polis operasyonu",
        "safak baskini", "ev baskini", "el konuldu", "ele geciril",
        "kacakcilik", "kacak",
    ]),
    ("Yargılama & Karar", [
        "tutuklandi", "tutuklama", "iddianame", "dava", "durusma", "mahkeme",
        "beraat", "mahkum", "hapis ceza", "muebbet", "cezaevi",
        "temyiz", "istinaf", "kesinles", "tahliye", "adli kontrol",
    ]),
    ("Soruşturma", [
        "sorusturma", "savci", "savcilik", "bassavcilik", "fezleke",
        "bilirkisi", "adli tip", "delil", "suc duyurusu",
    ]),
    ("Hukuk & Yargı Sistemi", [
        "yargi", "adalet bakanligi", "adalet bakani", "anayasa mahkemesi",
        "anayasa", "danistay", "hak ihlali", "insan haklari",
        "basin ozgurlugu", "ifade ozgurlugu",
    ]),
    ("Emniyet & Kolluk", [
        "emniyet", "polis", "jandarma", "mit", "karakol",
    ]),
]

# Normalize edilmis lookup tablosu (bir kez olusturulur)
_CATEGORY_MAP_NORM = [(cat, [norm(kw) for kw in kws]) for cat, kws in _CATEGORY_MAP]


def _detect_category(include_matches):
    """Eslesen include keyword'lerine gore haber kategorisi tespit et."""
    for cat_name, cat_keywords in _CATEGORY_MAP_NORM:
        for match in include_matches:
            if match in cat_keywords:
                return cat_name
    return "Gündem"


def filter_entry_advanced(entry, kw_index):
    """
    Phrase-aware filtreleme + word boundary + baglam dogrulama + haber turu.
    Returns: (pass: bool, score: int, category: str, include_matches: list, reject_reason: str|None)
      reject_reason: None=geçti | "opinion" | "foreign" | "exclude" | "no_include" | "weak_only"
    """
    title = entry.get("title", "")
    summary = entry.get("summary", "")
    link = entry.get("link", "")
    full_text = norm(f"{title} {summary}")

    # 0. Haber turu kontrolu — kose yazisi, uyari, istatistik elenir
    article_type = _detect_article_type(title, summary, link)
    if article_type in ("opinion", "advisory", "stats"):
        return False, 0, "", [], "opinion"

    # 0b. Yurt dışı filtresi — Türkiye bağlamı olmayan yabancı haberler elenir
    if _is_foreign_only(full_text):
        return False, 0, "", [], "foreign"

    # 1. Include eslesmelerini bul (baglam diskalifiye ile)
    include_matches = []
    for kw, pat in zip(kw_index.include, kw_index._include_patterns):
        if _kw_in_text(pat, full_text):
            if not _is_disqualified(kw, full_text):
                include_matches.append(kw)

    # 2. Exclude kontrolu — phrase-aware
    for kw, pat in zip(kw_index.exclude, kw_index._exclude_patterns):
        if _kw_in_text(pat, full_text):
            covered = any(kw in inc for inc in include_matches)
            if not covered:
                return False, 0, "", [], "exclude"

    # 3. Include yoksa gec
    if not include_matches:
        return False, 0, "", [], "no_include"

    # 3b. Zayif keyword kontrolu — sadece genel keyword varsa gecirme
    strong_matches = [m for m in include_matches if m not in _WEAK_KEYWORDS]
    if not strong_matches:
        return False, 0, "", [], "weak_only"

    # 4. Skorla
    score = len(include_matches) * 10
    for kw, pat in zip(kw_index.clickbait, kw_index._clickbait_patterns):
        if _kw_in_text(pat, full_text):
            score += 5

    category = _detect_category(include_matches)
    return True, score, category, include_matches, None


# ══════════════════════════════════════════════════════════════
# HASH / ZAMAN / DB
# ══════════════════════════════════════════════════════════════

def item_hash(title, link, published_ts=None):
    base = f"{title}|{link}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _title_words(title):
    """Basliktan anlamli kelimeleri cikar (normalize + kisa/dolgu kelimeleri at)."""
    # Noktalama temizle
    clean = re.sub(r'[^\w\s]', '', norm(title))
    # 3 harften uzun kelimeleri al
    return set(w for w in clean.split() if len(w) > 3)


def title_fingerprint(title):
    """
    Baslik parmak izi — capraz kaynak mukerrer tespit icin.
    Normalize edip kisa kelimeleri atarak siralanmis ozdeger cikarir.
    """
    words = _title_words(title)
    return " ".join(sorted(words))


def within_hours(now_local, start_end_str):
    start_s, end_s = start_end_str.split("-")
    s_h, s_m = [int(x) for x in start_s.split(":")]
    e_h, e_m = [int(x) for x in end_s.split(":")]
    start_t = dtime(s_h, s_m)
    end_t = dtime(e_h, e_m)
    if start_t <= end_t:
        return start_t <= now_local.time() <= end_t
    else:
        return now_local.time() >= start_t or now_local.time() <= end_t


def seconds_until_active(now_local, start_end_str):
    """Aktif saate kac saniye kaldigini hesapla. Aktif ise 0 doner."""
    if within_hours(now_local, start_end_str):
        return 0
    start_s, _ = start_end_str.split("-")
    s_h, s_m = [int(x) for x in start_s.split(":")]
    target = now_local.replace(hour=s_h, minute=s_m, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    delta = (target - now_local).total_seconds()
    return max(int(delta), 0)


def parse_published(entry):
    if "published" in entry:
        try:
            dt = parsedate_to_datetime(entry.published)
            return int(dt.timestamp())
        except Exception:
            pass
    return int(time.time())


def ensure_db(path):
    con = sqlite3.connect(path, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS seen (
        hash TEXT PRIMARY KEY,
        link TEXT,
        title TEXT,
        ts INTEGER
    );""")
    con.execute("""CREATE TABLE IF NOT EXISTS meta (
        k TEXT PRIMARY KEY,
        v TEXT
    );""")
    # Mukerrer tespit icin fingerprint tablosu
    con.execute("""CREATE TABLE IF NOT EXISTS title_fps (
        fp TEXT PRIMARY KEY,
        ts INTEGER
    );""")
    # ── Feedback sistemi tablolari ──
    con.execute("""CREATE TABLE IF NOT EXISTS feedback (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        hash      TEXT NOT NULL,
        user_id   INTEGER NOT NULL,
        username  TEXT,
        rating    INTEGER,
        type      TEXT NOT NULL,
        tag       TEXT DEFAULT NULL,
        comment   TEXT DEFAULT NULL,
        ts        INTEGER NOT NULL,
        UNIQUE(hash, user_id)
    );""")
    con.execute("""CREATE TABLE IF NOT EXISTS article_keywords (
        hash    TEXT NOT NULL,
        keyword TEXT NOT NULL,
        PRIMARY KEY (hash, keyword)
    );""")
    con.execute("""CREATE TABLE IF NOT EXISTS message_map (
        hash       TEXT PRIMARY KEY,
        chat_id    TEXT NOT NULL,
        message_id INTEGER NOT NULL,
        title      TEXT,
        category   TEXT,
        wa_url     TEXT,
        ts         INTEGER
    );""")
    con.execute("""CREATE TABLE IF NOT EXISTS feed_stats (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        feed_url     TEXT NOT NULL,
        cycle_ts     INTEGER NOT NULL,
        checked      INTEGER DEFAULT 0,
        passed       INTEGER DEFAULT 0,
        r_opinion    INTEGER DEFAULT 0,
        r_foreign    INTEGER DEFAULT 0,
        r_no_include INTEGER DEFAULT 0,
        r_weak_only  INTEGER DEFAULT 0,
        r_exclude    INTEGER DEFAULT 0,
        r_duplicate  INTEGER DEFAULT 0
    );""")
    # article_log — her taranan haber için tek satır kayıt
    con.execute("""CREATE TABLE IF NOT EXISTS article_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        hash          TEXT NOT NULL,
        ts            INTEGER NOT NULL,
        feed_url      TEXT,
        title         TEXT,
        score         REAL,
        category      TEXT,
        matched_kws   TEXT,
        status        TEXT,
        reject_reason TEXT
    );""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_article_log_hash     ON article_log(hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_article_log_status   ON article_log(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_article_log_feed_url ON article_log(feed_url)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_article_log_ts       ON article_log(ts)")
    # feedback migration — username sütunu varsa güvenli şekilde kaldır
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(feedback)").fetchall()]
        if "username" in cols:
            con.execute("""CREATE TABLE IF NOT EXISTS feedback_new (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                hash      TEXT NOT NULL,
                user_id   INTEGER NOT NULL,
                rating    INTEGER,
                type      TEXT NOT NULL,
                tag       TEXT DEFAULT NULL,
                comment   TEXT DEFAULT NULL,
                ts        INTEGER NOT NULL,
                UNIQUE(hash, user_id)
            )""")
            con.execute("""INSERT OR IGNORE INTO feedback_new
                (id, hash, user_id, rating, type, tag, comment, ts)
                SELECT id, hash, user_id, rating, type, tag, comment, ts
                FROM feedback""")
            con.execute("DROP TABLE feedback")
            con.execute("ALTER TABLE feedback_new RENAME TO feedback")
    except Exception:
        pass  # migration başarısız — mevcut tablo bozulmaz
    con.commit()
    return con


def is_duplicate_title(con, title, threshold=0.65):
    """
    Baslik benzerlik kontrolu. Jaccard similarity ile.
    Son 48 saatteki basliklarla karsilastirir.
    threshold=0.65 -> %65 kelime ortusme = mukerrer.
    """
    new_words = _title_words(title)
    if len(new_words) < 2:
        return False

    # Exact fingerprint match
    fp = title_fingerprint(title)
    cur = con.execute("SELECT 1 FROM title_fps WHERE fp=?", (fp,)).fetchone()
    if cur:
        return True

    # Fuzzy match — son 48 saatteki basliklarla karsilastir
    cutoff = int(time.time()) - 172800
    rows = con.execute("SELECT fp FROM title_fps WHERE ts > ?", (cutoff,)).fetchall()
    for (existing_fp,) in rows:
        existing_words = set(existing_fp.split())
        if not existing_words:
            continue
        overlap = len(new_words & existing_words)
        union = len(new_words | existing_words)
        if union > 0 and overlap / union >= threshold:
            return True

    return False


def save_title_fp(con, title):
    """Baslik parmak izini kaydet."""
    fp = title_fingerprint(title)
    if fp:
        con.execute("INSERT OR IGNORE INTO title_fps(fp, ts) VALUES(?,?)",
                     (fp, int(time.time())))


def cleanup_db(con, days=30):
    """Eski kayitlari sil. Silinen satir sayisini doner."""
    cutoff = int(time.time()) - (days * 86400)
    cur = con.execute("DELETE FROM seen WHERE ts < ?", (cutoff,))
    deleted = cur.rowcount
    con.execute("DELETE FROM title_fps WHERE ts < ?", (cutoff,))
    con.commit()
    return deleted


def save_meta(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
    con.commit()


def get_meta(con, k, default=None):
    cur = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return cur[0] if cur else default


# ══════════════════════════════════════════════════════════════
# KONUM CIKARIMI
# ══════════════════════════════════════════════════════════════

_cities_data = None

def _load_cities():
    global _cities_data
    if _cities_data is not None:
        return _cities_data
    cities_path = os.path.join(os.path.dirname(__file__), "cities.json")
    try:
        with open(cities_path, "r", encoding="utf-8") as f:
            _cities_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _cities_data = {"cities": [], "aliases": {}}
    return _cities_data


def extract_location(text):
    """Haberden il adi cikar. Bulamazsa None doner."""
    data = _load_cities()
    for alias, city in data.get("aliases", {}).items():
        if not alias or not city:  # bos alias veya bos hedef -> atla
            continue
        if alias in text:
            return city
    for city in data.get("cities", []):
        if city in text:
            return city
    return None


# ══════════════════════════════════════════════════════════════
# ADLİ SUREC OZETI
# ══════════════════════════════════════════════════════════════

# "tahliye" baglam kontrolu icin
_TAHLIYE_CIVIL_CONTEXT = re.compile(
    r'kiracı|kira\w*|ev sahibi|eşya|barınma|konut|emlak|daire|apartman',
    re.IGNORECASE
)

_LEGAL_STAGES = [
    # ── KOLLUK / ON ISLEM ──
    (re.compile(r'gözaltı', re.IGNORECASE), "Gözaltına alındı"),
    (re.compile(r'yakalan\w*', re.IGNORECASE), "Yakalandı"),
    (re.compile(r'adliyeye sevk', re.IGNORECASE), "Adliyeye sevk edildi"),
    (re.compile(r'ifade\w*\s+(?:ver|alın|çağır)', re.IGNORECASE), "İfadesi alındı"),
    (re.compile(r'tutuklan\w*', re.IGNORECASE), "Tutuklandı"),
    (re.compile(r'adli kontrol', re.IGNORECASE), "Adli kontrol şartıyla serbest bırakıldı"),
    (re.compile(r'serbest bırakıl\w*', re.IGNORECASE), "Serbest bırakıldı"),
    # tahliye — ozel baglam kontrolu var, asagida handle ediliyor

    # ── SORUSTURMA / KOVUSTURMA ──
    (re.compile(r'soruşturma\w*\s+başlat', re.IGNORECASE), "Soruşturma başlatıldı"),
    (re.compile(r'iddianame', re.IGNORECASE), "İddianame hazırlandı"),
    (re.compile(r'dava\w*\s+açıl', re.IGNORECASE), "Dava açıldı"),
    (re.compile(r'duruşma', re.IGNORECASE), "Duruşma görüldü"),

    # ── KARAR ──
    (re.compile(r'beraat', re.IGNORECASE), "Beraat etti"),
    (re.compile(r'mahk[uû]m\w*', re.IGNORECASE), "Mahkumiyet kararı verildi"),
    (re.compile(r'hapis ceza', re.IGNORECASE), "Hapis cezasına çarptırıldı"),
    (re.compile(r'müebbet', re.IGNORECASE), "Müebbet hapis cezası verildi"),
    (re.compile(r'cezaevi', re.IGNORECASE), "Cezaevine gönderildi"),

    # ── KANUN YOLU ──
    (re.compile(r'temyiz', re.IGNORECASE), "Karar temyize taşındı"),
    (re.compile(r'istinaf', re.IGNORECASE), "İstinaf başvurusu yapıldı"),
    (re.compile(r'kesinleş\w*', re.IGNORECASE), "Karar kesinleşti"),

    # ── SUC TURLERI ──
    (re.compile(r'öldürül\w*|öldürd\w*|cinayet', re.IGNORECASE), "Cinayet soruşturması"),
    (re.compile(r'bıçaklan\w*|bıçaklı', re.IGNORECASE), "Bıçaklı saldırı"),
    (re.compile(r'silahl\w+\s+saldır', re.IGNORECASE), "Silahlı saldırı"),
    (re.compile(r'vuruldu|ateş aç\w*|kurşunlan\w*', re.IGNORECASE), "Silahlı saldırı"),
    (re.compile(r'tecavüz|cinsel\s+(?:saldırı|istismar|taciz)', re.IGNORECASE), "Cinsel suç soruşturması"),
    (re.compile(r'kaçak\w*|kaçakçılık', re.IGNORECASE), "Kaçakçılık operasyonu"),
    (re.compile(r'ele geçiril\w*', re.IGNORECASE), "Malzeme ele geçirildi"),
    (re.compile(r'operasyon\w*\s+düzenlen', re.IGNORECASE), "Operasyon düzenlendi"),
    (re.compile(r'dolandırıcılık|dolandır\w*', re.IGNORECASE), "Dolandırıcılık soruşturması"),
    (re.compile(r'kara\s*para\s*akla', re.IGNORECASE), "Kara para aklama soruşturması"),
    (re.compile(r'rüşvet', re.IGNORECASE), "Rüşvet soruşturması"),
    (re.compile(r'zimmet', re.IGNORECASE), "Zimmet soruşturması"),
]

# Genel "sorusturma" — sadece daha spesifik bir suc turu yoksa goster
_SORUSTURMA_GENERIC = re.compile(r'soruşturma', re.IGNORECASE)

# Spesifik sorusturma turleri (bunlardan biri varsa genel "sorusturma" tekrar gosterilmez)
_SPECIFIC_SORUSTURMA = {
    "Cinayet soruşturması", "Dolandırıcılık soruşturması",
    "Kara para aklama soruşturması", "Rüşvet soruşturması",
    "Zimmet soruşturması", "Cinsel suç soruşturması",
    "Soruşturma başlatıldı",
}

# Tahliye pattern — ayri handle edilecek
_TAHLIYE_RE = re.compile(r'tahliye', re.IGNORECASE)

# "operasyon" baglam kontrolu — askeri/kurtarma operasyonlarini ele
_OPERASYON_NON_CRIME = re.compile(
    r'arama\s+kurtarma|kurtarma\s+operasyon|askeri\s+operasyon|tatbikat|insani\s+yardım',
    re.IGNORECASE
)


def extract_legal_summary(title, summary):
    """
    Haberdeki hukuki surec terimlerini tespit edip adli surec ozeti olustur.
    Baglam dogrulama: tahliye evi mi adli mi, ifade etti mi verdi mi.
    Tekrar onleme: "sorusturma yurutuluyor" + "rusvet sorusturmasi" -> sadece ikincisi.
    """
    full = f"{title} {summary}"
    found = []
    seen_texts = set()

    for pattern, stage_text in _LEGAL_STAGES:
        if pattern.search(full) and stage_text not in seen_texts:
            # "Operasyon duzenlendi" — askeri/kurtarma baglaminda ekleme
            if stage_text == "Operasyon düzenlendi" and _OPERASYON_NON_CRIME.search(full):
                continue
            found.append(stage_text)
            seen_texts.add(stage_text)

    # Tahliye — sadece adli baglamda ekle
    if _TAHLIYE_RE.search(full):
        if not _TAHLIYE_CIVIL_CONTEXT.search(full):
            if "Tahliye edildi" not in seen_texts:
                found.append("Tahliye edildi")
                seen_texts.add("Tahliye edildi")

    # Genel "sorusturma yurutuluyor" — spesifik tur varsa ekleme
    has_specific = any(s in seen_texts for s in _SPECIFIC_SORUSTURMA)
    if not has_specific and _SORUSTURMA_GENERIC.search(full):
        if "Soruşturma yürütülüyor" not in seen_texts:
            found.append("Soruşturma yürütülüyor")

    if not found:
        return None

    # En fazla 3 asama, her biri buyuk harfle
    return ". ".join(found[:3]) + "."


# ══════════════════════════════════════════════════════════════
# METIN TEMIZLEME
# ══════════════════════════════════════════════════════════════

def clean_summary(text):
    """
    RSS summary metnini temizle:
    - HTML tag'leri kaldir
    - &nbsp; ve diger HTML entity'leri coz
    - Boilerplate kaliplari sil
    - Kelime sinirina gore kirp
    """
    # HTML tag'leri
    text = re.sub(r'<[^>]+>', '', text)

    # HTML entities
    text = html.unescape(text)

    # Boilerplate kaliplari
    for pattern in _BOILERPLATE_PATTERNS:
        text = pattern.sub('', text)

    # Bos satirlari ve fazla bosluklari temizle
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def smart_truncate(text, max_len=500):
    """Kelime veya cumle sinirina gore kirp, kelime ortasindan kesme."""
    if len(text) <= max_len:
        return text

    # Son cumle sinirini bul (. ! ?)
    truncated = text[:max_len]
    last_sentence = max(
        truncated.rfind('. '),
        truncated.rfind('! '),
        truncated.rfind('? '),
    )
    if last_sentence > max_len * 0.5:  # En az yarisini goster
        return truncated[:last_sentence + 1]

    # Cumle siniri bulunamazsa kelime sinirina gore kes
    last_space = truncated.rfind(' ')
    if last_space > max_len * 0.7:
        return truncated[:last_space] + "..."

    return truncated + "..."


# ══════════════════════════════════════════════════════════════
# FEEDBACK YARDIMCI FONKSİYONLARI
# ══════════════════════════════════════════════════════════════

def find_similar_sent(con, title, hours=48, top_n=3):
    """Son N saatte gonderilen haberlerden baslık benzerligine gore en yakin olanlari bul."""
    cutoff = int(time.time()) - (hours * 3600)
    new_words = _title_words(title)
    if len(new_words) < 2:
        return []

    rows = con.execute(
        "SELECT hash, title FROM seen WHERE ts > ? ORDER BY ts DESC", (cutoff,)
    ).fetchall()

    scored = []
    for h, t in rows:
        existing_words = _title_words(t or "")
        if not existing_words:
            continue
        overlap = len(new_words & existing_words)
        union = len(new_words | existing_words)
        jaccard = overlap / union if union > 0 else 0
        if jaccard > 0.15:
            scored.append((h, t, jaccard))

    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:top_n]


def save_article_keywords(con, article_hash, keywords):
    """Haberi geciren keyword'leri kaydet (feedback analizi icin)."""
    for kw in keywords:
        con.execute(
            "INSERT OR IGNORE INTO article_keywords(hash, keyword) VALUES(?,?)",
            (article_hash, kw)
        )


def save_message_map(con, article_hash, chat_id, message_id, title="", category="", wa_url=""):
    """Mesaj ID ↔ hash eslemesi kaydet (buton guncelleme icin)."""
    con.execute(
        "INSERT OR REPLACE INTO message_map(hash, chat_id, message_id, title, category, wa_url, ts) "
        "VALUES(?,?,?,?,?,?,?)",
        (article_hash, str(chat_id), message_id, title, category, wa_url, int(time.time()))
    )


def save_feed_stats(con, feed_url, cycle_ts, stats):
    """Feed başına döngü istatistiğini kaydet."""
    con.execute(
        "INSERT INTO feed_stats(feed_url, cycle_ts, checked, passed, "
        "r_opinion, r_foreign, r_no_include, r_weak_only, r_exclude, r_duplicate) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (feed_url, cycle_ts,
         stats.get("checked", 0), stats.get("passed", 0),
         stats.get("r_opinion", 0), stats.get("r_foreign", 0),
         stats.get("r_no_include", 0), stats.get("r_weak_only", 0),
         stats.get("r_exclude", 0), stats.get("r_duplicate", 0))
    )


def log_article(con, hash_, feed_url=None, title=None, score=None,
                category=None, matched_kws=None, status="seen",
                reject_reason=None, ts=None, logger=None):
    """Her taranan haber için article_log'a tek satır yazar. Hata botu durdurmaz."""
    try:
        if ts is None:
            ts = int(time.time())
        if isinstance(matched_kws, (list, tuple)):
            matched_kws = json.dumps(matched_kws, ensure_ascii=False)
        con.execute(
            "INSERT INTO article_log(hash, ts, feed_url, title, score, category, "
            "matched_kws, status, reject_reason) VALUES(?,?,?,?,?,?,?,?,?)",
            (hash_, ts, feed_url, title, score if score else None,
             category or None, matched_kws, status, reject_reason)
        )
    except Exception as exc:
        if logger:
            logger.debug(f"log_article error: {exc}")


def write_weekly_report(con, report_path=None, logger=None):
    """Son 7 günün verilerini üretir. report_path verilirse dosyaya yazar, her zaman metni döndürür."""
    cutoff = int(time.time()) - 7 * 86400
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    L = []
    L.append("=" * 52)
    L.append(f"  HAFTALIK RAPOR — {now_str}")
    L.append("=" * 52)

    # ── Genel özet (article_log'dan) ──
    total_row = con.execute(
        "SELECT COUNT(*) FROM article_log WHERE ts > ?", (cutoff,)
    ).fetchone()
    total_scanned = total_row[0] if total_row else 0
    sent_cnt = con.execute(
        "SELECT COUNT(*) FROM article_log WHERE ts > ? AND status='sent'", (cutoff,)
    ).fetchone()[0]
    rejected_cnt = con.execute(
        "SELECT COUNT(*) FROM article_log WHERE ts > ? AND status='rejected'", (cutoff,)
    ).fetchone()[0]
    duplicate_cnt = con.execute(
        "SELECT COUNT(*) FROM article_log WHERE ts > ? AND status='duplicate'", (cutoff,)
    ).fetchone()[0]
    error_cnt = con.execute(
        "SELECT COUNT(*) FROM article_log WHERE ts > ? AND status='error'", (cutoff,)
    ).fetchone()[0]

    L.append("")
    L.append("GENEL OZET")
    L.append("-" * 52)
    pass_rate = f"{sent_cnt/total_scanned*100:.1f}%" if total_scanned else "0%"
    L.append(f"  Taranan:{total_scanned}  Gonderilen:{sent_cnt}({pass_rate})")
    L.append(f"  Filtrelenen:{rejected_cnt}  Mukerrer:{duplicate_cnt}  Hata:{error_cnt}")

    # ── Eleme nedenleri ──
    reason_rows = con.execute("""
        SELECT reject_reason, COUNT(*) as cnt FROM article_log
        WHERE ts > ? AND status='rejected' AND reject_reason IS NOT NULL
        GROUP BY reject_reason ORDER BY cnt DESC
    """, (cutoff,)).fetchall()
    if reason_rows:
        L.append("")
        L.append("ELEME NEDENLERI")
        L.append("-" * 52)
        for reason, cnt in reason_rows:
            L.append(f"  {reason:<20} {cnt}")

    # ── Kaynak performansı (article_log'dan) ──
    feed_rows = con.execute("""
        SELECT feed_url,
               COUNT(*) as total,
               SUM(CASE WHEN status='sent'     THEN 1 ELSE 0 END) as sent,
               SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) as rej,
               SUM(CASE WHEN status='duplicate'THEN 1 ELSE 0 END) as dup
        FROM article_log WHERE ts > ?
        GROUP BY feed_url ORDER BY sent DESC
    """, (cutoff,)).fetchall()
    if feed_rows:
        L.append("")
        L.append("KAYNAK PERFORMANSI")
        L.append("-" * 52)
        for url, total, sent, rej, dup in feed_rows:
            short = (url or "?").replace("https://", "").replace("http://", "")[:46]
            rate = f"{sent/total*100:.0f}%" if total else "0%"
            L.append(f"  {short}")
            L.append(f"    Taranan:{total}  Gonderilen:{sent}({rate})  Filtrelenen:{rej}  Mukerrer:{dup}")

    # ── Kategori dağılımı (gönderilen haberler) ──
    cat_rows = con.execute("""
        SELECT category, COUNT(*) as cnt FROM article_log
        WHERE ts > ? AND status='sent' AND category IS NOT NULL
        GROUP BY category ORDER BY cnt DESC
    """, (cutoff,)).fetchall()
    if cat_rows:
        L.append("")
        L.append("KATEGORI DAGILIMI (gonderilen)")
        L.append("-" * 52)
        for cat, cnt in cat_rows:
            L.append(f"  {cat:<32} {cnt}")

    # ── En etkin keywordler ──
    kw_rows = con.execute("""
        SELECT ak.keyword, COUNT(*) as cnt
        FROM article_keywords ak
        JOIN article_log al ON ak.hash = al.hash
        WHERE al.ts > ? AND al.status = 'sent'
        GROUP BY ak.keyword ORDER BY cnt DESC LIMIT 15
    """, (cutoff,)).fetchall()
    if kw_rows:
        L.append("")
        L.append("EN ETKIN KEYWORDLER")
        L.append("-" * 52)
        for kw, cnt in kw_rows:
            L.append(f"  {kw:<28} {cnt} haber")

    # ── Eşiğe yakın elenmiş haberler (borderline) ──
    borderline_rows = con.execute("""
        SELECT title, score, reject_reason FROM article_log
        WHERE ts > ? AND status='rejected' AND score IS NOT NULL AND score > 0
        ORDER BY score DESC LIMIT 10
    """, (cutoff,)).fetchall()
    if borderline_rows:
        L.append("")
        L.append("ESIGE YAKIN ELENENMIS HABERLER")
        L.append("-" * 52)
        for title, score, reason in borderline_rows:
            t = (title or "")[:44]
            L.append(f"  [{score:.2f}] {t}  ({reason})")

    # ── Feedback özeti ──
    total_fb = con.execute("SELECT COUNT(*) FROM feedback WHERE ts > ?", (cutoff,)).fetchone()[0]
    liked = con.execute("SELECT COUNT(*) FROM feedback WHERE ts > ? AND rating > 0", (cutoff,)).fetchone()[0]
    disliked = con.execute("SELECT COUNT(*) FROM feedback WHERE ts > ? AND rating < 0", (cutoff,)).fetchone()[0]
    tag_rows = con.execute("""
        SELECT tag, COUNT(*) as cnt FROM feedback
        WHERE ts > ? AND tag IS NOT NULL
        GROUP BY tag ORDER BY cnt DESC
    """, (cutoff,)).fetchall()
    L.append("")
    L.append("FEEDBACK")
    L.append("-" * 52)
    L.append(f"  Toplam:{total_fb}  Begendi:{liked}  Begenmedim:{disliked}")
    for tag, cnt in tag_rows:
        L.append(f"  {tag}: {cnt}")

    # ── Feedback × kategori ──
    fb_cat_rows = con.execute("""
        SELECT mm.category,
               SUM(CASE WHEN f.rating > 0 THEN 1 ELSE 0 END) as likes,
               SUM(CASE WHEN f.rating < 0 THEN 1 ELSE 0 END) as dislikes
        FROM feedback f JOIN message_map mm ON f.hash = mm.hash
        WHERE f.ts > ?
        GROUP BY mm.category ORDER BY likes DESC
    """, (cutoff,)).fetchall()
    if fb_cat_rows:
        L.append("")
        L.append("FEEDBACK x KATEGORI")
        L.append("-" * 52)
        for cat, likes, dislikes in fb_cat_rows:
            L.append(f"  {(cat or '?'):<28} +{likes} / -{dislikes}")

    L.append("")
    L.append("=" * 52)

    report = "\n".join(L)

    if report_path:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        if logger:
            logger.info(f"Haftalik rapor yazildi: {report_path}")

    return report


def save_feedback(con, article_hash, user_id, rating, fb_type, tag=None, comment=None):
    """Geri bildirim kaydet. Ayni kullanici ayni habere 1 kez oy verebilir (UPSERT)."""
    con.execute(
        "INSERT INTO feedback(hash, user_id, rating, type, tag, comment, ts) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(hash, user_id) DO UPDATE SET rating=?, type=?, tag=?, comment=?, ts=?",
        (article_hash, user_id, rating, fb_type, tag, comment, int(time.time()),
         rating, fb_type, tag, comment, int(time.time()))
    )


def toggle_feedback(con, article_hash, user_id, rating, fb_type, tag=None):
    """Beğeni toggle: aynı oy → DELETE, farklı oy → UPDATE, yeni → INSERT."""
    existing = con.execute(
        "SELECT type FROM feedback WHERE hash=? AND user_id=?",
        (article_hash, user_id)
    ).fetchone()
    if existing:
        if existing[0] == fb_type:
            con.execute("DELETE FROM feedback WHERE hash=? AND user_id=?",
                        (article_hash, user_id))
            return "deleted"
        else:
            con.execute(
                "UPDATE feedback SET rating=?, type=?, tag=?, ts=? WHERE hash=? AND user_id=?",
                (rating, fb_type, tag, int(time.time()), article_hash, user_id)
            )
            return "updated"
    else:
        con.execute(
            "INSERT INTO feedback(hash, user_id, rating, type, tag, ts) VALUES(?,?,?,?,?,?)",
            (article_hash, user_id, rating, fb_type, tag, int(time.time()))
        )
        return "inserted"
