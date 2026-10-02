#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_savunuculuk.py - Savunuculuk/mobilizasyon repertuvarını ölçer.

Üç ayrı sinyali ayrı ayrı sayar, sonra birleştirir:
  1. Hashtag kullanımı (kampanya etiketleri)
  2. Farklı kişilerce paylaşılan şablon metinler (dilekçe, mektup, çağrı)
  3. Sözlük temelli mobilizasyon terimleri (dilekçe, imza, kampanya, etkinlik...)
  4. Dilekçe/başvuru platformlarına giden bağlantılar

Sözlük temelli sayımlar DOĞRULANMADAN bulgu sayılmaz: betik her terim için
rastgele bağlam örnekleri (KWIC) seçip veri/kodlama/kwic_dogrulama.xlsx dosyasına
yazar. O dosyadaki "dogru_mu" sütununu doldurduktan sonra terim başına isabet
oranını raporlayabilirsin.

Kullanım:
  python 04_savunuculuk.py
  python 04_savunuculuk.py --kwic 40      # terim başına örnek sayısı
"""

import argparse
import json
import re
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
MESAJLAR = BASE / "veri" / "islenmis" / "mesajlar.parquet"
BAGLANTILAR = BASE / "veri" / "islenmis" / "baglantilar.parquet"
LOG_DIR = BASE / "loglar"
CODE_DIR = BASE / "veri" / "kodlama"

SABLON_MIN_GONDERICI = 3
SABLON_MIN_KELIME = 8

# Terimler iki gruba ayrılır: anlamı görece net olanlar ve bağlam gerektirenler.
TERIMLER_NET = {
    "dilekçe": r"\b(?:e-?)?dilekçe\w*",
    "imza": r"\bimza\w*",
    "kampanya": r"\bkampanya\w*",
}
TERIMLER_BELIRSIZ = {
    "etkinlik": r"\betkinlik\w*",
    "tt/gündem": r"\btt\b|\btrend\s*topic\b|\bgündem\s+ol\w*|\bgündeme\s+ta[şs]\w*",
    "hashtag/etiket": r"\bhashtag\w*|\betiket\w*",
    "twitter çağrısı": r"\btwitter\b|\bretweet\w*|\brt\s+at\w*|\bx'?te\b",
    "destek çağrısı": r"\bdestek\s+ol\w*|\bdesteğinizi\b|\bses\s+ol\w*|\bpaylaş\w*\s+lütfen",
    "toplanma çağrısı": r"\bsaat\s*\d{1,2}[.:]\d{2}\b.{0,40}\b(?:atıyoruz|başlıyoruz|olacağız)",
}
DILEKCE_ALANLARI = {"change.org", "tbmm.gov.tr", "cimer.gov.tr", "turkiye.gov.tr", "ipetitions.com"}
SOSYAL_MEDYA_ALANLARI = {"x.com", "twitter.com", "t.co", "instagram.com"}


def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def kwic(metin, kalip, genislik=70):
    """Terimin geçtiği bağlamı döndürür. Arama küçük harfli metinde yapılır,
    gösterim orijinal metinden alınır; eşleşme >>...<< ile işaretlenir."""
    if not metin:
        return ""
    kucuk = tr_lower(metin)
    eslesme = kalip.search(kucuk)
    if not eslesme:
        return ""
    kaynak = metin if len(kucuk) == len(metin) else kucuk   # uzunluk korunmadıysa küçük hali
    bas, son = eslesme.start(), eslesme.end()
    sol = max(0, bas - genislik)
    sag = min(len(kaynak), son + genislik)
    parca = (kaynak[sol:bas] + ">>" + kaynak[bas:son] + "<<" + kaynak[son:sag]).replace("\n", " ")
    return ("..." if sol else "") + parca + ("..." if sag < len(kaynak) else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kwic", type=int, default=30, help="terim başına doğrulama örneği")
    args = ap.parse_args()

    if not MESAJLAR.exists():
        raise SystemExit("Önce 02_temizle.py çalıştırın.")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    CODE_DIR.mkdir(parents=True, exist_ok=True)

    m = pd.read_parquet(MESAJLAR)
    m = m[m["metin_analizi"]].copy()
    m["_low"] = m["text_light"].map(tr_lower)
    toplam = len(m)
    print(f"Metin analizi kümesi: {toplam:,} mesaj\n")

    # 1) Hashtag
    m["hashtag_listesi"] = m["hashtags_json"].map(lambda s: [h.lower() for h in json.loads(s or "[]")])
    m["hashtagli"] = m["hashtag_listesi"].map(len) > 0

    ht_kayit = [(h, mid, pid, ay) for hs, mid, pid, ay in
                zip(m["hashtag_listesi"], m["msg_id"], m["sender_pid"], m["ay"]) for h in set(hs)]
    ht = pd.DataFrame(ht_kayit, columns=["hashtag", "msg_id", "sender_pid", "ay"])
    ht_ozet = (ht.groupby("hashtag")
               .agg(mesaj=("msg_id", "size"), gonderici=("sender_pid", "nunique"),
                    ilk_ay=("ay", "min"), son_ay=("ay", "max"), aktif_ay=("ay", "nunique"))
               .sort_values("mesaj", ascending=False))
    zirve = ht.groupby(["hashtag", "ay"]).size().rename("n").reset_index()
    zirve = zirve.loc[zirve.groupby("hashtag")["n"].idxmax()].set_index("hashtag")
    ht_ozet["zirve_ay"] = zirve["ay"]
    ht_ozet["zirve_mesaj"] = zirve["n"]
    ht_ozet.to_csv(LOG_DIR / "kampanya_hashtagleri.csv", encoding="utf-8-sig")

    # 2) Şablon metinler
    m["sablon"] = ((m["ortak_metin_gonderici"] >= SABLON_MIN_GONDERICI)
                   & (m["n_token"] >= SABLON_MIN_KELIME)).fillna(False)

    # 3) Sözlük
    tum_terimler = {**TERIMLER_NET, **TERIMLER_BELIRSIZ}
    kaliplar = {ad: re.compile(p) for ad, p in tum_terimler.items()}
    terim_satirlari, kwic_satirlari = [], []
    for ad, kalip in kaliplar.items():
        maske = m["_low"].str.contains(kalip, na=False)
        alt = m[maske]
        terim_satirlari.append({
            "terim": ad,
            "grup": "net" if ad in TERIMLER_NET else "bağlam gerektirir",
            "mesaj": int(maske.sum()),
            "oran_yuzde": round(100 * maske.mean(), 3),
            "gonderici": int(alt["sender_pid"].nunique()),
            "ilk_ay": alt["ay"].min() if len(alt) else None,
            "son_ay": alt["ay"].max() if len(alt) else None,
        })
        if len(alt):
            ornek = alt.sample(min(args.kwic, len(alt)), random_state=20260917)
            for _, r in ornek.iterrows():
                kwic_satirlari.append({
                    "terim": ad, "msg_id": r["msg_id"],
                    "tarih": r["date_tr"].strftime("%Y-%m-%d"),
                    "baglam": kwic(r["text_light"], kalip),
                    "dogru_mu": "", "not": "",
                })
        m[f"t_{ad}"] = maske
    terimler = pd.DataFrame(terim_satirlari).sort_values("mesaj", ascending=False)
    terimler.to_csv(LOG_DIR / "mobilizasyon_terimleri.csv", index=False, encoding="utf-8-sig")

    # 4) Bağlantılar
    dilekce_mesajlar, sosyal_mesajlar = set(), set()
    if BAGLANTILAR.exists():
        b = pd.read_parquet(BAGLANTILAR)
        dilekce_mesajlar = set(b.loc[b["domain"].isin(DILEKCE_ALANLARI), "msg_id"])
        sosyal_mesajlar = set(b.loc[b["domain"].isin(SOSYAL_MEDYA_ALANLARI), "msg_id"])
    m["dilekce_link"] = m["msg_id"].isin(dilekce_mesajlar)
    m["sosyal_medya_link"] = m["msg_id"].isin(sosyal_mesajlar)

    # Birleşik sinyaller
    net_sozluk = m[[f"t_{ad}" for ad in TERIMLER_NET]].any(axis=1)
    belirsiz_sozluk = m[[f"t_{ad}" for ad in TERIMLER_BELIRSIZ]].any(axis=1)
    m["sinyal_dar"] = m["hashtagli"] | m["sablon"] | net_sozluk | m["dilekce_link"]
    m["sinyal_genis"] = m["sinyal_dar"] | belirsiz_sozluk

    bilesenler = {
        "hashtag": m["hashtagli"], "şablon metin": m["sablon"],
        "net sözlük (dilekçe/imza/kampanya)": net_sozluk,
        "dilekçe bağlantısı": m["dilekce_link"],
        "bağlam gerektiren sözlük": belirsiz_sozluk,
    }
    ozet = {"metin_analizi_kumesi": toplam,
            "bilesenler": {ad: {"mesaj": int(s.sum()), "oran_yuzde": round(100 * s.mean(), 3),
                                "gonderici": int(m.loc[s, "sender_pid"].nunique())}
                           for ad, s in bilesenler.items()},
            "sinyal_dar": {"mesaj": int(m["sinyal_dar"].sum()),
                           "oran_yuzde": round(100 * m["sinyal_dar"].mean(), 3),
                           "gonderici": int(m.loc[m["sinyal_dar"], "sender_pid"].nunique())},
            "sinyal_genis": {"mesaj": int(m["sinyal_genis"].sum()),
                             "oran_yuzde": round(100 * m["sinyal_genis"].mean(), 3),
                             "gonderici": int(m.loc[m["sinyal_genis"], "sender_pid"].nunique())},
            "hashtag": {"benzersiz": int(ht_ozet.shape[0]),
                        "tek_ayda_kalan": int((ht_ozet["aktif_ay"] == 1).sum()),
                        "en_az_3_ay_suren": int((ht_ozet["aktif_ay"] >= 3).sum())},
            "sosyal_medya_linki_olan_mesaj": int(m["sosyal_medya_link"].sum()),
            "hashtag_ve_sosyal_medya_linki": int((m["hashtagli"] & m["sosyal_medya_link"]).sum())}
    (LOG_DIR / "savunuculuk_ozet.json").write_text(
        json.dumps(ozet, ensure_ascii=False, indent=2), encoding="utf-8")

    # Aylık seri
    aylik = m.groupby("ay").agg(mesaj=("msg_id", "size"), hashtagli=("hashtagli", "sum"),
                                sablon=("sablon", "sum"), sinyal_dar=("sinyal_dar", "sum"),
                                sinyal_genis=("sinyal_genis", "sum"),
                                gonderici=("sender_pid", "nunique")).reset_index()
    aylik["sinyal_dar_oran"] = (100 * aylik["sinyal_dar"] / aylik["mesaj"]).round(2)
    aylik.to_csv(LOG_DIR / "savunuculuk_aylik.csv", index=False, encoding="utf-8-sig")

    # Doğrulama dosyası
    kwic_df = pd.DataFrame(kwic_satirlari)
    yol = CODE_DIR / "kwic_dogrulama.xlsx"
    with pd.ExcelWriter(yol, engine="openpyxl") as w:
        kwic_df.to_excel(w, sheet_name="kwic", index=False)
        ht_ozet.reset_index().to_excel(w, sheet_name="hashtagler", index=False)
        terimler.to_excel(w, sheet_name="terim_ozeti", index=False)
        ws = w.book["kwic"]
        ws.column_dimensions["D"].width = 110
        ws.column_dimensions["E"].width = 14

    # Ekran raporu
    print("BİLEŞENLER")
    for ad, s in bilesenler.items():
        print(f"  {ad:38s} {int(s.sum()):>7,} mesaj  (%{100 * s.mean():.2f})  "
              f"{m.loc[s, 'sender_pid'].nunique():>5,} gönderici")
    print(f"\n  {'BİRLEŞİK (dar tanım)':38s} {ozet['sinyal_dar']['mesaj']:>7,} mesaj  "
          f"(%{ozet['sinyal_dar']['oran_yuzde']:.2f})  {ozet['sinyal_dar']['gonderici']:>5,} gönderici")
    print(f"  {'BİRLEŞİK (geniş tanım)':38s} {ozet['sinyal_genis']['mesaj']:>7,} mesaj  "
          f"(%{ozet['sinyal_genis']['oran_yuzde']:.2f})  {ozet['sinyal_genis']['gonderici']:>5,} gönderici")

    print("\nTERİMLER")
    print(terimler.to_string(index=False))

    print(f"\nHASHTAG: {ht_ozet.shape[0]:,} benzersiz | tek ayda kalan "
          f"{ozet['hashtag']['tek_ayda_kalan']:,} | 3+ ay süren {ozet['hashtag']['en_az_3_ay_suren']:,}")
    print("\nEn uzun süren 10 kampanya etiketi:")
    print(ht_ozet.sort_values(["aktif_ay", "mesaj"], ascending=False)
          .head(10).to_string())

    print("\nSinyal oranı en yüksek 12 ay:")
    print(aylik.sort_values("sinyal_dar_oran", ascending=False)
          .head(12)[["ay", "mesaj", "hashtagli", "sablon", "sinyal_dar", "sinyal_dar_oran"]]
          .to_string(index=False))

    print(f"\nDosyalar: savunuculuk_ozet.json, savunuculuk_aylik.csv, "
          f"kampanya_hashtagleri.csv, mobilizasyon_terimleri.csv ({LOG_DIR.name}) "
          f"ve {yol.name} ({CODE_DIR.name})")


if __name__ == "__main__":
    main()
