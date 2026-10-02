#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05_dogrulama_ornegi.py - Savunuculuk ölçütünün doğrulanması için örneklem hazırlar.

Neden: Terim bazlı doğrulama yerine BİRLEŞİK ölçütü doğruluyoruz. Ölçütün
"savunuculuk var" dediği mesajlardan da, "yok" dediklerinden de örnek alıyoruz;
böylece hem isabet (precision) hem kaçırma (recall) tahmin edilebiliyor.

Üretilen dosyalar (veri/kodlama/):
  dogrulama_ornegi.csv        -> 250 mesaj, kodlanacak (bana göndereceğin dosya)
  dogrulama_insan_alt.csv     -> bunların 60'ı, senin bağımsız kodlaman için
  dogrulama_anahtar.csv       -> hangi mesajın hangi sinyalden geldiği (kodlarken bakılmaz)

Kullanım:
  python 05_dogrulama_ornegi.py
  python 05_dogrulama_ornegi.py --pozitif 150 --negatif 100 --insan 60
"""

import argparse
import importlib.util
import json
import re
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
MESAJLAR = BASE / "veri" / "islenmis" / "mesajlar.parquet"
BAGLANTILAR = BASE / "veri" / "islenmis" / "baglantilar.parquet"
CODE_DIR = BASE / "veri" / "kodlama"
TOHUM = 20260918
METIN_SINIR = 700


def sinyalleri_hesapla(m, sv):
    m = m[m["metin_analizi"]].copy()
    m["_low"] = m["text_light"].map(sv.tr_lower)
    m["hashtagli"] = m["hashtags_json"].map(lambda s: len(json.loads(s or "[]")) > 0)
    m["sablon"] = ((m["ortak_metin_gonderici"] >= sv.SABLON_MIN_GONDERICI)
                   & (m["n_token"] >= sv.SABLON_MIN_KELIME)).fillna(False)

    dilekce_mesajlar = set()
    if BAGLANTILAR.exists():
        b = pd.read_parquet(BAGLANTILAR)
        dilekce_mesajlar = set(b.loc[b["domain"].isin(sv.DILEKCE_ALANLARI), "msg_id"])
    m["dilekce_link"] = m["msg_id"].isin(dilekce_mesajlar)

    net, belirsiz = [], []
    for ad, kalip in sv.TERIMLER_NET.items():
        m[f"t_{ad}"] = m["_low"].str.contains(re.compile(kalip), na=False)
        net.append(f"t_{ad}")
    for ad, kalip in sv.TERIMLER_BELIRSIZ.items():
        m[f"t_{ad}"] = m["_low"].str.contains(re.compile(kalip), na=False)
        belirsiz.append(f"t_{ad}")

    m["sinyal_dar"] = m["hashtagli"] | m["sablon"] | m[net].any(axis=1) | m["dilekce_link"]
    m["sinyal_genis"] = m["sinyal_dar"] | m[belirsiz].any(axis=1)

    etiketler = []
    adlar = ([("hashtag", "hashtagli"), ("şablon", "sablon"), ("dilekçe-linki", "dilekce_link")]
             + [(ad, f"t_{ad}") for ad in list(sv.TERIMLER_NET) + list(sv.TERIMLER_BELIRSIZ)])
    for _, satir in m[[s for _, s in adlar]].iterrows():
        etiketler.append("+".join(ad for (ad, s), v in zip(adlar, satir) if v))
    m["hangi_sinyal"] = etiketler
    return m


def tabakali(kaynak, n, tohum):
    if len(kaynak) <= n:
        return kaynak.copy()
    paylar = (kaynak.groupby("yil").size() / len(kaynak) * n).round().astype(int)
    parcalar = [kaynak[kaynak["yil"] == y].sample(min(k, (kaynak["yil"] == y).sum()),
                                                  random_state=tohum)
                for y, k in paylar.items() if k > 0]
    ornek = pd.concat(parcalar)
    return ornek.sample(min(n, len(ornek)), random_state=tohum)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pozitif", type=int, default=150)
    ap.add_argument("--negatif", type=int, default=100)
    ap.add_argument("--insan", type=int, default=60)
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location("sv", BASE / "04_savunuculuk.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)

    CODE_DIR.mkdir(parents=True, exist_ok=True)
    m = sinyalleri_hesapla(pd.read_parquet(MESAJLAR), sv)

    poz = tabakali(m[m["sinyal_genis"]], args.pozitif, TOHUM)
    neg = tabakali(m[~m["sinyal_genis"]], args.negatif, TOHUM + 1)
    ornek = pd.concat([poz.assign(grup="sinyal_var"), neg.assign(grup="sinyal_yok")])
    ornek = ornek.sample(frac=1, random_state=TOHUM).reset_index(drop=True)
    ornek["sira"] = range(1, len(ornek) + 1)

    kodlanacak = pd.DataFrame({
        "sira": ornek["sira"],
        "tarih": ornek["date_tr"].dt.strftime("%Y-%m-%d"),
        "metin": ornek["text_light"].str.slice(0, METIN_SINIR).str.replace(r"\s+", " ", regex=True),
        "kod": "",
    })
    anahtar = pd.DataFrame({
        "sira": ornek["sira"], "msg_id": ornek["msg_id"], "grup": ornek["grup"],
        "hangi_sinyal": ornek["hangi_sinyal"], "sinyal_dar": ornek["sinyal_dar"],
        "sinyal_genis": ornek["sinyal_genis"], "yil": ornek["yil"],
    })
    insan = kodlanacak[kodlanacak["sira"].isin(
        ornek.sample(min(args.insan, len(ornek)), random_state=TOHUM + 2)["sira"])]

    kodlanacak.to_csv(CODE_DIR / "dogrulama_ornegi.csv", index=False, encoding="utf-8-sig")
    insan.to_csv(CODE_DIR / "dogrulama_insan_alt.csv", index=False, encoding="utf-8-sig")
    anahtar.to_csv(CODE_DIR / "dogrulama_anahtar.csv", index=False, encoding="utf-8-sig")

    print(f"Metin analizi kümesi     : {len(m):,}")
    print(f"Sinyal veren (geniş)     : {int(m['sinyal_genis'].sum()):,}")
    print(f"Sinyal veren (dar)       : {int(m['sinyal_dar'].sum()):,}")
    print(f"\nÖrneklem: {len(poz)} sinyal veren + {len(neg)} sinyal vermeyen = {len(ornek)} mesaj")
    print(f"İnsan kodlaması için alt örneklem: {len(insan)} mesaj")
    print("\nSinyal bileşenlerinin örneklemdeki dağılımı:")
    print(anahtar[anahtar.grup == "sinyal_var"]["hangi_sinyal"].value_counts().head(12).to_string())
    print(f"\nDosyalar {CODE_DIR} altında:")
    print("  dogrulama_ornegi.csv      -> bana gönder")
    print("  dogrulama_insan_alt.csv   -> 'kod' sütununu sen doldur (E/H)")
    print("  dogrulama_anahtar.csv     -> kodlarken açma; hesaplama sırasında kullanılacak")


if __name__ == "__main__":
    main()
