#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
12_sekiller.py - Makale şekillerini üretir (300 dpi PNG + vektörel PDF)

Şekil 1: Lorenz eğrisi (katılım eşitsizliği)
Şekil 2: Aylık mesaj hacmi, olağan dışı aylar işaretli
Şekil 3: Yıllara göre bilgi talebi oranı ve kişi başına mesaj (kısmi yıllar taralı)
Şekil 4: Mesajların saate göre dağılımı
Şekil 5: Savunuculuk sinyalinin aylık seyri

Çıktı: sekiller/ klasörü

Kullanım:
  python 12_sekiller.py
  python 12_sekiller.py --dil en                  # İngilizce etiketler
  python 12_sekiller.py --dil en --baslik-yok     # dergi sürümü: İngilizce, başlıksız
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

BASE = Path(__file__).resolve().parent
LOG_DIR = BASE / "loglar"
FIG_DIR = BASE / "sekiller"
RENK = "#31506b"
VURGU = "#b4432e"

METIN = {
    "tr": {
        "s1_baslik": "Aylık mesaj hacmi (2020-2026)", "s1_y": "Mesaj sayısı", "s1_x": "Ay",
        "s1_esik": "Olağan dışı eşiği", "s1_nokta": "Olağan dışı ay",
        "s2_baslik": "Katılımın dağılımı (Lorenz eğrisi)",
        "s2_x": "Göndericilerin kümülatif oranı", "s2_y": "Mesajların kümülatif oranı",
        "s2_esit": "Eşit dağılım", "s2_gozlenen": "Gözlenen dağılım",
        "s3_baslik": "Mesajların saate göre dağılımı", "s3_x": "Saat (Türkiye saati)",
        "s3_y": "Mesajların yüzdesi",
        "s4_baslik": "Savunuculuk içeriğinin aylık seyri", "s4_y": "Mesajların yüzdesi",
        "s4_dar": "Dar ölçüt", "s4_genis": "Geniş ölçüt",
        "s5_baslik": "Yıllara göre bilgi talebi oranı ve kişi başına mesaj",
        "s5_talep": "Bilgi talebi oranı (%)", "s5_kisi": "Kişi başına mesaj", "s5_x": "Yıl",
        "s5_kismi": "Kısmi yıl (2020, 2026)",
    },
    "en": {
        "s1_baslik": "Monthly message volume (2020-2026)", "s1_y": "Messages", "s1_x": "Month",
        "s1_esik": "Outlier threshold", "s1_nokta": "Outlier month",
        "s2_baslik": "Distribution of participation (Lorenz curve)",
        "s2_x": "Cumulative share of senders", "s2_y": "Cumulative share of messages",
        "s2_esit": "Equal distribution", "s2_gozlenen": "Observed",
        "s3_baslik": "Hourly distribution of messages", "s3_x": "Hour (Turkey time)",
        "s3_y": "% of messages",
        "s4_baslik": "Monthly trend of advocacy content", "s4_y": "% of messages",
        "s4_dar": "Narrow criterion", "s4_genis": "Broad criterion",
        "s5_baslik": "Information requests and messages per sender by year",
        "s5_talep": "Information requests (%)", "s5_kisi": "Messages per sender", "s5_x": "Year",
        "s5_kismi": "Partial year (2020, 2026)",
    },
}


AYAR = {"baslik": True, "ek": ""}   # main() içinde komut satırına göre doldurulur


def baslik(ax, metin, **kw):
    """--baslik-yok verilirse şekil içine başlık yazılmaz (başlık dergide altyazıda olur)."""
    if AYAR["baslik"]:
        ax.set_title(metin, **kw)


