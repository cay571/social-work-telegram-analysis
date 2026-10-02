#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
09_talep_yanit.py - Bilgi talebi sınıflandırması + yanıt/karşılık analizi (S2)

İki iş yapar:
  1. "Bilgi talebi / diğer" ikili sınıflandırıcısını 600 etiketli mesajla eğitir,
     çapraz doğrulama sonuçlarını raporlar, tüm korpusa uygular. İkili karışıklık
     matrisiyle düzeltilmiş yaygınlık tahminini de hesaplar.
  2. Yanıt ilişkilerini kullanarak talep mesajlarının ne ölçüde ve ne hızla
     karşılık bulduğunu ölçer; yanıtlayan profillerini çıkarır.

Çıktılar (loglar/):
  talep_yanit_rapor.json, talep_yillik.csv, yanit_sureleri.csv, yanitlayanlar.csv
  veri/islenmis/talep_tahmin.parquet

Kullanım:
  python 09_talep_yanit.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import FeatureUnion, Pipeline

BASE = Path(__file__).resolve().parent
CODE_DIR = BASE / "veri" / "kodlama"
OUT_DIR = BASE / "veri" / "islenmis"
LOG_DIR = BASE / "loglar"
TOHUM = 20260920
SON_KESIT_GUN = 7          # son günlerdeki mesajlar yanıt için "sansürlü" sayılır


def model_kur():
    return Pipeline([
        ("ozellik", FeatureUnion([
            ("kelime", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
            ("karakter", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                         min_df=3, sublinear_tf=True)),
        ])),
        ("model", LogisticRegression(max_iter=2000, class_weight="balanced", C=5.0)),
    ])


