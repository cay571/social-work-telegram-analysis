#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_veri_cek.py - Telegram grubundan araştırma verisinin yeniden toplanması

Ne yapar?
  * Grubun mesajlarını en eskiden en yeniye doğru çeker. Medya dosyası indirmez.
  * Göndericileri toplama anında takma adlandırır (HMAC-SHA256 + gizli anahtar).
    Gerçek kullanıcı kimlikleri hiçbir dosyaya yazılmaz.
  * Önceki veride olmayan alanları kaydeder: yanıt ilişkisi (reply_to), yönlendirme,
    servis mesajları (katıldı/ayrıldı vb.), gönderici türü (kullanıcı, bot, anonim
    yönetici), medya türü, gizli bağlantılar dahil URL'ler, hashtag'ler, tepki sayısı.
  * Ham metnin yanında kişisel bilgileri maskelenmiş bir metin sürümü üretir.
  * 5.000 mesajlık parçalar halinde Parquet'e yazar. Kesilirse aynı komutla
    kaldığı yerden devam eder.
  * Bitince veri akışı özetini loglar/toplama_ozeti.json dosyasına yazar.

Kullanım:
  python 01_veri_cek.py          -> topla (ya da kaldığı yerden devam et) + özet
  python 01_veri_cek.py --ozet   -> yalnızca özeti yeniden üret
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Klasörler ve sabitler
# ---------------------------------------------------------------------------
BASE = Path(__file__).resolve().parent
RAW_DIR = BASE / "veri" / "ham"          # Parquet parçaları (erişimi kısıtlı tutun)
META_DIR = BASE / "veri" / "meta"        # Grup bilgileri, yönetici takma adları
SECRET_DIR = BASE / "gizli"              # salt.key ve oturum dosyası (ASLA paylaşmayın)
LOG_DIR = BASE / "loglar"
STATE_FILE = LOG_DIR / "durum.json"
SALT_FILE = SECRET_DIR / "salt.key"
SESSION_FILE = SECRET_DIR / "telegram_oturum"   # Telethon .session uzantısını ekler
SETTINGS_FILE = BASE / "ayarlar.env"

BATCH_SIZE = 5000
PROGRESS_EVERY = 2000

# Türkiye 2016'dan beri sabit UTC+3 kullanıyor; 2020-2026 dönemi için doğrudur.
TR_TZ = timezone(timedelta(hours=3), "Europe/Istanbul")

SCHEMA = pa.schema([
    ("msg_id", pa.int64()),
    ("date_utc", pa.timestamp("s", tz="UTC")),
    ("edit_date_utc", pa.timestamp("s", tz="UTC")),
    ("sender_type", pa.string()),        # user / bot / anon_admin / channel / no_from_id
    ("sender_pid", pa.string()),         # takma ad (HMAC)
    ("is_service", pa.bool_()),
    ("action_type", pa.string()),        # servis mesajı türü (ör. MessageActionChatAddUser)
    ("reply_to_msg_id", pa.int64()),
    ("reply_to_top_id", pa.int64()),
    ("reply_to_other_chat", pa.bool_()),
    ("is_forward", pa.bool_()),
    ("fwd_from_type", pa.string()),      # user / channel / chat / hidden_user
    ("fwd_from_pid", pa.string()),
    ("fwd_date_utc", pa.timestamp("s", tz="UTC")),
    ("has_media", pa.bool_()),
    ("media_type", pa.string()),
    ("doc_mime", pa.string()),
    ("grouped_id", pa.int64()),          # albümler aynı grouped_id'yi paylaşır
    ("is_pinned", pa.bool_()),
    ("via_bot", pa.bool_()),
    ("has_post_author", pa.bool_()),
    ("total_reactions", pa.int32()),
    ("n_urls", pa.int32()),
    ("n_hidden_urls", pa.int32()),       # metinde görünmeyen, bağlantı olarak gömülü URL'ler
    ("urls_json", pa.string()),
    ("hashtags_json", pa.string()),
    ("n_mentions", pa.int32()),
    ("text_len", pa.int32()),
    ("text_raw", pa.string()),           # HAM_METNI_SAKLA=0 ise boş kalır
    ("text_redacted", pa.string()),      # URL, e-posta, telefon, TC no, IBAN, @kullanıcı maskeli
])


