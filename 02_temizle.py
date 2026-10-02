#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_temizle.py - Ham Telegram verisini analiz tablolarına dönüştürür.

Ne yapar?
  * veri/ham/ altındaki parçaları birleştirir.
  * Hiçbir satırı SİLMEZ; servis mesajı, bot, yalnızca medya, mükerrer gibi
    durumları bayrak sütunlarıyla işaretler. Böylece hangi analizin hangi
    alt kümeyle yapıldığı her zaman izlenebilir.
  * Metnin analiz sürümlerini üretir (gömme modelleri için doğal metin,
    frekans analizleri için sadeleştirilmiş metin).
  * mesajlar / kullanicilar / baglantilar / yanitlar tablolarını yazar.
  * Makaleye girecek veri akış tablosunu ve aylık-yıllık betimsel tabloları üretir.
  * İnsan kodlaması için yıllara göre tabakalı örneklemi Excel olarak hazırlar.
  * Tüm parametreleri ve sayıları loglar/ altına kaydeder; yöntem bölümünün
    taslağını loglar/yontem_notlari.md dosyasına yazar.

Kullanım:
  python 02_temizle.py
"""

import json
import platform
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# AYARLAR - değiştirdiğinde loglar/isleme_gunlugu.json dosyasına da yansır
# ---------------------------------------------------------------------------
TOHUM = 20260917          # örneklem rastgeleliği (değiştirme, tekrarlanabilirlik için)
ORNEK_A = 600             # tüm korpustan tabakalı rastgele örneklem (yaygınlık tahmini)
ORNEK_B = 300             # olası talep mesajlarından ek örneklem (yalnızca model eğitimi)
LEMATIZE = False          # True yaparsan zeyrek ile lemmatizasyon dener (yavaş, belirsiz)
KARSILASTIRMA_TARIHI = "2026-07-01"   # eski veri setiyle karşılaştırma kesiti (TR saati)
MIN_TOKEN_UZUNLUK = 2

BASE = Path(__file__).resolve().parent
RAW_DIR = BASE / "veri" / "ham"
OUT_DIR = BASE / "veri" / "islenmis"
META_DIR = BASE / "veri" / "meta"
CODE_DIR = BASE / "veri" / "kodlama"
LOG_DIR = BASE / "loglar"
TR_TZ = timezone(timedelta(hours=3), "Europe/Istanbul")

# Türkçe durak kelimeler. Alan terimleri (sosyal, hizmet, atama, kpss...) BİLEREK
# listede yok: hangi kelimenin baskın olduğu bulgunun kendisi.
DURAK_KELIMELER = set("""
acaba ama amma anca ancak artık aslında ayrıca az bana bazen bazı belki ben benden beni benim beri
bile bilhassa bir biraz birçoğu birçok biri birisi birkaç birşey biz bizden bize bizi bizim böyle
böylece bu buna bunda bundan bunlar bunları bunların bunu bunun burada bütün çoğu çoğunu çok çünkü
da daha dahi de defa değil diğer diye dolayı dolayısıyla edecek eden ederek edilecek ediliyor edilmesi
ediyor eğer elbette en etmesi etti ettiği ettiğini fakat falan filan gibi göre halbuki hangi hani
hatta hem henüz hep hepsi her herhangi herkes herkese herkesi hiç hiçbir için içinde iki ile ilgili
ise işte itibaren itibariyle kadar karşın kendi kendilerine kendini kendisi kendisine kendisini kez
ki kim kime kimi kimse kısaca lakin madem mi mu mü mı nasıl ne neden nedenle nerde nerede nereye
niçin niye o olan olarak oldu olduğu olduğunu olduklarını olmadı olmadığı olmak olması olmayan olmaz
olsa olsun olup olur olursa oluyor ona onda ondan onlar onlara onlardan onları onların onu onun orada
oysa öyle öz pek rağmen sadece sanki se şayet şekilde şey şeyden şeyi şeyler şöyle şu şuna şunda
şundan şunları şunu şunun tabii tarafından trilyon tüm üzere var vardı ve veya ya yani yapacak
yapılan yapılması yapıyor yapmak yaptı yaptığı yaptığını yaptıkları yerine yine yoksa zaten zira
""".split())

# Talep olasılığı yüksek mesajları örneklemde çoğaltmak için kullanılan SEZGİSEL kalıplar.
# Bu bir bulgu değildir; yalnızca B örneklemini seçmeye yarar.
TALEP_KALIPLARI = [
    r"\?", r"\bvar\s*mı\b", r"\bbilen\s+var\b", r"\bbilgisi\s+olan\b", r"\byard[ıi]mc[ıi]\s+ol",
    r"\bnas[ıi]l\b", r"\bne\s+zaman\b", r"\bnereden\b", r"\bkim\s+bilir\b", r"\bacaba\b",
    r"\brica\s+ed", r"\bsormak\s+istiyorum\b", r"\bbilgi\s+alabilir\b", r"\bmi\?|\bmu\?|\bmü\?|\bmı\?",
]
TALEP_RE = re.compile("|".join(TALEP_KALIPLARI), re.IGNORECASE)

PLACEHOLDER_RE = re.compile(r"<(URL|EPOSTA|TELEFON|TCNO|IBAN|KULLANICI)>")
NON_LETTER_RE = re.compile(r"[^a-zçğıöşü\s]")
WS_RE = re.compile(r"\s+")
KISALTICILAR = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "cutt.ly", "is.gd", "shorturl.at", "rb.gy"}

ISLEV_KODLARI = [
    "bilgi talebi", "duyuru/bilgi paylaşımı", "yanıt/bilgi verme", "yardım teklifi",
    "mobilizasyon çağrısı", "duygusal ifade/destek", "eleştiri/şikâyet", "sohbet/nezaket", "diğer",
]
KONU_KODLARI = [
    "istihdam/atama/alım", "sınav ve tercih", "eğitim ve akademik", "mesleki uygulama/vaka",
    "mevzuat/özlük/unvan", "toplumsal olay/afet", "grup içi işleyiş", "diğer",
]


# ---------------------------------------------------------------------------
# Metin işlemleri
# ---------------------------------------------------------------------------
def tr_lower(s):
    """Türkçe uyumlu küçük harfe çevirme (I->ı, İ->i)."""
    return s.replace("I", "ı").replace("İ", "i").lower()


def sadelesir(text):
    """Frekans analizleri için: maskeler kaldırılır, harf dışı her şey atılır."""
    if not text:
        return ""
    t = PLACEHOLDER_RE.sub(" ", text)
    t = tr_lower(t)
    t = NON_LETTER_RE.sub(" ", t)
    return WS_RE.sub(" ", t).strip()


def tokenize(text):
    return [w for w in text.split() if len(w) >= MIN_TOKEN_UZUNLUK]


def lemmatizer_kur():
    """İsteğe bağlı zeyrek lemmatizasyonu. Belirsizlikte: token ile en uzun ortak
    ön eki paylaşan aday seçilir, eşitlikte en uzun aday alınır."""
    import logging
    import zeyrek
    logging.getLogger("zeyrek").setLevel(logging.CRITICAL)
    analiz = zeyrek.MorphAnalyzer()
    cache = {}

    def ortak_onek(a, b):
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    def lemma(token):
        if token in cache:
            return cache[token]
        try:
            sonuc = analiz.lemmatize(token)
            adaylar = [tr_lower(c) for c in (sonuc[0][1] if sonuc else []) if c]
        except Exception:
            adaylar = []
        secim = max(adaylar, key=lambda c: (ortak_onek(c, token), len(c))) if adaylar else token
        cache[token] = secim
        return secim

    return lemma, cache


# ---------------------------------------------------------------------------
# Yükleme
# ---------------------------------------------------------------------------
def ham_yukle():
    parcalar = sorted(RAW_DIR.glob("parca_*.parquet"))
    if not parcalar:
        sys.exit("veri/ham altında parça bulunamadı. Önce 01_veri_cek.py çalıştırın.")
    df = pd.concat((pd.read_parquet(p) for p in parcalar), ignore_index=True)
    df = df.sort_values("msg_id").reset_index(drop=True)
    if df["msg_id"].duplicated().any():
        sys.exit("Aynı msg_id birden fazla kez var. veri/ham klasörünü kontrol edin.")
    print(f"{len(df):,} satır okundu ({len(parcalar)} parça).")
    return df


def yonetici_pidleri():
    p = META_DIR / "yonetici_pidleri.json"
    return set(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else set()


# ---------------------------------------------------------------------------
# Bayraklar ve metin sürümleri
# ---------------------------------------------------------------------------
def zenginlestir(df, adminler):
    df["date_tr"] = df["date_utc"].dt.tz_convert(TR_TZ)
    df["yil"] = df["date_tr"].dt.year
    df["ay"] = df["date_tr"].dt.strftime("%Y-%m")
    df["hafta"] = df["date_tr"].dt.strftime("%G-W%V")
    df["saat"] = df["date_tr"].dt.hour
    df["haftagunu"] = df["date_tr"].dt.dayofweek

    df["is_bot"] = df["sender_type"].eq("bot")
    df["is_anon_admin"] = df["sender_type"].eq("anon_admin")
    df["is_admin_now"] = df["sender_pid"].isin(adminler)   # yalnızca GÜNCEL yöneticiler
    df["has_text"] = df["text_len"] > 0
    df["is_media_only"] = df["has_media"] & ~df["has_text"]

    kaynak = df["text_raw"].where(df["text_raw"].notna(), df["text_redacted"]).fillna("")
    df["text_light"] = df["text_redacted"].fillna("").map(lambda s: WS_RE.sub(" ", s).strip())
    df["_dup_key"] = kaynak.map(lambda s: WS_RE.sub(" ", tr_lower(s)).strip())

    # Aynı göndericinin birebir tekrarı (ilk gönderim tekrar sayılmaz)
    df["is_dup_same_sender"] = (
        df["has_text"] & df.duplicated(subset=["sender_pid", "_dup_key"], keep="first"))

    # Farklı göndericilerin paylaştığı özdeş metinler (kampanya/kopyala-yapıştır sinyali)
    metinli = df[df["has_text"]]
    grup = metinli.groupby("_dup_key")["sender_pid"].agg(["nunique", "size"])
    ortak = grup[(grup["nunique"] >= 2)]
    kod = {k: i + 1 for i, k in enumerate(ortak.index)}
    df["ortak_metin_id"] = df["_dup_key"].map(kod).astype("Int64")
    df["ortak_metin_gonderici"] = df["_dup_key"].map(ortak["nunique"]).astype("Int64")
    df["ortak_metin_mesaj"] = df["_dup_key"].map(ortak["size"]).astype("Int64")

    df["n_char"] = df["text_light"].str.len()
    df["n_token"] = df["text_light"].str.split().map(len)
    df["olasi_talep"] = df["has_text"] & df["text_light"].str.contains(TALEP_RE, na=False)

    # Analiz kümeleri (silme yok, tanım var)
    df["metin_analizi"] = ~df["is_service"] & df["has_text"] & ~df["is_bot"]
    df["katilim_analizi"] = ~df["is_service"] & df["sender_type"].eq("user")
    kesit = pd.Timestamp(KARSILASTIRMA_TARIHI, tz=TR_TZ)
    df["kesit_oncesi"] = df["date_tr"] < kesit
    return df.drop(columns=["_dup_key"])


def metin_surumleri(df):
    print("Metin sürümleri hazırlanıyor...")
    df["text_norm"] = df["text_light"].map(sadelesir)
    tokenlar = df["text_norm"].map(tokenize)

    lemma_bilgi = {"uygulandi": False}
    if LEMATIZE:
        try:
            lemma_fn, cache = lemmatizer_kur()
            sozluk = {t for toks in tokenlar for t in toks}
            print(f"  {len(sozluk):,} benzersiz token lemmatize ediliyor (uzun sürebilir)...")
            for t in sozluk:
                lemma_fn(t)
            tokenlar = tokenlar.map(lambda ts: [cache.get(t, t) for t in ts])
            (META_DIR / "lemma_sozlugu.json").write_text(
                json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
            lemma_bilgi = {"uygulandi": True, "sozluk_boyutu": len(cache),
                           "kural": "en uzun ortak ön ek, eşitlikte en uzun aday"}
        except Exception as e:
            print(f"  Lemmatizasyon atlandı ({type(e).__name__}: {e}).")

    df["tokens_json"] = tokenlar.map(lambda ts: json.dumps(ts, ensure_ascii=False))
    icerik = tokenlar.map(lambda ts: [t for t in ts if t not in DURAK_KELIMELER])
    df["tokens_icerik_json"] = icerik.map(lambda ts: json.dumps(ts, ensure_ascii=False))
    df["n_token_icerik"] = icerik.map(len)
    return df, lemma_bilgi


# ---------------------------------------------------------------------------
# Türev tablolar
# ---------------------------------------------------------------------------
def baglanti_tablosu(df):
    try:
        import tldextract
        ex = tldextract.TLDExtract(suffix_list_urls=())   # çevrimdışı liste
    except Exception:
        ex = None

    kayitlar = []
    for msg_id, yil, urls in zip(df["msg_id"], df["yil"], df["urls_json"]):
        for u in json.loads(urls or "[]"):
            tam = u if "://" in u else "https://" + u
            kalan = tam.split("://", 1)[1]
            host = re.sub(r"^www\.", "", kalan.split("/")[0].split("?")[0].lower())
            yol = "/" + kalan.split("/", 1)[1] if "/" in kalan else "/"
            domain = host
            if ex is not None and host:
                r = ex(tam)
                domain = (getattr(r, "top_domain_under_public_suffix", None)
                          or r.registered_domain or host)
            # docs.google.com/forms ile /spreadsheets aynı şey değil: yol türünü ayrı tut
            yol_turu = yol.split("/")[1] if host.endswith("google.com") and len(yol) > 1 else ""
            kayitlar.append({"msg_id": msg_id, "yil": yil, "url": u, "host": host,
                             "domain": domain, "yol": yol[:120], "yol_turu": yol_turu,
                             "kisaltici": host in KISALTICILAR})
    return pd.DataFrame(kayitlar)


def yanit_tablosu(df):
    ana = df.set_index("msg_id")[["date_tr", "sender_pid", "metin_analizi"]]
    y = df[df["reply_to_msg_id"].notna()][
        ["msg_id", "date_tr", "sender_pid", "reply_to_msg_id"]].copy()
    y["reply_to_msg_id"] = y["reply_to_msg_id"].astype("int64")
    y = y.join(ana.add_prefix("ana_"), on="reply_to_msg_id")
    y["ana_mesaj_var"] = y["ana_date_tr"].notna()
    y["yanit_suresi_sn"] = (y["date_tr"] - y["ana_date_tr"]).dt.total_seconds()
    y["kendine_yanit"] = y["sender_pid"] == y["ana_sender_pid"]
    return y.rename(columns={"msg_id": "yanit_msg_id", "reply_to_msg_id": "ana_msg_id"})


def kullanici_tablosu(df, yanitlar, adminler):
    k = df[df["katilim_analizi"]]
    g = k.groupby("sender_pid")
    tbl = pd.DataFrame({
        "ilk_mesaj": g["date_tr"].min(),
        "son_mesaj": g["date_tr"].max(),
        "mesaj_sayisi": g.size(),
        "aktif_ay_sayisi": g["ay"].nunique(),
        "metinli_mesaj": g["has_text"].sum(),
        "url_iceren": (g["n_urls"].apply(lambda s: (s > 0).sum())),
        "olasi_talep": g["olasi_talep"].sum(),
        "ortalama_token": g["n_token"].mean().round(2),
    })
    verilen = yanitlar.groupby("sender_pid").size().rename("verdigi_yanit")
    alinan = yanitlar[yanitlar["ana_mesaj_var"]].groupby("ana_sender_pid").size().rename("aldigi_yanit")
    tbl = tbl.join(verilen).join(alinan).fillna({"verdigi_yanit": 0, "aldigi_yanit": 0})
    tbl["kohort_yil"] = tbl["ilk_mesaj"].dt.year
    tbl["omur_gun"] = (tbl["son_mesaj"] - tbl["ilk_mesaj"]).dt.days
    tbl["guncel_yonetici"] = tbl.index.isin(adminler)
    return tbl.reset_index()


# ---------------------------------------------------------------------------
# Veri akışı ve betimsel tablolar
# ---------------------------------------------------------------------------
def veri_akisi(df):
    def sayim(d):
        metinli_analiz = d["metin_analizi"]
        return {
            "1. Ham satır": len(d),
            "2. Servis mesajı (katıldı/ayrıldı vb.)": int(d["is_service"].sum()),
            "3. Bot mesajı": int((~d["is_service"] & d["is_bot"]).sum()),
            "4. Metinsiz (yalnızca medya veya boş)": int((~d["is_service"] & ~d["is_bot"] & ~d["has_text"]).sum()),
            "5. Metin analizi kümesi": int(metinli_analiz.sum()),
            "6.   bunun içinde aynı göndericiden birebir tekrar": int((metinli_analiz & d["is_dup_same_sender"]).sum()),
            "7. Tekrarsız metin kümesi": int((metinli_analiz & ~d["is_dup_same_sender"]).sum()),
            "8. Katılım analizi kümesi (bireysel hesaplar)": int(d["katilim_analizi"].sum()),
        }
    tum = sayim(df)
    kesit = sayim(df[df["kesit_oncesi"]])
    tbl = pd.DataFrame({"Adım": list(tum), "Tüm dönem": list(tum.values()),
                        f"{KARSILASTIRMA_TARIHI} öncesi": [kesit[k] for k in tum]})
    return tbl


def betimsel_tablolar(df, baglantilar, yanitlar):
    m = df[df["metin_analizi"]]
    yillik = pd.DataFrame({
        "mesaj": m.groupby("yil").size(),
        "benzersiz_gonderici": df[df["katilim_analizi"]].groupby("yil")["sender_pid"].nunique(),
        "yanit_orani": (m.groupby("yil")["reply_to_msg_id"].apply(lambda s: s.notna().mean() * 100)).round(1),
        "url_iceren_oran": (m.groupby("yil")["n_urls"].apply(lambda s: (s > 0).mean() * 100)).round(1),
        "olasi_talep_oran": (m.groupby("yil")["olasi_talep"].mean() * 100).round(1),
        "ortalama_token": m.groupby("yil")["n_token"].mean().round(1),
    }).reset_index()
    aylik = m.groupby("ay").agg(mesaj=("msg_id", "size"),
                                gonderici=("sender_pid", "nunique")).reset_index()
    alanlar = (baglantilar.groupby("domain").agg(url=("url", "size"),
                                                 mesaj=("msg_id", "nunique"))
               .sort_values("url", ascending=False).reset_index())
    return yillik, aylik, alanlar


# ---------------------------------------------------------------------------
# Kodlama örneklemi
# ---------------------------------------------------------------------------
def tabakali_ornek(kaynak, n, tohum):
    if len(kaynak) <= n:
        return kaynak.copy()
    paylar = (kaynak.groupby("yil").size() / len(kaynak) * n).round().astype(int)
    parcalar = []
    for yil, adet in paylar.items():
        havuz = kaynak[kaynak["yil"] == yil]
        parcalar.append(havuz.sample(min(adet, len(havuz)), random_state=tohum))
    ornek = pd.concat(parcalar)
    if len(ornek) > n:
        ornek = ornek.sample(n, random_state=tohum)
    return ornek


def kodlama_dosyasi(df):
    havuz = df[df["metin_analizi"] & (df["n_token"] >= 1)]
    a = tabakali_ornek(havuz, ORNEK_A, TOHUM)
    b_havuz = havuz[havuz["olasi_talep"] & ~havuz["msg_id"].isin(a["msg_id"])]
    b = tabakali_ornek(b_havuz, ORNEK_B, TOHUM + 1)
    a = a.assign(ornek_tipi="A (rastgele)")
    b = b.assign(ornek_tipi="B (olası talep)")
    ornek = pd.concat([a, b]).sample(frac=1, random_state=TOHUM).reset_index(drop=True)

    sayfa = pd.DataFrame({
        "msg_id": ornek["msg_id"],
        "tarih": ornek["date_tr"].dt.strftime("%Y-%m-%d %H:%M"),
        "yil": ornek["yil"],
        "ornek_tipi": ornek["ornek_tipi"],
        "metin": ornek["text_light"],
        "islev_1": "", "islev_2": "", "konu": "", "emin_degilim": "", "not": "",
    })

    CODE_DIR.mkdir(parents=True, exist_ok=True)
    kod_kitabi = pd.DataFrame({
        "alan": (["islev_1 / islev_2"] * len(ISLEV_KODLARI)) + (["konu"] * len(KONU_KODLARI)),
        "kod": ISLEV_KODLARI + KONU_KODLARI,
        "aciklama": [""] * (len(ISLEV_KODLARI) + len(KONU_KODLARI)),
    })
    yollar = []
    for kodlayici in (1, 2):
        yol = CODE_DIR / f"kodlama_ornegi_kodlayici{kodlayici}.xlsx"
        with pd.ExcelWriter(yol, engine="openpyxl") as w:
            sayfa.to_excel(w, sheet_name="kodlama", index=False)
            kod_kitabi.to_excel(w, sheet_name="kod_kitabi", index=False)
            n = max(len(ISLEV_KODLARI), len(KONU_KODLARI))
            pd.DataFrame({
                "islev": ISLEV_KODLARI + [""] * (n - len(ISLEV_KODLARI)),
                "": [""] * n,
                "konu": KONU_KODLARI + [""] * (n - len(KONU_KODLARI)),
            }).to_excel(w, sheet_name="listeler", index=False)
            _dropdown_ekle(w.book, len(sayfa))
        yollar.append(yol)
    return ornek, sayfa, yollar


def _dropdown_ekle(book, n_satir):
    from openpyxl.worksheet.datavalidation import DataValidation
    ws = book["kodlama"]
    islev = DataValidation(type="list", formula1=f"=listeler!$A$2:$A${len(ISLEV_KODLARI) + 1}",
                           allow_blank=True)
    konu = DataValidation(type="list", formula1=f"=listeler!$C$2:$C${len(KONU_KODLARI) + 1}",
                          allow_blank=True)
    ws.add_data_validation(islev)
    ws.add_data_validation(konu)
    islev.add(f"F2:G{n_satir + 1}")
    konu.add(f"H2:H{n_satir + 1}")
    ws.column_dimensions["E"].width = 90
    for col in ("F", "G", "H"):
        ws.column_dimensions[col].width = 24


# ---------------------------------------------------------------------------
# Normalizasyon adayları (şeffaf kök eşleme için)
# ---------------------------------------------------------------------------
def normalizasyon_adaylari(df, ust=400):
    sayac = Counter()
    for js in df.loc[df["metin_analizi"], "tokens_icerik_json"]:
        sayac.update(set(json.loads(js)))          # belge frekansı
    en_sik = sayac.most_common(ust)
    satirlar = []
    for kelime, n in en_sik:
        kok = kelime[:5]
        akraba = sorted({k for k, _ in en_sik if k.startswith(kok) and k != kelime})[:6]
        satirlar.append({"kelime": kelime, "belge_frekansi": n,
                         "olasi_varyantlar": ", ".join(akraba), "birlestir_su_kelimeye": ""})
    return pd.DataFrame(satirlar)


# ---------------------------------------------------------------------------
def main():
    for d in (OUT_DIR, META_DIR, CODE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    baslangic = datetime.now(timezone.utc)
    adminler = yonetici_pidleri()
    df = ham_yukle()
    df = zenginlestir(df, adminler)
    df, lemma_bilgi = metin_surumleri(df)

    print("Türev tablolar oluşturuluyor...")
    baglantilar = baglanti_tablosu(df)
    yanitlar = yanit_tablosu(df)
    kullanicilar = kullanici_tablosu(df, yanitlar, adminler)

    sutunlar = [c for c in df.columns if c not in ("text_raw",)]
    df[sutunlar].to_parquet(OUT_DIR / "mesajlar.parquet", index=False)
    df[["msg_id", "text_raw"]].to_parquet(OUT_DIR / "mesajlar_ham_metin.parquet", index=False)
    kullanicilar.to_parquet(OUT_DIR / "kullanicilar.parquet", index=False)
    baglantilar.to_parquet(OUT_DIR / "baglantilar.parquet", index=False)
    yanitlar.to_parquet(OUT_DIR / "yanitlar.parquet", index=False)

    akis = veri_akisi(df)
    yillik, aylik, alanlar = betimsel_tablolar(df, baglantilar, yanitlar)
    akis.to_csv(LOG_DIR / "veri_akisi.csv", index=False, encoding="utf-8-sig")
    yillik.to_csv(LOG_DIR / "yillik_ozet.csv", index=False, encoding="utf-8-sig")
    aylik.to_csv(LOG_DIR / "aylik_hacim.csv", index=False, encoding="utf-8-sig")
    alanlar.to_csv(META_DIR / "alan_adi_taslak.csv", index=False, encoding="utf-8-sig")
    normalizasyon_adaylari(df).to_csv(META_DIR / "normalizasyon_adaylari.csv",
                                      index=False, encoding="utf-8-sig")

    ornek, sayfa, kodlama_yollari = kodlama_dosyasi(df)
    ornek[["msg_id", "ornek_tipi", "yil"]].to_csv(
        CODE_DIR / "ornek_kayit.csv", index=False, encoding="utf-8-sig")

    m = df[df["metin_analizi"]]
    gunluk = {
        "calisma_zamani_utc": baslangic.isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "parametreler": {"tohum": TOHUM, "ornek_A": ORNEK_A, "ornek_B": ORNEK_B,
                         "lematizasyon": lemma_bilgi, "min_token_uzunluk": MIN_TOKEN_UZUNLUK,
                         "durak_kelime_sayisi": len(DURAK_KELIMELER),
                         "karsilastirma_tarihi": KARSILASTIRMA_TARIHI},
        "kume_tanimlari": {
            "metin_analizi": "servis değil ve metin içeriyor ve bot değil",
            "katilim_analizi": "servis değil ve gönderici türü 'user'",
        },
        "talep_kaliplari": TALEP_KALIPLARI,
        "veri_akisi": akis.to_dict(orient="records"),
        "yanit": {
            "yanit_olan_mesaj": int(df["reply_to_msg_id"].notna().sum()),
            "ana_mesaji_veride_olan": int(yanitlar["ana_mesaj_var"].sum()),
            "medyan_yanit_suresi_dk": round(float(
                yanitlar.loc[yanitlar["ana_mesaj_var"], "yanit_suresi_sn"].median() / 60), 1),
        },
        "ortak_metin": {
            "grup_sayisi": int(df["ortak_metin_id"].nunique()),
            "en_cok_paylasilan_metin_gonderici_sayisi": int(df["ortak_metin_gonderici"].max() or 0),
        },
        "baglanti": {"toplam": int(len(baglantilar)),
                     "benzersiz_alan_adi": int(baglantilar["domain"].nunique()),
                     "kisaltici_link": int(baglantilar["kisaltici"].sum())},
        "ornekleme": {"A": int((ornek["ornek_tipi"] == "A (rastgele)").sum()),
                      "B": int((ornek["ornek_tipi"] == "B (olası talep)").sum()),
                      "yillara_gore": {str(k): int(v) for k, v in
                                       ornek.groupby("yil").size().items()}},
        "sure_sn": round((datetime.now(timezone.utc) - baslangic).total_seconds(), 1),
    }
    (LOG_DIR / "isleme_gunlugu.json").write_text(
        json.dumps(gunluk, ensure_ascii=False, indent=2), encoding="utf-8")

    yontem_notlari(gunluk, akis, df, m, yanitlar, baglantilar, kullanicilar)

    print("\n=== VERİ AKIŞI ===")
    print(akis.to_string(index=False))
    print(f"\nYazılan dosyalar: {OUT_DIR.relative_to(BASE)}, {LOG_DIR.relative_to(BASE)}, "
          f"{META_DIR.relative_to(BASE)}, {CODE_DIR.relative_to(BASE)}")
    print("Kodlama dosyaları: " + ", ".join(p.name for p in kodlama_yollari))


def markdown_tablo(tbl):
    try:
        return tbl.to_markdown(index=False)
    except Exception:          # tabulate kurulu değilse
        return "```\n" + tbl.to_string(index=False) + "\n```"


def yontem_notlari(gunluk, akis, df, m, yanitlar, baglantilar, kullanicilar):
    """Yöntem bölümünün taslağı - sayılar otomatik, yorum sana ait."""
    meta_dosyalari = sorted(META_DIR.glob("grup_meta_*.json"))
    grup = json.loads(meta_dosyalari[-1].read_text(encoding="utf-8")) if meta_dosyalari else {}
    ilk, son = df["date_tr"].min(), df["date_tr"].max()
    satirlar = [
        "# Yöntem notları (otomatik üretildi)",
        "",
        f"Üretim zamanı: {gunluk['calisma_zamani_utc']} (UTC)",
        "",
        "## Veri toplama",
        f"- Veri, Telethon {grup.get('telethon', '?')} kütüphanesiyle Telegram API üzerinden "
        f"{grup.get('toplama_zamani_utc', '?')} tarihinde çekilmiştir.",
        f"- Grup toplama anında {grup.get('uye_sayisi', '?')} üyeye sahiptir; "
        f"veri {ilk:%d.%m.%Y} - {son:%d.%m.%Y} tarihlerini kapsamaktadır.",
        "- Mesaj metni, tarihi, gönderici kimliği, yanıt ilişkisi, yönlendirme bilgisi, medya "
        "türü, bağlantılar, hashtag'ler ve tepki sayıları kaydedilmiş; medya dosyaları "
        "indirilmemiştir.",
        "- Gönderici kimlikleri toplama anında HMAC-SHA256 ile takma adlandırılmış, gerçek "
        "kimlikler hiçbir dosyaya yazılmamıştır. Metin içindeki telefon numarası, e-posta, "
        "TC kimlik numarası, IBAN ve kullanıcı adları örüntü temelli maskelenmiştir.",
        "",
        "## Veri akışı",
        markdown_tablo(akis),
        "",
        "## Ön işleme",
        f"- Analizlerde iki küme tanımlanmıştır: metin analizi kümesi ({gunluk['kume_tanimlari']['metin_analizi']}) "
        f"ve katılım analizi kümesi ({gunluk['kume_tanimlari']['katilim_analizi']}).",
        "- Metnin iki sürümü üretilmiştir: (a) dil modelleri için kişisel bilgileri maskelenmiş "
        "doğal metin, (b) frekans analizleri için küçük harfe çevrilmiş, noktalama ve rakamlardan "
        f"arındırılmış sadeleştirilmiş metin. Frekans analizlerinde {gunluk['parametreler']['durak_kelime_sayisi']} "
        "kelimelik Türkçe durak kelime listesi kullanılmış; alan terimleri listeye dahil "
        "edilmemiştir.",
        f"- Lemmatizasyon: {'uygulandı' if gunluk['parametreler']['lematizasyon'].get('uygulandi') else 'uygulanmadı; yüzey biçimleri korunmuştur'}.",
        "",
        "## Betimsel sayılar",
        f"- Metin analizi kümesi: {len(m):,} mesaj, {m['sender_pid'].nunique():,} gönderici.",
        f"- Mesaj başına ortalama {m['n_token'].mean():.1f} kelime (medyan {m['n_token'].median():.0f}).",
        f"- Yanıt ilişkisi taşıyan mesaj: {gunluk['yanit']['yanit_olan_mesaj']:,} "
        f"(metin analizi kümesinin %{100 * m['reply_to_msg_id'].notna().mean():.1f}'i); "
        f"medyan ilk yanıt süresi {gunluk['yanit']['medyan_yanit_suresi_dk']} dakika.",
        f"- Bağlantı: {len(baglantilar):,} URL, {baglantilar['domain'].nunique():,} benzersiz alan adı.",
        f"- Farklı göndericilerce paylaşılan özdeş metin grubu: {gunluk['ortak_metin']['grup_sayisi']:,}.",
        f"- Katılım: {len(kullanicilar):,} bireysel gönderici; en aktif %1 "
        f"({max(1, len(kullanicilar) // 100)} kişi) mesajların "
        f"%{100 * kullanicilar['mesaj_sayisi'].nlargest(max(1, len(kullanicilar) // 100)).sum() / kullanicilar['mesaj_sayisi'].sum():.1f}'ini üretmiştir.",
        "",
        "## Kodlama örneklemi",
        f"- Yıllara göre tabakalı {gunluk['ornekleme']['A']} rastgele mesaj (A örneklemi) "
        f"yaygınlık tahmini için; {gunluk['ornekleme']['B']} olası talep mesajı (B örneklemi) "
        "yalnızca model eğitimi için seçilmiştir.",
        f"- Rastgelelik tohumu: {gunluk['parametreler']['tohum']}.",
        "- B örneklemi sezgisel kalıplarla seçilmiştir; bu kalıplar bir bulgu değil, örnekleme "
        "aracıdır ve yaygınlık tahminlerine dahil edilmemiştir.",
    ]
    (LOG_DIR / "yontem_notlari.md").write_text("\n".join(satirlar), encoding="utf-8")


if __name__ == "__main__":
    main()
