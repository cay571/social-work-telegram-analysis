#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04b_kwic_yenile.py - Doğrulama dosyasındaki boş bağlamları doldurur.

04_savunuculuk.py'nin ilk sürümünde bağlam metni büyük harfli geçişlerde boş
kalıyordu (arama küçük harfte, gösterim orijinal metinde yapılıyordu). Bu betik
aynı mesajlar için bağlamı yeniden üretir; senin girdiğin "dogru_mu" ve "not"
sütunları korunur. Mevcut dosyanın üzerine yazmaz, yanına _v2 ekler.

Kullanım:
  python 04b_kwic_yenile.py
"""

import re
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
KWIC_DOSYA = BASE / "veri" / "kodlama" / "kwic_dogrulama.xlsx"
YENI_DOSYA = BASE / "veri" / "kodlama" / "kwic_dogrulama_v2.xlsx"
MESAJLAR = BASE / "veri" / "islenmis" / "mesajlar.parquet"


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sv", BASE / "04_savunuculuk.py")
    sv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sv)

    if not KWIC_DOSYA.exists():
        raise SystemExit(f"{KWIC_DOSYA.name} bulunamadı.")
    kwic_df = pd.read_excel(KWIC_DOSYA, sheet_name="kwic")
    m = pd.read_parquet(MESAJLAR, columns=["msg_id", "text_light"])
    metinler = dict(zip(m["msg_id"], m["text_light"]))

    kaliplar = {ad: re.compile(p) for ad, p in {**sv.TERIMLER_NET, **sv.TERIMLER_BELIRSIZ}.items()}

    yeni_baglam, tam_metin = [], []
    for terim, msg_id in zip(kwic_df["terim"], kwic_df["msg_id"]):
        metin = metinler.get(msg_id, "")
        kalip = kaliplar.get(terim)
        yeni_baglam.append(sv.kwic(metin, kalip) if kalip else "")
        tam_metin.append(metin[:600])

    onceki_bos = kwic_df["baglam"].isna() | (kwic_df["baglam"].astype(str).str.strip() == "")
    kwic_df["baglam"] = yeni_baglam
    kwic_df["tam_metin"] = tam_metin          # bağlam yetmezse mesajın tamamı
    hala_bos = kwic_df["baglam"].str.strip() == ""

    sutunlar = ["terim", "msg_id", "tarih", "baglam", "tam_metin", "dogru_mu", "not"]
    kwic_df = kwic_df[[c for c in sutunlar if c in kwic_df.columns]]

    with pd.ExcelWriter(YENI_DOSYA, engine="openpyxl") as w:
        kwic_df.to_excel(w, sheet_name="kwic", index=False)
        for sayfa in ("hashtagler", "terim_ozeti"):
            try:
                pd.read_excel(KWIC_DOSYA, sheet_name=sayfa).to_excel(w, sheet_name=sayfa, index=False)
            except Exception:
                pass
        ws = w.book["kwic"]
        ws.column_dimensions["D"].width = 110
        ws.column_dimensions["E"].width = 90
        ws.column_dimensions["F"].width = 12
        from openpyxl.styles import Alignment
        for satir in ws.iter_rows(min_row=2, min_col=4, max_col=5):
            for hucre in satir:
                hucre.alignment = Alignment(wrap_text=True, vertical="top")

    print(f"Önceden boş olan bağlam sayısı : {int(onceki_bos.sum())}")
    print(f"Şimdi boş kalan                : {int(hala_bos.sum())}")
    if hala_bos.any():
        print("Boş kalanlar (mesaj silinmiş ya da terim artık eşleşmiyor):")
        print(kwic_df.loc[hala_bos, ["terim", "msg_id", "tarih"]].to_string(index=False))
    print(f"\nYeni dosya: {YENI_DOSYA}")
    print("Eski dosyan olduğu gibi duruyor; işaretlerini v2 dosyasına taşıdım.")


if __name__ == "__main__":
    main()