# ---------------------------------------------------------------------------
# Yardımcılar: ayarlar, takma ad, durum dosyası
# ---------------------------------------------------------------------------
def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date(value):
    """'YYYY-MM-DD' (Türkiye saatiyle gece yarısı) -> UTC datetime. Boşsa None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        sys.exit(f"Tarih biçimi hatalı: '{value}'. YYYY-MM-DD kullanın (ör. 2026-07-01).")
    return d.replace(tzinfo=TR_TZ).astimezone(timezone.utc)


def load_settings():
    if not SETTINGS_FILE.exists():
        sys.exit("ayarlar.env bulunamadı. ayarlar_ornek.env dosyasını ayarlar.env adıyla "
                 "kopyalayıp doldurun.")
    from dotenv import load_dotenv
    load_dotenv(SETTINGS_FILE, override=True)

    def need(key):
        val = os.environ.get(key, "").strip()
        if not val:
            sys.exit(f"ayarlar.env içinde {key} boş.")
        return val

    try:
        api_id = int(need("TG_API_ID"))
    except ValueError:
        sys.exit("TG_API_ID yalnızca rakamlardan oluşmalı.")

    return {
        "api_id": api_id,
        "api_hash": need("TG_API_HASH"),
        "group": need("TG_GRUP"),
        "start": parse_date(os.environ.get("BASLANGIC_TARIHI")),
        "end": parse_date(os.environ.get("BITIS_TARIHI")),
        "store_raw": os.environ.get("HAM_METNI_SAKLA", "1").strip() == "1",
    }


def load_or_create_salt():
    SECRET_DIR.mkdir(parents=True, exist_ok=True)
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = secrets.token_bytes(32)
    SALT_FILE.write_bytes(salt)
    print("Yeni gizli anahtar oluşturuldu: gizli/salt.key\n"
          "  Bu dosyayı güvenli bir yere yedekleyin. Kaybolursa sonraki toplamalarda\n"
          "  aynı kişiye aynı takma ad verilemez. Kimseyle paylaşmayın.")
    return salt


def salt_fingerprint(salt):
    return hashlib.sha256(salt).hexdigest()[:12]


def make_pid(salt, kind, raw_id):
    if raw_id is None:
        return None
    return hmac.new(salt, f"{kind}:{raw_id}".encode(), hashlib.sha256).hexdigest()[:16]


def settings_fingerprint(settings):
    return {
        "group": settings["group"],
        "start": settings["start"].isoformat() if settings["start"] else None,
        "end": settings["end"].isoformat() if settings["end"] else None,
        "store_raw": settings["store_raw"],
    }


def save_state(state):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def part_bounds(path):
    _, first, last = path.stem.split("_")
    return int(first), int(last)


def load_or_rebuild_state(settings, salt):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for tmp in RAW_DIR.glob("*.tmp"):
        tmp.unlink()
    parts = sorted(RAW_DIR.glob("parca_*.parquet"))

    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("settings") != settings_fingerprint(settings):
            sys.exit("ayarlar.env, yarım kalan toplamanın ayarlarından farklı.\n"
                     f"  Önceki: {state.get('settings')}\n"
                     f"  Şimdiki: {settings_fingerprint(settings)}\n"
                     "  Aynı ayarlarla devam edin ya da yeni toplama için veri/ham ve "
                     "loglar/durum.json dosyalarını başka bir klasöre taşıyın.")
        if state.get("salt_fp") != salt_fingerprint(salt):
            sys.exit("gizli/salt.key, yarım kalan toplamada kullanılan anahtar değil. "
                     "Takma adlar tutarsız olacağı için durduruldu.")
        # Durum dosyası güncellenmeden önce yazılmış (yetim) parçaları sil
        for p in parts:
            first, _ = part_bounds(p)
            if first > state["last_msg_id"]:
                print(f"Yarım kalmış parça siliniyor: {p.name}")
                p.unlink()
        return state

    state = {
        "settings": settings_fingerprint(settings),
        "salt_fp": salt_fingerprint(salt),
        "last_msg_id": 0,
        "written": 0,
        "parts": 0,
        "started_utc": now_utc_iso(),
        "completed": False,
    }
    if parts:  # Durum dosyası kaybolmuş ama parçalar duruyor: parçalardan yeniden kur
        state["last_msg_id"] = max(part_bounds(p)[1] for p in parts)
        state["written"] = sum(pq.ParquetFile(p).metadata.num_rows for p in parts)
        state["parts"] = len(parts)
        print(f"Durum dosyası parçalardan yeniden kuruldu: {state['written']:,} mesaj.")
    save_state(state)
    return state


# ---------------------------------------------------------------------------
# Metin: URL, hashtag, maskeleme
# ---------------------------------------------------------------------------
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b(?:t\.me|telegram\.me)/\S+")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IBAN_RE = re.compile(r"(?i)\bTR\s?\d{2}(?:\s?\d{4}){5}\s?\d{2}\b")
ELEVEN_DIGITS_RE = re.compile(r"(?<!\d)\d{11}(?!\d)")
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?90[\s.-]?)?\(?0?\s?[2-5]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)")
MENTION_RE = re.compile(r"(?<![\w.@])@[A-Za-z0-9_]{4,32}")
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")


def is_valid_tc(s):
    if len(s) != 11 or not s.isdigit() or s[0] == "0":
        return False
    d = [int(c) for c in s]
    if (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def redact(text, mention_names=()):
    if not text:
        return text
    for name in mention_names:            # Kullanıcı adı olmayan kişilerin satır içi etiketleri
        if name and name.strip():
            text = text.replace(name, "<KULLANICI>")
    text = URL_RE.sub("<URL>", text)
    text = EMAIL_RE.sub("<EPOSTA>", text)
    text = IBAN_RE.sub("<IBAN>", text)
    text = ELEVEN_DIGITS_RE.sub(lambda m: "<TCNO>" if is_valid_tc(m.group()) else m.group(), text)
    text = PHONE_RE.sub("<TELEFON>", text)
    text = MENTION_RE.sub("<KULLANICI>", text)
    return text


def clean_url(u):
    return u.strip().rstrip(".,;:!?)]}\"'»")


# ---------------------------------------------------------------------------
# Mesaj -> satır
# ---------------------------------------------------------------------------
def peer_kind_and_id(peer):
    from telethon.tl.types import PeerChannel, PeerChat, PeerUser
    if peer is None:
        return None, None
    if isinstance(peer, PeerUser):
        return "user", peer.user_id
    if isinstance(peer, PeerChannel):
        return "channel", peer.channel_id
    if isinstance(peer, PeerChat):
        return "chat", peer.chat_id
    return type(peer).__name__, None


MEDIA_ATTRS = ("photo", "sticker", "gif", "voice", "video_note", "video", "audio", "poll",
               "web_preview", "contact", "geo", "venue", "dice", "game", "invoice", "document")


def detect_media(m):
    if getattr(m, "media", None) is None:
        return None, None
    for attr in MEDIA_ATTRS:
        try:
            if getattr(m, attr, None):
                mime = None
                f = getattr(m, "file", None)
                if attr in ("document", "audio", "video", "voice", "gif") and f is not None:
                    mime = getattr(f, "mime_type", None)
                return attr, mime
        except Exception:
            continue
    return type(m.media).__name__, None


def message_to_row(m, salt, group_id, store_raw):
    from telethon.tl.types import (MessageEntityHashtag, MessageEntityMention,
                                   MessageEntityMentionName, MessageEntityTextUrl,
                                   MessageEntityUrl)

    action = getattr(m, "action", None)
    is_service = action is not None
    text = (getattr(m, "message", None) or "") if not is_service else ""

    # Gönderici
    kind, raw_id = peer_kind_and_id(getattr(m, "from_id", None))
    if kind is None:
        sender_type = "no_from_id"
    elif kind == "channel" and raw_id == group_id:
        sender_type = "anon_admin"
    elif kind == "channel":
        sender_type = "channel"
    elif kind == "user":
        sender = getattr(m, "sender", None)
        sender_type = "bot" if getattr(sender, "bot", False) else "user"
    else:
        sender_type = kind
    sender_pid = make_pid(salt, kind, raw_id) if kind else make_pid(salt, "chat_self", group_id)

    # Yanıt
    r = getattr(m, "reply_to", None)
    reply_to_msg_id = getattr(r, "reply_to_msg_id", None) if r else None
    reply_to_top_id = getattr(r, "reply_to_top_id", None) if r else None
    reply_to_other_chat = bool(getattr(r, "reply_to_peer_id", None)) if r else False

    # Yönlendirme
    fwd = getattr(m, "fwd_from", None)
    fwd_kind, fwd_raw = peer_kind_and_id(getattr(fwd, "from_id", None)) if fwd else (None, None)
    if fwd and fwd_kind is None and getattr(fwd, "from_name", None):
        fwd_kind = "hidden_user"
    fwd_pid = make_pid(salt, fwd_kind, fwd_raw) if fwd_raw is not None else None

    # Bağlantılar, hashtag'ler, etiketler
    urls, hashtags, mention_names = [], [], []
    n_hidden, n_mentions = 0, 0
    if text and getattr(m, "entities", None):
        for ent, inner in m.get_entities_text():
            if isinstance(ent, MessageEntityUrl):
                urls.append(clean_url(inner))
            elif isinstance(ent, MessageEntityTextUrl):
                urls.append(clean_url(ent.url))
                if ent.url not in text:
                    n_hidden += 1
            elif isinstance(ent, MessageEntityHashtag):
                hashtags.append(inner)
            elif isinstance(ent, MessageEntityMention):
                n_mentions += 1
            elif isinstance(ent, MessageEntityMentionName):
                n_mentions += 1
                mention_names.append(inner)
    if text and not urls:
        urls = [clean_url(u) for u in URL_RE.findall(text)]
    if text and not hashtags:
        hashtags = HASHTAG_RE.findall(text)
    wp = getattr(m, "web_preview", None) if getattr(m, "media", None) else None
    wp_url = getattr(wp, "url", None) if wp else None
    if wp_url and clean_url(wp_url) not in urls:
        urls.append(clean_url(wp_url))
    urls = list(dict.fromkeys(u for u in urls if u))

    media_type, doc_mime = detect_media(m)
    reactions = getattr(m, "reactions", None)
    total_reactions = sum(rc.count for rc in (getattr(reactions, "results", None) or []))

    return {
        "msg_id": m.id,
        "date_utc": m.date,
        "edit_date_utc": getattr(m, "edit_date", None),
        "sender_type": sender_type,
        "sender_pid": sender_pid,
        "is_service": is_service,
        "action_type": type(action).__name__ if is_service else None,
        "reply_to_msg_id": reply_to_msg_id,
        "reply_to_top_id": reply_to_top_id,
        "reply_to_other_chat": reply_to_other_chat,
        "is_forward": fwd is not None,
        "fwd_from_type": fwd_kind,
        "fwd_from_pid": fwd_pid,
        "fwd_date_utc": getattr(fwd, "date", None) if fwd else None,
        "has_media": getattr(m, "media", None) is not None,
        "media_type": media_type,
        "doc_mime": doc_mime,
        "grouped_id": getattr(m, "grouped_id", None),
        "is_pinned": bool(getattr(m, "pinned", False)),
        "via_bot": getattr(m, "via_bot_id", None) is not None,
        "has_post_author": bool(getattr(m, "post_author", None)),
        "total_reactions": int(total_reactions),
        "n_urls": len(urls),
        "n_hidden_urls": n_hidden,
        "urls_json": json.dumps(urls, ensure_ascii=False),
        "hashtags_json": json.dumps(hashtags, ensure_ascii=False),
        "n_mentions": n_mentions,
        "text_len": len(text),
        "text_raw": text if store_raw else None,
        "text_redacted": redact(text, mention_names),
    }


# ---------------------------------------------------------------------------
# Parça yazıcı
# ---------------------------------------------------------------------------
class PartWriter:
    def __init__(self, state):
        self.state = state
        self.buf = []

    def add(self, row):
        self.buf.append(row)
        if len(self.buf) >= BATCH_SIZE:
            self.flush()

    def pending(self):
        return len(self.buf)

    def flush(self):
        if not self.buf:
            return
        first, last = self.buf[0]["msg_id"], self.buf[-1]["msg_id"]
        table = pa.Table.from_pylist(self.buf, schema=SCHEMA)
        final = RAW_DIR / f"parca_{first:010d}_{last:010d}.parquet"
        tmp = final.with_suffix(".tmp")
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, final)
        self.state["last_msg_id"] = last
        self.state["written"] += len(self.buf)
        self.state["parts"] += 1
        self.state["updated_utc"] = now_utc_iso()
        save_state(self.state)
        self.buf = []


# ---------------------------------------------------------------------------
# Toplama
# ---------------------------------------------------------------------------
def parse_group(value):
    v = value.strip()
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


async def save_meta(client, entity, salt):
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.types import ChannelParticipantsAdmins
    import telethon

    META_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "toplama_zamani_utc": now_utc_iso(),
        "grup_id": entity.id,
        "baslik": getattr(entity, "title", None),
        "kullanici_adi": getattr(entity, "username", None),
        "megagroup": getattr(entity, "megagroup", None),
        "forum": getattr(entity, "forum", None),
        "python": platform.python_version(),
        "telethon": telethon.__version__,
    }
    try:
        full = await client(GetFullChannelRequest(entity))
        meta["uye_sayisi"] = full.full_chat.participants_count
        meta["aciklama"] = full.full_chat.about
        meta["yavas_mod_saniye"] = getattr(full.full_chat, "slowmode_seconds", None)
    except Exception as e:
        meta["tam_bilgi_hatasi"] = f"{type(e).__name__}: {e}"
    try:
        admins = await client.get_participants(entity, filter=ChannelParticipantsAdmins)
        admin_pids = sorted(make_pid(salt, "user", a.id) for a in admins)
        meta["yonetici_sayisi"] = len(admin_pids)
        (META_DIR / "yonetici_pidleri.json").write_text(
            json.dumps(admin_pids, indent=2), encoding="utf-8")
    except Exception as e:
        meta["yonetici_listesi_hatasi"] = f"{type(e).__name__}: {e}"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (META_DIR / f"grup_meta_{stamp}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Grup: {meta['baslik']} | üye sayısı: {meta.get('uye_sayisi', 'alınamadı')}")


async def collect(settings, salt):
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError

    state = load_or_rebuild_state(settings, salt)
    start, end = settings["start"], settings["end"]

    client = TelegramClient(str(SESSION_FILE), settings["api_id"], settings["api_hash"])
    client.flood_sleep_threshold = 3600   # 1 saate kadar olan beklemeleri kendisi yapar

    async with client:
        try:
            entity = await client.get_entity(parse_group(settings["group"]))
        except Exception as e:
            sys.exit(f"Grup bulunamadı ({type(e).__name__}: {e}).\n"
                     "  Hesabınızın gruba üye olduğundan ve TG_GRUP değerinin doğru "
                     "olduğundan emin olun.")
        await save_meta(client, entity, salt)
        total = (await client.get_messages(entity, limit=0)).total
        print(f"Telegram'ın bildirdiği toplam mesaj: {total:,}")
        if state["written"]:
            print(f"Kaldığı yerden devam: {state['written']:,} mesaj yazılmış, "
                  f"son msg_id {state['last_msg_id']}")

        writer = PartWriter(state)
        errors = 0
        seen = 0
        while True:
            reached_end = False
            try:
                async for m in client.iter_messages(entity, reverse=True,
                                                    min_id=state["last_msg_id"], wait_time=1):
                    if end and m.date >= end:
                        reached_end = True
                        break
                    if start and m.date < start:
                        continue
                    writer.add(message_to_row(m, salt, entity.id, settings["store_raw"]))
                    seen += 1
                    if seen % PROGRESS_EVERY == 0:
                        done = state["written"] + writer.pending()
                        tr_date = m.date.astimezone(TR_TZ).strftime("%Y-%m-%d")
                        print(f"  {done:,} mesaj | son mesaj tarihi {tr_date}")
                writer.flush()
                break
            except FloodWaitError as e:
                writer.flush()
                print(f"Telegram {e.seconds} saniye beklenmesini istedi; bekleniyor...")
                await asyncio.sleep(e.seconds + 5)
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                writer.flush()
                errors += 1
                if errors > 10:
                    raise
                print(f"Bağlantı sorunu ({type(e).__name__}); 30 sn sonra tekrar "
                      f"denenecek ({errors}/10)")
                await asyncio.sleep(30)

        state["completed"] = True
        state["finished_utc"] = now_utc_iso()
        state["bitis_tarihine_ulasildi"] = reached_end
        save_state(state)
        print(f"Toplama tamamlandı: {state['written']:,} satır, {state['parts']} parça.")


# ---------------------------------------------------------------------------
# Özet (veri akış tablosunun başlangıcı)
# ---------------------------------------------------------------------------
def counts(series):
    return {str(k): int(v) for k, v in series.value_counts(dropna=False).sort_index().items()}


def summarize():
    import pandas as pd

    files = sorted(RAW_DIR.glob("parca_*.parquet"))
    if not files:
        print("Özet için veri bulunamadı.")
        return
    df = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    svc = df["is_service"]
    msgs = df[~svc]
    has_text = msgs["text_len"] > 0
    text_col = "text_raw" if msgs["text_raw"].notna().any() else "text_redacted"
    same_sender_dups = int(msgs[has_text].duplicated(subset=["sender_pid", text_col]).sum())
    tr_dates = msgs["date_utc"].dt.tz_convert(TR_TZ)
    cutoff = pd.Timestamp("2026-07-01", tz=TR_TZ)

    ozet = {
        "olusturma_zamani_utc": now_utc_iso(),
        "parca_sayisi": len(files),
        "toplam_satir": int(len(df)),
        "yinelenen_msg_id": int(df["msg_id"].duplicated().sum()),
        "servis_mesaji": int(svc.sum()),
        "servis_turleri": counts(df.loc[svc, "action_type"]),
        "servis_disi_mesaj": int(len(msgs)),
        "metin_iceren": int(has_text.sum()),
        "yalnizca_medya": int((msgs["has_media"] & ~has_text).sum()),
        "yonlendirilmis": int(msgs["is_forward"].sum()),
        "yanit_olan": int(msgs["reply_to_msg_id"].notna().sum()),
        "ayni_gondericiden_birebir_tekrar": same_sender_dups,
        "gonderici_turleri": counts(msgs["sender_type"]),
        "benzersiz_gonderici_kullanici_ve_bot": int(
            msgs.loc[msgs["sender_type"].isin(["user", "bot"]), "sender_pid"].nunique()),
        "url_toplam": int(msgs["n_urls"].sum()),
        "gizli_url_toplam": int(msgs["n_hidden_urls"].sum()),
        "hashtag_iceren_mesaj": int((msgs["hashtags_json"] != "[]").sum()),
        "medya_turleri": counts(msgs.loc[msgs["has_media"], "media_type"]),
        "ilk_mesaj_tr": str(tr_dates.min()),
        "son_mesaj_tr": str(tr_dates.max()),
        "yillara_gore_servis_disi": counts(tr_dates.dt.year),
        "ilk_toplamayla_karsilastirma_2026_07_01_oncesi": {
            "tum_satirlar": int((df["date_utc"].dt.tz_convert(TR_TZ) < cutoff).sum()),
            "servis_disi": int((tr_dates < cutoff).sum()),
        },
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = LOG_DIR / "toplama_ozeti.json"
    out.write_text(json.dumps(ozet, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== VERİ AKIŞI ÖZETİ ===")
    for key in ("toplam_satir", "servis_mesaji", "servis_disi_mesaj", "metin_iceren",
                "yalnizca_medya", "ayni_gondericiden_birebir_tekrar", "yonlendirilmis",
                "yanit_olan", "benzersiz_gonderici_kullanici_ve_bot", "url_toplam",
                "gizli_url_toplam", "hashtag_iceren_mesaj", "yinelenen_msg_id"):
        print(f"  {key:40s} {ozet[key]:>10,}")
    print(f"  {'gonderici_turleri':40s} {ozet['gonderici_turleri']}")
    print(f"  {'tarih aralığı (TR)':40s} {ozet['ilk_mesaj_tr']} -> {ozet['son_mesaj_tr']}")
    print(f"  2026-07-01 öncesi: {ozet['ilk_toplamayla_karsilastirma_2026_07_01_oncesi']}")
    print(f"Ayrıntılar: {out.relative_to(BASE)}")


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Telegram grubundan araştırma verisi toplama")
    parser.add_argument("--ozet", action="store_true", help="yalnızca özeti yeniden üret")
    args = parser.parse_args()

    if args.ozet:
        summarize()
        return
    settings = load_settings()
    salt = load_or_create_salt()
    try:
        asyncio.run(collect(settings, salt))
    except KeyboardInterrupt:
        print("\nDurduruldu. Aynı komutu yeniden çalıştırınca son yazılan parçadan devam eder.")
        return
    summarize()


if __name__ == "__main__":
    main()