def ikili_duzeltme(km, ham_oran):
    """2x2 karışıklık matrisiyle yaygınlık düzeltmesi (Rogan-Gladen).
    km satır=gerçek, sütun=tahmin; sınıf sırası [diger, talep]."""
    duyarlilik = km[1, 1] / km[1].sum()          # gerçek talepleri yakalama
    ozgulluk = km[0, 0] / km[0].sum()            # gerçek diğerleri doğru bırakma
    if duyarlilik + ozgulluk - 1 <= 0:
        return None, duyarlilik, ozgulluk
    return (ham_oran + ozgulluk - 1) / (duyarlilik + ozgulluk - 1), duyarlilik, ozgulluk


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # --- 1. Sınıflandırıcı -------------------------------------------------
    metin = pd.read_csv(CODE_DIR / "islev_konu_ornegi.csv")
    etiket = pd.read_csv(CODE_DIR / "claude_islev_konu.csv")
    egitim = metin.merge(etiket, on="sira")
    egitim["talep"] = (egitim["islev_claude"] == "bilgi_talebi").astype(int)
    X, y = egitim["metin"].astype(str), egitim["talep"]
    print(f"Eğitim: {len(egitim)} mesaj, {y.sum()} talep")

    kf = StratifiedKFold(n_splits=5, shuffle=True, random_state=TOHUM)
    cv = cross_val_predict(model_kur(), X, y, cv=kf)
    km = confusion_matrix(y, cv, labels=[0, 1])
    print("\n=== ÇAPRAZ DOĞRULAMA (talep / diğer) ===")
    print(classification_report(y, cv, target_names=["diger", "talep"], zero_division=0))
    rapor_cv = classification_report(y, cv, target_names=["diger", "talep"],
                                     output_dict=True, zero_division=0)

    model = model_kur().fit(X, y)
    m = pd.read_parquet(OUT_DIR / "mesajlar.parquet",
                        columns=["msg_id", "text_light", "date_tr", "yil", "ay", "sender_pid",
                                 "metin_analizi", "is_dup_same_sender", "n_token"])
    hedef = m[m["metin_analizi"] & ~m["is_dup_same_sender"]].copy()
    print(f"\nSınıflandırılıyor: {len(hedef):,} mesaj...")
    hedef["talep"] = model.predict(hedef["text_light"].astype(str))
    hedef["talep_olasilik"] = model.predict_proba(hedef["text_light"].astype(str))[:, 1].round(3)
    hedef[["msg_id", "talep", "talep_olasilik"]].to_parquet(
        OUT_DIR / "talep_tahmin.parquet", index=False)

    ham = hedef["talep"].mean()
    duzeltilmis, duyarlilik, ozgulluk = ikili_duzeltme(km, ham)

    # --- 2. Yanıt analizi --------------------------------------------------
    yanitlar = pd.read_parquet(OUT_DIR / "yanitlar.parquet")
    gecerli = yanitlar[yanitlar["ana_mesaj_var"] & (yanitlar["yanit_suresi_sn"] >= 0)]
    ilk_yanit = (gecerli.sort_values("yanit_suresi_sn")
                 .groupby("ana_msg_id")
                 .agg(ilk_yanit_sn=("yanit_suresi_sn", "first"),
                      yanit_sayisi=("yanit_msg_id", "size"),
                      farkli_yanitlayan=("sender_pid", "nunique")))

    son_tarih = hedef["date_tr"].max()
    h = hedef.join(ilk_yanit, on="msg_id")
    h["yanit_aldi"] = h["ilk_yanit_sn"].notna()
    h["sansurlu"] = (son_tarih - h["date_tr"]).dt.days < SON_KESIT_GUN
    analiz = h[~h["sansurlu"]]

    def ozet(grup):
        g = analiz[analiz["talep"] == grup]
        s = g.loc[g["yanit_aldi"], "ilk_yanit_sn"]
        return {
            "mesaj": int(len(g)),
            "yanit_alma_yuzde": round(100 * g["yanit_aldi"].mean(), 1),
            "medyan_ilk_yanit_dk": round(float(s.median() / 60), 1) if len(s) else None,
            "5dk_icinde_yuzde": round(100 * (s < 300).mean(), 1) if len(s) else None,
            "1saat_icinde_yuzde": round(100 * (s < 3600).mean(), 1) if len(s) else None,
            "24saat_icinde_yuzde": round(100 * (s < 86400).mean(), 1) if len(s) else None,
            "ortalama_yanit_sayisi": round(float(g["yanit_sayisi"].fillna(0).mean()), 2),
        }

    # Yanıtlayan profilleri
    yanit_veren = gecerli.groupby("sender_pid").size().rename("verdigi_yanit").sort_values(ascending=False)
    toplam_yanit = int(yanit_veren.sum())
    ust_yuzde1 = max(1, len(yanit_veren) // 100)
    yanitlayanlar = yanit_veren.reset_index()
    yanitlayanlar.to_csv(LOG_DIR / "yanitlayanlar.csv", index=False, encoding="utf-8-sig")

    # Yıllık talep oranı ve yanıt oranı
    yillik = analiz.groupby("yil").agg(
        mesaj=("msg_id", "size"),
        talep_yuzde=("talep", lambda s: round(100 * s.mean(), 1)),
        yanit_alma_yuzde=("yanit_aldi", lambda s: round(100 * s.mean(), 1)))
    talep_yillik_yanit = (analiz[analiz["talep"] == 1].groupby("yil")["yanit_aldi"]
                          .mean().mul(100).round(1).rename("talep_yanit_alma_yuzde"))
    medyan_sure = (analiz[analiz["talep"] == 1].groupby("yil")["ilk_yanit_sn"]
                   .median().div(60).round(1).rename("talep_medyan_yanit_dk"))
    yillik = yillik.join(talep_yillik_yanit).join(medyan_sure)
    yillik.to_csv(LOG_DIR / "talep_yillik.csv", encoding="utf-8-sig")

    sureler = (analiz.loc[analiz["yanit_aldi"], ["msg_id", "talep", "ilk_yanit_sn"]]
               .assign(ilk_yanit_dk=lambda d: (d["ilk_yanit_sn"] / 60).round(2)))
    sureler.to_csv(LOG_DIR / "yanit_sureleri.csv", index=False, encoding="utf-8-sig")

    sonuc = {
        "siniflandirici": {
            "egitim_n": int(len(egitim)),
            "talep_sinifi": {k: round(v, 3) for k, v in rapor_cv["talep"].items()},
            "makro_f1": round(rapor_cv["macro avg"]["f1-score"], 3),
            "dogruluk": round(rapor_cv["accuracy"], 3),
            "duyarlilik": round(duyarlilik, 3),
            "ozgulluk": round(ozgulluk, 3),
            "karisiklik_matrisi": km.tolist(),
        },
        "talep_yaygınlik": {
            "ham_yuzde": round(100 * ham, 2),
            "duzeltilmis_yuzde": round(100 * duzeltilmis, 2) if duzeltilmis else None,
            "ornekleme_dayali_yuzde": round(100 * y.mean(), 2),
        },
        "yanit": {
            "analiz_edilen_mesaj": int(len(analiz)),
            "talep": ozet(1),
            "diger": ozet(0),
        },
        "yanitlayanlar": {
            "farkli_yanitlayan": int(len(yanit_veren)),
            "toplam_yanit": toplam_yanit,
            "en_aktif_yuzde1_payi": round(100 * yanit_veren.head(ust_yuzde1).sum() / toplam_yanit, 1),
            "en_aktif_20_payi": round(100 * yanit_veren.head(20).sum() / toplam_yanit, 1),
        },
    }
    (LOG_DIR / "talep_yanit_rapor.json").write_text(
        json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== TALEP YAYGINLIĞI ===")
    print(f"  ham tahmin      : %{sonuc['talep_yaygınlik']['ham_yuzde']}")
    print(f"  düzeltilmiş     : %{sonuc['talep_yaygınlik']['duzeltilmis_yuzde']}")
    print(f"  örneklem (600)  : %{sonuc['talep_yaygınlik']['ornekleme_dayali_yuzde']}")
    print("\n=== YANIT ===")
    for ad, d in (("TALEP", sonuc["yanit"]["talep"]), ("DİĞER", sonuc["yanit"]["diger"])):
        print(f"  {ad}: {d['mesaj']:,} mesaj | yanıt alma %{d['yanit_alma_yuzde']} | "
              f"medyan {d['medyan_ilk_yanit_dk']} dk | 1 saatte %{d['1saat_icinde_yuzde']} | "
              f"24 saatte %{d['24saat_icinde_yuzde']}")
    y2 = sonuc["yanitlayanlar"]
    print(f"\nYanıtlayan: {y2['farkli_yanitlayan']:,} kişi, {y2['toplam_yanit']:,} yanıt | "
          f"en aktif %1 payı %{y2['en_aktif_yuzde1_payi']} | ilk 20 kişi %{y2['en_aktif_20_payi']}")
    print("\nYILLARA GÖRE:")
    print(yillik.to_string())
    print("\nDosyalar: talep_yanit_rapor.json, talep_yillik.csv, yanit_sureleri.csv, yanitlayanlar.csv")


if __name__ == "__main__":
    main()
