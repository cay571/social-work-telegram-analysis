#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06_dogrulama_hesapla.py - Doğrulama örnekleminden ölçütün başarısını hesaplar.

Girdiler (veri/kodlama/ altında):
  dogrulama_anahtar.csv     -> 05 numaralı betiğin ürettiği anahtar
  claude_kodlari.csv        -> Claude'un kör kodlaması (sira, kod_claude)
  dogrulama_insan_alt.csv   -> senin kodladığın 60 satır ('kod' sütunu E/H)

Çıktılar:
  loglar/dogrulama_sonuc.json ve ekrana özet

Hesaplananlar:
  * İsabet (precision): ölçütün "var" dediklerinin kaçı gerçekten savunuculuk
  * Kaçırma: ölçütün "yok" dediklerinin kaçı aslında savunuculuk
  * Düzeltilmiş yaygınlık: iki oranın korpus büyüklükleriyle ağırlıklandırılması
  * Kodlayıcılar arası uyum: Cohen kappa (Claude x sen)

Kullanım:
  python 06_dogrulama_hesapla.py
  python 06_dogrulama_hesapla.py --korpus 135062 --genis 7951 --dar 2557
"""

import argparse
import json
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
CODE_DIR = BASE / "veri" / "kodlama"
LOG_DIR = BASE / "loglar"


def oran_ci(basari, n):
    """Oran ve %95 normal yaklaşım güven aralığı."""
    if n == 0:
        return None, None, None
    p = basari / n
    yari = 1.96 * (p * (1 - p) / n) ** 0.5
    return round(100 * p, 2), round(100 * max(0, p - yari), 2), round(100 * min(1, p + yari), 2)


def kappa(a, b):
    """İki kodlayıcı, iki kategori için Cohen kappa."""
    n = len(a)
    if n == 0:
        return None
    uyum = sum(x == y for x, y in zip(a, b)) / n
    pa_e = sum(x == "E" for x in a) / n
    pb_e = sum(x == "E" for x in b) / n
    sans = pa_e * pb_e + (1 - pa_e) * (1 - pb_e)
    return round((uyum - sans) / (1 - sans), 3) if sans < 1 else None, round(100 * uyum, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--korpus", type=int, default=135062, help="metin analizi kümesi büyüklüğü")
    ap.add_argument("--genis", type=int, default=7951, help="geniş ölçütün işaretlediği mesaj sayısı")
    ap.add_argument("--dar", type=int, default=2557, help="dar ölçütün işaretlediği mesaj sayısı")
    args = ap.parse_args()

    anahtar = pd.read_csv(CODE_DIR / "dogrulama_anahtar.csv")
    claude = pd.read_csv(CODE_DIR / "claude_kodlari.csv")
    d = anahtar.merge(claude, on="sira", how="left")
    d["kod_claude"] = d["kod_claude"].str.strip().str.upper()

    poz = d[d["grup"] == "sinyal_var"]
    neg = d[d["grup"] == "sinyal_yok"]
    p_isabet = oran_ci((poz["kod_claude"] == "E").sum(), len(poz))
    p_kacirma = oran_ci((neg["kod_claude"] == "E").sum(), len(neg))

    # dar ölçüt, örneklem içindeki alt küme üzerinden
    dar_poz = poz[poz["sinyal_dar"].astype(str).str.lower().isin(["true", "1"])]
    p_dar = oran_ci((dar_poz["kod_claude"] == "E").sum(), len(dar_poz))

    # Düzeltilmiş yaygınlık: isabet x işaretlenen + kaçırma x işaretlenmeyen
    isabet = p_isabet[0] / 100 if p_isabet[0] is not None else 0
    kacirma = p_kacirma[0] / 100 if p_kacirma[0] is not None else 0
    tahmin = isabet * args.genis + kacirma * (args.korpus - args.genis)
    sonuc = {
        "ornek": {"sinyal_var": len(poz), "sinyal_yok": len(neg)},
        "isabet_genis_yuzde": {"tahmin": p_isabet[0], "alt": p_isabet[1], "ust": p_isabet[2]},
        "isabet_dar_yuzde": {"tahmin": p_dar[0], "alt": p_dar[1], "ust": p_dar[2], "n": len(dar_poz)},
        "kacirma_yuzde": {"tahmin": p_kacirma[0], "alt": p_kacirma[1], "ust": p_kacirma[2]},
        "duzeltilmis_yaygınlik": {
            "mesaj_tahmini": int(round(tahmin)),
            "yuzde": round(100 * tahmin / args.korpus, 2),
            "ham_genis_yuzde": round(100 * args.genis / args.korpus, 2),
            "ham_dar_yuzde": round(100 * args.dar / args.korpus, 2),
        },
        "bilesen_isabeti": {},
    }

    for bilesen, alt in poz.groupby("hangi_sinyal"):
        if len(alt) >= 5:
            o = oran_ci((alt["kod_claude"] == "E").sum(), len(alt))
            sonuc["bilesen_isabeti"][bilesen] = {"n": len(alt), "isabet_yuzde": o[0]}

    xlsx_yol = CODE_DIR / "dogrulama_insan_alt.xlsx"
    csv_yol = CODE_DIR / "dogrulama_insan_alt.csv"
    if xlsx_yol.exists():                      # Excel sürümü varsa o esas alınır
        insan = pd.read_excel(xlsx_yol, sheet_name="kodlama")
    else:
        insan = pd.read_csv(csv_yol)
    if "kod" in insan.columns and insan["kod"].notna().any():
        insan = insan[insan["kod"].notna()][["sira", "kod"]]
        insan["kod"] = insan["kod"].astype(str).str.strip().str.upper()
        ortak = d.merge(insan, on="sira")
        ortak = ortak[ortak["kod"].isin(["E", "H"])]
        k, uyum = kappa(list(ortak["kod_claude"]), list(ortak["kod"]))
        sonuc["kodlayici_uyumu"] = {"n": len(ortak), "kappa": k, "uyum_yuzde": uyum}
        fark = ortak[ortak["kod_claude"] != ortak["kod"]]
        sonuc["ayrisan_satirlar"] = fark[["sira", "kod_claude", "kod", "hangi_sinyal"]].to_dict("records")
    else:
        sonuc["kodlayici_uyumu"] = "insan kodlaması henüz doldurulmamış"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    (LOG_DIR / "dogrulama_sonuc.json").write_text(
        json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Örneklem: {len(poz)} sinyal veren + {len(neg)} sinyal vermeyen")
    print(f"İsabet (geniş ölçüt) : %{p_isabet[0]}  [%{p_isabet[1]} - %{p_isabet[2]}]")
    print(f"İsabet (dar ölçüt)   : %{p_dar[0]}  [%{p_dar[1]} - %{p_dar[2]}]  (n={len(dar_poz)})")
    print(f"Kaçırma oranı        : %{p_kacirma[0]}  [%{p_kacirma[1]} - %{p_kacirma[2]}]")
    print(f"\nHam geniş oran       : %{sonuc['duzeltilmis_yaygınlik']['ham_genis_yuzde']}")
    print(f"Düzeltilmiş tahmin   : %{sonuc['duzeltilmis_yaygınlik']['yuzde']} "
          f"(~{sonuc['duzeltilmis_yaygınlik']['mesaj_tahmini']:,} mesaj)")
    if sonuc["bilesen_isabeti"]:
        print("\nBileşen bazında isabet (n>=5):")
        for ad, v in sorted(sonuc["bilesen_isabeti"].items(), key=lambda x: -x[1]["n"]):
            print(f"  {ad:28s} n={v['n']:>3}  %{v['isabet_yuzde']}")
    if isinstance(sonuc["kodlayici_uyumu"], dict):
        u = sonuc["kodlayici_uyumu"]
        print(f"\nKodlayıcı uyumu (n={u['n']}): kappa={u['kappa']}, uyum %{u['uyum_yuzde']}")
        print(f"Ayrışan satır sayısı: {len(sonuc['ayrisan_satirlar'])}")
    else:
        print("\nİnsan kodlaması henüz yok; kappa hesaplanmadı.")
    print(f"\nAyrıntı: loglar/dogrulama_sonuc.json")


if __name__ == "__main__":
    main()
