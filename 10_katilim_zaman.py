#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
10_katilim_zaman.py - Katılım ve rol analizi (S3) + zaman analizi (S4)

S3: Gini, Lorenz eğrisi, en aktif dilimlerin payı, kohortlar, rol tipolojisi
S4: Aylık hacim, olağan dışı aylar (Modifiye Z), bileşim değişimi, afet terimleri

Çıktılar (loglar/):
  katilim_rapor.json, lorenz.csv, roller.csv, kohortlar.csv
  zaman_rapor.json, aylik_seri.csv, afet_terimleri.csv

Kullanım:
  python 10_katilim_zaman.py
"""

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "veri" / "islenmis"
META_DIR = BASE / "veri" / "meta"
LOG_DIR = BASE / "loglar"
Z_ESIK = 3.5
MIN_MESAJ_ROL = 5          # rol ataması için asgari mesaj sayısı

AFET_TERIMLERI = {
    "deprem": r"\bdeprem\w*", "afad": r"\bafad\b", "enkaz": r"\benkaz\w*",
    "barınma": r"\bbarınma\w*|\bçadır\w*|\bkonteyner\w*", "gönüllü": r"\bgönüllü\w*",
    "yardım_kampanyası": r"\byardım\s+kampanya\w*|\bbağış\w*",
    "deprem_bolgesi": r"\bhatay\b|\bkahramanmaraş\b|\bmaraş\b|\badıyaman\b",
}


def tr_lower(s):
    return s.replace("I", "ı").replace("İ", "i").lower()


def gini(x):
    x = np.sort(np.asarray(x, dtype=float))
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * x.sum()))


def modifiye_z(seri):
    med = seri.median()
    mad = (seri - med).abs().median()
    if mad == 0:
        return pd.Series(0, index=seri.index), med, mad
    return 0.6745 * (seri - med) / mad, med, mad


def katilim(m, yanitlar, tahmin, adminler):
    k = m[m["katilim_analizi"]]
    kisi = k.groupby("sender_pid").agg(
        mesaj=("msg_id", "size"), ilk=("date_tr", "min"), son=("date_tr", "max"),
        aktif_ay=("ay", "nunique"), url_mesaj=("n_urls", lambda s: int((s > 0).sum())))
    kisi["kohort"] = kisi["ilk"].dt.year
    kisi["omur_gun"] = (kisi["son"] - kisi["ilk"]).dt.days
    kisi["yonetici"] = kisi.index.isin(adminler)

    talep = tahmin.set_index("msg_id")["talep"]
    kt = k.assign(talep=k["msg_id"].map(talep)).groupby("sender_pid")["talep"].mean()
    kisi["talep_orani"] = kt.round(3)
    verdigi = yanitlar.groupby("sender_pid").size()
    kisi["verdigi_yanit"] = kisi.index.map(verdigi).fillna(0).astype(int)
    kisi["yanit_orani"] = (kisi["verdigi_yanit"] / kisi["mesaj"]).round(3)
    kisi["url_orani"] = (kisi["url_mesaj"] / kisi["mesaj"]).round(3)

    def rol(r):
        if r["mesaj"] < MIN_MESAJ_ROL:
            return "seyrek katılımcı"
        if r["url_orani"] >= 0.25:
            return "kaynak/duyuru paylaşan"
        if r["talep_orani"] >= 0.40:
            return "soru soran"
        if r["yanit_orani"] >= 0.40:
            return "yanıtlayan"
        return "karışık"
    kisi["rol"] = kisi.apply(rol, axis=1)

    pay = kisi["mesaj"].sort_values(ascending=False)
    toplam = pay.sum()
    kumulatif = pay.cumsum() / toplam
    dilim = lambda p: round(100 * pay.head(max(1, int(len(pay) * p))).sum() / toplam, 1)
    kisi_80 = int((kumulatif <= 0.8).sum() + 1)

    lorenz = pd.DataFrame({
        "kisi_orani": np.arange(1, len(pay) + 1) / len(pay),
        "mesaj_orani": np.sort(pay.values).cumsum() / toplam})
    seyrek = lorenz.iloc[:: max(1, len(lorenz) // 500)]
    if not seyrek.index.equals(lorenz.index[-1:]) and seyrek.index[-1] != lorenz.index[-1]:
        seyrek = pd.concat([seyrek, lorenz.iloc[[-1]]])       # eğri (1,1) noktasında bitmeli
    seyrek.to_csv(LOG_DIR / "lorenz.csv", index=False, encoding="utf-8-sig")

    roller = (kisi.groupby("rol").agg(kisi=("mesaj", "size"), mesaj=("mesaj", "sum"),
                                      ort_mesaj=("mesaj", "mean")).round(1))
    roller["kisi_yuzde"] = (100 * roller["kisi"] / len(kisi)).round(1)
    roller["mesaj_yuzde"] = (100 * roller["mesaj"] / toplam).round(1)
    roller.to_csv(LOG_DIR / "roller.csv", encoding="utf-8-sig")

    kohort = kisi.groupby("kohort").agg(
        kisi=("mesaj", "size"), ort_mesaj=("mesaj", "mean"), ort_aktif_ay=("aktif_ay", "mean"),
        tek_mesajli_yuzde=("mesaj", lambda s: 100 * (s == 1).mean()),
        bir_aydan_uzun_yuzde=("mesaj", lambda s: np.nan)).round(1)
    kohort["bir_aydan_uzun_yuzde"] = (kisi.groupby("kohort")["aktif_ay"]
                                      .apply(lambda s: 100 * (s > 1).mean()).round(1))
    kohort.to_csv(LOG_DIR / "kohortlar.csv", encoding="utf-8-sig")

    rapor = {
        "gonderici": int(len(kisi)),
        "mesaj": int(toplam),
        "gini": round(gini(pay.values), 4),
        "en_aktif_yuzde1_payi": dilim(0.01),
        "en_aktif_yuzde5_payi": dilim(0.05),
        "en_aktif_yuzde10_payi": dilim(0.10),
        "en_aktif_20_kisi_payi": round(100 * pay.head(20).sum() / toplam, 1),
        "mesajlarin_80ini_ureten_kisi": kisi_80,
        "mesajlarin_80ini_ureten_kisi_yuzde": round(100 * kisi_80 / len(kisi), 1),
        "tek_mesajli_gonderici_yuzde": round(100 * (pay == 1).mean(), 1),
        "medyan_mesaj": int(pay.median()),
        "yonetici_sayisi": int(kisi["yonetici"].sum()),
        "yonetici_mesaj_payi": round(100 * kisi.loc[kisi["yonetici"], "mesaj"].sum() / toplam, 1),
        "roller": roller.to_dict(orient="index"),
    }
    return rapor, kisi


def zaman(m, tahmin):
    a = m[m["metin_analizi"] & ~m["is_dup_same_sender"]].copy()
    a["talep"] = a["msg_id"].map(tahmin.set_index("msg_id")["talep"])

    aylik = a.groupby("ay").agg(mesaj=("msg_id", "size"), gonderici=("sender_pid", "nunique"),
                                talep_yuzde=("talep", lambda s: round(100 * s.mean(), 1)),
                                yanit_yuzde=("reply_to_msg_id",
                                             lambda s: round(100 * s.notna().mean(), 1)))
    aylik["mesaj_kisi_basi"] = (aylik["mesaj"] / aylik["gonderici"]).round(1)
    z, med, mad = modifiye_z(aylik["mesaj"])
    aylik["z"] = z.round(2)
    aylik["olagandisi"] = aylik["z"] > Z_ESIK
    aylik.to_csv(LOG_DIR / "aylik_seri.csv", encoding="utf-8-sig")

    yillik = a.groupby("yil").agg(mesaj=("msg_id", "size"), gonderici=("sender_pid", "nunique"),
                                  talep_yuzde=("talep", lambda s: round(100 * s.mean(), 1)),
                                  ort_token=("n_token", "mean")).round(1)
    yillik["mesaj_kisi_basi"] = (yillik["mesaj"] / yillik["gonderici"]).round(1)

    # Afet terimleri (deprem etkisinin doğrudan testi)
    dusuk = a["text_light"].map(tr_lower)
    afet = pd.DataFrame({"ay": a["ay"]})
    for ad, kalip in AFET_TERIMLERI.items():
        afet[ad] = dusuk.str.contains(re.compile(kalip), na=False)
    afet_aylik = afet.groupby("ay").mean().mul(100).round(2)
    afet_aylik["mesaj"] = a.groupby("ay").size()
    afet_aylik.to_csv(LOG_DIR / "afet_terimleri.csv", encoding="utf-8-sig")

    deprem_aylari = ["2023-02", "2023-03"]
    var = [x for x in deprem_aylari if x in afet_aylik.index]
    karsilastirma = {}
    if var:
        temel = afet_aylik.drop(index=var).drop(columns="mesaj").mean().round(2)
        deprem = afet_aylik.loc[var].drop(columns="mesaj").mean().round(2)
        karsilastirma = {ad: {"deprem_aylari_yuzde": float(deprem[ad]),
                              "diger_aylar_yuzde": float(temel[ad]),
                              "kat": round(float(deprem[ad] / temel[ad]), 1) if temel[ad] else None}
                         for ad in AFET_TERIMLERI}

    rapor = {
        "ay_sayisi": int(len(aylik)),
        "aylik_medyan": int(med), "mad": float(mad),
        "esik_mesaj": int(round(med + Z_ESIK * mad / 0.6745)),
        "olagandisi_aylar": aylik[aylik["olagandisi"]][["mesaj", "z"]].round(2)
        .reset_index().to_dict(orient="records"),
        "yillik": yillik.reset_index().to_dict(orient="records"),
        "afet_terimleri": karsilastirma,
        "saat_dagilimi": (a.groupby(a["date_tr"].dt.hour).size() / len(a) * 100).round(1).to_dict(),
    }
    return rapor


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    m = pd.read_parquet(OUT_DIR / "mesajlar.parquet")
    yanitlar = pd.read_parquet(OUT_DIR / "yanitlar.parquet")
    tahmin = pd.read_parquet(OUT_DIR / "talep_tahmin.parquet")
    admin_dosya = META_DIR / "yonetici_pidleri.json"
    adminler = set(json.loads(admin_dosya.read_text(encoding="utf-8"))) if admin_dosya.exists() else set()

    k_rapor, kisi = katilim(m, yanitlar, tahmin, adminler)
    (LOG_DIR / "katilim_rapor.json").write_text(
        json.dumps(k_rapor, ensure_ascii=False, indent=2), encoding="utf-8")

    z_rapor = zaman(m, tahmin)
    (LOG_DIR / "zaman_rapor.json").write_text(
        json.dumps(z_rapor, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== KATILIM (S3) ===")
    for anahtar in ("gonderici", "mesaj", "gini", "en_aktif_yuzde1_payi", "en_aktif_yuzde5_payi",
                    "en_aktif_yuzde10_payi", "en_aktif_20_kisi_payi",
                    "mesajlarin_80ini_ureten_kisi", "mesajlarin_80ini_ureten_kisi_yuzde",
                    "tek_mesajli_gonderici_yuzde", "medyan_mesaj",
                    "yonetici_sayisi", "yonetici_mesaj_payi"):
        print(f"  {anahtar:38s} {k_rapor[anahtar]}")
    print("\nROLLER:")
    print(pd.DataFrame(k_rapor["roller"]).T[["kisi", "kisi_yuzde", "mesaj_yuzde", "ort_mesaj"]].to_string())

    print("\n=== ZAMAN (S4) ===")
    print(f"  aylık medyan {z_rapor['aylik_medyan']}, MAD {z_rapor['mad']}, "
          f"olağan dışı eşiği {z_rapor['esik_mesaj']} mesaj")
    print("  Olağan dışı aylar:")
    for r in z_rapor["olagandisi_aylar"]:
        print(f"    {r['ay']}  {r['mesaj']:>6,} mesaj  Z={r['z']}")
    print("\n  Yıllık:")
    print(pd.DataFrame(z_rapor["yillik"]).to_string(index=False))
    if z_rapor["afet_terimleri"]:
        print("\n  Afet terimleri (Şubat-Mart 2023 vs diğer aylar, mesaj yüzdesi):")
        for ad, v in z_rapor["afet_terimleri"].items():
            kat = f"{v['kat']}x" if v["kat"] else "-"
            print(f"    {ad:20s} %{v['deprem_aylari_yuzde']:<6} vs %{v['diger_aylar_yuzde']:<6} ({kat})")
    print("\nDosyalar: katilim_rapor.json, zaman_rapor.json, lorenz.csv, roller.csv, "
          "kohortlar.csv, aylik_seri.csv, afet_terimleri.csv")


if __name__ == "__main__":
    main()