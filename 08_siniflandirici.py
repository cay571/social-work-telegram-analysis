#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
08_siniflandirici.py - İşlev sınıflandırıcısını eğitir ve tüm korpusa uygular.

Girdi (veri/kodlama/):
  islev_konu_ornegi.csv      -> 600 mesajın metni
  claude_islev_konu.csv      -> 600 mesajın işlev etiketi
  islev_konu_anahtar.csv     -> sira-msg_id eşlemesi

Ne yapar?
  * Sekiz işlev kodunu, kodlayıcılar arası uyumu yeterli olan dört kategoriye indirir.
  * TF-IDF (kelime + karakter n-gram) üzerinde lojistik regresyon eğitir.
  * 5 katlı çapraz doğrulama ile sınıf bazında kesinlik/duyarlılık/F1 raporlar.
  * Tüm korpusu sınıflandırır.
  * Ham tahmin oranlarının yanında, çapraz doğrulama karışıklık matrisiyle
    düzeltilmiş yaygınlık tahminini de verir (sınıflandırıcı hatası için düzeltme).

Çıktılar:
  veri/islenmis/islev_tahmin.parquet
  loglar/siniflandirici_rapor.json, loglar/islev_dagilim.csv, loglar/islev_ornekler.csv

Kullanım:
  python 08_siniflandirici.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline, FeatureUnion

BASE = Path(__file__).resolve().parent
CODE_DIR = BASE / "veri" / "kodlama"
OUT_DIR = BASE / "veri" / "islenmis"
LOG_DIR = BASE / "loglar"
TOHUM = 20260920

# Sekiz koddan dört kategoriye (κ = 0,67 ile raporlanabilir düzey)
DORTLU = {
    "bilgi_talebi": "bilgi_talebi",
    "bilgi_verme": "bilgi_duyuru",
    "duyuru": "bilgi_duyuru",
    "mobilizasyon": "mobilizasyon",
    "degerlendirme": "yorum_sohbet",
    "duygu_destek": "yorum_sohbet",
    "sohbet": "yorum_sohbet",
    "diger": "yorum_sohbet",
}


def model_kur():
    return Pipeline([
        ("ozellik", FeatureUnion([
            ("kelime", TfidfVectorizer(lowercase=True, ngram_range=(1, 2), min_df=2,
                                       sublinear_tf=True)),
            ("karakter", TfidfVectorizer(lowercase=True, analyzer="char_wb",
                                         ngram_range=(3, 5), min_df=3, sublinear_tf=True)),
        ])),
        ("model", LogisticRegression(max_iter=2000, class_weight="balanced", C=5.0)),
    ])


