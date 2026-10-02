#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_ortak_metin.py - Farklı kişilerce paylaşılan özdeş metinleri ve hashtag'leri listeler.

Amaç: Kopyala-yapıştır kampanya metinlerini ("etkinlik" çağrıları, sloganlar,
imza metinleri) tespit etmek. Kısa nezaket kalıplarını ("teşekkürler", "evet")
elemek için asgari kelime sayısı ve asgari gönderici sayısı eşikleri kullanılır.

Kullanım:
  python 03_ortak_metin.py
  python 03_ortak_metin.py --gonderici 5 --kelime 12 --adet 40
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
MESAJLAR = BASE / "veri" / "islenmis" / "mesajlar.parquet"
CIKTI_DIR = BASE / "loglar"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gonderici", type=int, default=3, help="asgari farklı gönderici sayısı")
    ap.add_argument("--kelime", type=int, default=8, help="asgari kelime sayısı")
    ap.add_argument("--adet", type=int, default=25, help="ekranda gösterilecek grup sayısı")
    args = ap.parse_args()

    if not MESAJLAR.exists():
        raise SystemExit("veri/islenmis/mesajlar.parquet bulunamadı. Önce 02_temizle.py çalıştırın.")
    m = pd.read_parquet(MESAJLAR)
    CIKTI_DIR.mkdir(parents=True, exist_ok=True)

    secim = m[(m["ortak_metin_gonderici"] >= args.gonderici)
              & (m["n_token"] >= args.kelime)
              & m["metin_analizi"]]

    gruplar = (secim.groupby("ortak_metin_id")
               .agg(gonderici=("sender_pid", "nunique"),
                    mesaj=("msg_id", "size"),
                    ilk=("date_tr", "min"),
                    son=("date_tr", "max"),
                    kelime=("n_token", "first"),
                    ornek=("text_light", "first"))
               .sort_values("mesaj", ascending=False))
    gruplar["ilk"] = gruplar["ilk"].dt.strftime("%Y-%m-%d")
    gruplar["son"] = gruplar["son"].dt.strftime("%Y-%m-%d")

    yol = CIKTI_DIR / "ortak_metinler.csv"
    gruplar.to_csv(yol, encoding="utf-8-sig")

    print(f"Eşikler: en az {args.gonderici} farklı gönderici, en az {args.kelime} kelime")
    print(f"{len(gruplar):,} grup, toplam {int(gruplar['mesaj'].sum()):,} mesaj "
          f"({int(gruplar['gonderici'].sum()):,} gönderici-kaydı)\n")

    gosterim = gruplar.head(args.adet).copy()
    gosterim["ornek"] = gosterim["ornek"].str.slice(0, 150)
    with pd.option_context("display.max_colwidth", 160, "display.width", 250):
        print(gosterim.to_string())

    # Hashtag dökümü (savunuculuk/mobilizasyon için)
    sayac = Counter()
    mesaj_sayaci = Counter()
    for js in m.loc[m["metin_analizi"], "hashtags_json"]:
        etiketler = [h.lower() for h in json.loads(js or "[]")]
        sayac.update(etiketler)
        mesaj_sayaci.update(set(etiketler))
    if sayac:
        ht = (pd.DataFrame({"hashtag": list(mesaj_sayaci), "mesaj": list(mesaj_sayaci.values())})
              .sort_values("mesaj", ascending=False).reset_index(drop=True))
        ht.to_csv(CIKTI_DIR / "hashtagler.csv", index=False, encoding="utf-8-sig")
        print(f"\nEn sık 20 hashtag ({len(ht):,} benzersiz hashtag):")
        print(ht.head(20).to_string(index=False))

    print(f"\nTam listeler: {yol.name}, hashtagler.csv ({CIKTI_DIR})")


if __name__ == "__main__":
    main()