def kaydet(fig, ad):
    ad = ad + AYAR["ek"]
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for uzanti in ("png", "pdf"):
        fig.savefig(FIG_DIR / f"{ad}.{uzanti}", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  {ad}.png / {ad}.pdf")


def sekil1(T):
    d = pd.read_csv(LOG_DIR / "aylik_seri.csv")
    z = json.loads((LOG_DIR / "zaman_rapor.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(d["ay"], d["mesaj"], color=RENK, linewidth=1.4)
    olagan = d[d["olagandisi"] == True] if "olagandisi" in d else d.iloc[0:0]
    ax.scatter(olagan["ay"], olagan["mesaj"], color=VURGU, zorder=5, s=32,
               label=T["s1_nokta"])
    for i, (_, r) in enumerate(olagan.iterrows()):      # etiketler çakışmasın diye şaşırtmalı
        yon = 12 if i % 2 == 0 else -16
        ax.annotate(r["ay"], (r["ay"], r["mesaj"]), textcoords="offset points",
                    xytext=(0, yon), ha="center", fontsize=7, color=VURGU)
    ax.axhline(z["esik_mesaj"], color="gray", linestyle="--", linewidth=0.9,
               label=f"{T['s1_esik']} ({z['esik_mesaj']:,})")
    baslik(ax, T["s1_baslik"]); ax.set_xlabel(T["s1_x"]); ax.set_ylabel(T["s1_y"])
    ax.set_xticks(d["ay"][::6]); ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.legend(frameon=False, fontsize=8); ax.spines[["top", "right"]].set_visible(False)
    kaydet(fig, "sekil2_aylik_hacim")


def sekil2(T):
    d = pd.read_csv(LOG_DIR / "lorenz.csv")
    k = json.loads((LOG_DIR / "katilim_rapor.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(5.2, 5))
    ax.plot([0, 1], [0, 1], color="gray", linestyle="--", linewidth=0.9, label=T["s2_esit"])
    if d["kisi_orani"].iloc[-1] < 1 or d["mesaj_orani"].iloc[-1] < 1:
        d = pd.concat([d, pd.DataFrame({"kisi_orani": [1.0], "mesaj_orani": [1.0]})])
    ax.plot(d["kisi_orani"], d["mesaj_orani"], color=RENK, linewidth=1.8, label=T["s2_gozlenen"])
    ax.fill_between(d["kisi_orani"], d["mesaj_orani"], d["kisi_orani"], color=RENK, alpha=0.12)
    ax.text(0.05, 0.9, f"Gini = {k['gini']:.3f}", transform=ax.transAxes, fontsize=10)
    baslik(ax, T["s2_baslik"]); ax.set_xlabel(T["s2_x"]); ax.set_ylabel(T["s2_y"])
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    kaydet(fig, "sekil1_lorenz")


def sekil3(T):
    z = json.loads((LOG_DIR / "zaman_rapor.json").read_text(encoding="utf-8"))
    saat = pd.Series(z["saat_dagilimi"]).rename(lambda s: int(s)).sort_index()
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.bar(saat.index, saat.values, color=RENK, width=0.75)
    baslik(ax, T["s3_baslik"]); ax.set_xlabel(T["s3_x"]); ax.set_ylabel(T["s3_y"])
    ax.set_xticks(range(0, 24)); ax.tick_params(axis="x", labelsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    kaydet(fig, "sekil4_saat")


def sekil4(T):
    d = pd.read_csv(LOG_DIR / "savunuculuk_aylik.csv")
    d["genis_oran"] = 100 * d["sinyal_genis"] / d["mesaj"]
    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.plot(d["ay"], d["genis_oran"], color="#9aa7b4", linewidth=1.2, label=T["s4_genis"])
    ax.plot(d["ay"], d["sinyal_dar_oran"], color=VURGU, linewidth=1.6, label=T["s4_dar"])
    baslik(ax, T["s4_baslik"]); ax.set_ylabel(T["s4_y"])
    ax.set_xticks(d["ay"][::6]); ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.legend(frameon=False, fontsize=8); ax.spines[["top", "right"]].set_visible(False)
    kaydet(fig, "sekil5_savunuculuk")


def sekil5(T):
    z = json.loads((LOG_DIR / "zaman_rapor.json").read_text(encoding="utf-8"))
    y = pd.DataFrame(z["yillik"])
    fig, ax = plt.subplots(figsize=(7, 4))
    kismi = y["yil"].isin([2020, 2026])
    ax.bar(y.loc[~kismi, "yil"], y.loc[~kismi, "talep_yuzde"], color=RENK, width=0.6,
           label=T["s5_talep"])
    ax.bar(y.loc[kismi, "yil"], y.loc[kismi, "talep_yuzde"], color="white", edgecolor=RENK,
           hatch="///", width=0.6, label=T["s5_kismi"])
    ax.set_ylabel(T["s5_talep"]); ax.set_xlabel(T["s5_x"])
    ax2 = ax.twinx()
    ax2.plot(y["yil"], y["mesaj_kisi_basi"], color=VURGU, marker="o", linewidth=1.6,
             label=T["s5_kisi"])
    ax2.set_ylabel(T["s5_kisi"])
    baslik(ax, T["s5_baslik"], pad=12)
    cizgiler = ax.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    etiketler = ax.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
    ax.legend(cizgiler, etiketler, fontsize=8, loc="upper center",
              bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False)
    ax.spines[["top"]].set_visible(False); ax2.spines[["top"]].set_visible(False)
    kaydet(fig, "sekil3_talep_yogunluk")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dil", choices=["tr", "en"], default="tr")
    ap.add_argument("--baslik-yok", action="store_true",
                    help="şekillerin içine başlık yazma (dergi sürümü için)")
    args = ap.parse_args()
    T = METIN[args.dil]
    AYAR["baslik"] = not args.baslik_yok
    AYAR["ek"] = ("_en" if args.dil == "en" else "") + ("_basliksiz" if args.baslik_yok else "")
    plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.25,
                         "grid.linewidth": 0.5, "figure.dpi": 110})

    print(f"Şekiller üretiliyor ({args.dil}):")
    # Sıra, makalede ilk anılma sırasıdır (Şekil 1 = Lorenz, ... Şekil 5 = savunuculuk)
    for ad, fn in (("Şekil 1 (Lorenz)", sekil2), ("Şekil 2 (aylık hacim)", sekil1),
                   ("Şekil 3 (talep ve yoğunluk)", sekil5), ("Şekil 4 (saat)", sekil3),
                   ("Şekil 5 (savunuculuk)", sekil4)):
        try:
            fn(T)
        except FileNotFoundError as e:
            print(f"  {ad} atlandı, dosya yok: {Path(str(e.filename)).name}")
        except Exception as e:
            print(f"  {ad} hata: {type(e).__name__}: {e}")
    print(f"\nKlasör: {FIG_DIR}")


if __name__ == "__main__":
    main()