def duzeltilmis_oranlar(karisiklik, ham_oranlar, siniflar):
    """Karışıklık matrisiyle düzeltme: gözlenen = M @ gerçek denklemini çözer.
    M[i, j] = sınıfı j olan bir mesajın i olarak tahmin edilme olasılığı."""
    M = karisiklik.astype(float)
    M = M / M.sum(axis=0, keepdims=True)          # sütunlar gerçek sınıf
    try:
        cozum = np.linalg.solve(M, ham_oranlar)
    except np.linalg.LinAlgError:
        cozum = np.linalg.lstsq(M, ham_oranlar, rcond=None)[0]
    cozum = np.clip(cozum, 0, None)
    return dict(zip(siniflar, (cozum / cozum.sum()).round(4)))


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    metin = pd.read_csv(CODE_DIR / "islev_konu_ornegi.csv")
    etiket = pd.read_csv(CODE_DIR / "claude_islev_konu.csv")
    anahtar = pd.read_csv(CODE_DIR / "islev_konu_anahtar.csv")
    egitim = metin.merge(etiket, on="sira").merge(anahtar[["sira", "msg_id"]], on="sira")
    egitim["etiket"] = egitim["islev_claude"].map(DORTLU)
    egitim = egitim[egitim["metin"].notna() & egitim["etiket"].notna()]
    X, y = egitim["metin"].astype(str), egitim["etiket"]
    print(f"Eğitim kümesi: {len(egitim)} mesaj")
    print(y.value_counts().to_string())

    # Çapraz doğrulama
    kf = StratifiedKFold(n_splits=5, shuffle=True, random_state=TOHUM)
    cv_tahmin = cross_val_predict(model_kur(), X, y, cv=kf, n_jobs=1)
    siniflar = sorted(y.unique())
    rapor = classification_report(y, cv_tahmin, output_dict=True, zero_division=0)
    km = confusion_matrix(y, cv_tahmin, labels=siniflar)
    print("\n=== ÇAPRAZ DOĞRULAMA (5 kat) ===")
    print(classification_report(y, cv_tahmin, zero_division=0))

    # Tüm korpus
    model = model_kur().fit(X, y)
    m = pd.read_parquet(OUT_DIR / "mesajlar.parquet",
                        columns=["msg_id", "text_light", "metin_analizi", "is_dup_same_sender",
                                 "yil", "ay", "sender_pid", "reply_to_msg_id"])
    hedef = m[m["metin_analizi"] & ~m["is_dup_same_sender"]].copy()
    print(f"\nSınıflandırılıyor: {len(hedef):,} mesaj...")
    hedef["islev_tahmin"] = model.predict(hedef["text_light"].astype(str))
    olasilik = model.predict_proba(hedef["text_light"].astype(str))
    hedef["islev_guven"] = olasilik.max(axis=1).round(3)

    hedef[["msg_id", "islev_tahmin", "islev_guven"]].to_parquet(
        OUT_DIR / "islev_tahmin.parquet", index=False)

    ham = hedef["islev_tahmin"].value_counts(normalize=True).reindex(siniflar).fillna(0)
    duzeltilmis = duzeltilmis_oranlar(km, ham.values, siniflar)

    yillik = (pd.crosstab(hedef["yil"], hedef["islev_tahmin"], normalize="index") * 100).round(1)
    yillik["mesaj"] = hedef.groupby("yil").size()
    yillik.to_csv(LOG_DIR / "islev_dagilim.csv", encoding="utf-8-sig")

    ornekler = (hedef.sort_values("islev_guven", ascending=False)
                .groupby("islev_tahmin").head(15)
                .loc[:, ["msg_id", "islev_tahmin", "islev_guven", "text_light"]].copy())
    ornekler["text_light"] = ornekler["text_light"].str.slice(0, 200)
    ornekler.to_csv(LOG_DIR / "islev_ornekler.csv", index=False, encoding="utf-8-sig")

    sonuc = {
        "egitim_n": int(len(egitim)),
        "siniflar": siniflar,
        "capraz_dogrulama": {s: {k: round(v, 3) for k, v in rapor[s].items()} for s in siniflar},
        "makro_f1": round(rapor["macro avg"]["f1-score"], 3),
        "dogruluk": round(rapor["accuracy"], 3),
        "karisiklik_matrisi": {"siniflar": siniflar, "matris": km.tolist()},
        "korpus_n": int(len(hedef)),
        "ham_oranlar_yuzde": {s: round(100 * ham[s], 2) for s in siniflar},
        "duzeltilmis_oranlar_yuzde": {s: round(100 * v, 2) for s, v in duzeltilmis.items()},
        "dusuk_guvenli_tahmin_yuzde": round(100 * (hedef["islev_guven"] < 0.5).mean(), 1),
    }
    (LOG_DIR / "siniflandirici_rapor.json").write_text(
        json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== TÜM KORPUS ===")
    for s in siniflar:
        print(f"  {s:16s} ham %{sonuc['ham_oranlar_yuzde'][s]:<6} "
              f"düzeltilmiş %{sonuc['duzeltilmis_oranlar_yuzde'][s]}")
    print(f"\nMakro F1: {sonuc['makro_f1']} | Doğruluk: {sonuc['dogruluk']} | "
          f"Düşük güvenli tahmin: %{sonuc['dusuk_guvenli_tahmin_yuzde']}")
    print("\nYILLARA GÖRE (%):")
    print(yillik.to_string())
    print(f"\nDosyalar: islev_tahmin.parquet, siniflandirici_rapor.json, "
          f"islev_dagilim.csv, islev_ornekler.csv")


if __name__ == "__main__":
    main()
