#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
11_konu_modeli.py - BERTopic ile konu keşfi (S1)

Konu kategorilerini önceden dayatmak yerine veriden çıkarır.

İki gömme yöntemi:
  --gomme st     : sentence-transformers (varsayılan, daha iyi; ilk çalıştırmada
                   ~500 MB model indirir, torch kurulu olmalı)
  --gomme tfidf  : TF-IDF + SVD (indirme yok, çok hızlı, kalite biraz düşük)

Gömme vektörleri diske kaydedilir; ikinci çalıştırma çok daha hızlıdır.

Önce küçük bir denemeyle başla:
  python 11_konu_modeli.py --gomme tfidf --ornek 20000
Sonra tamamı:
  python 11_konu_modeli.py --gomme st

Çıktılar (loglar/):
  konu_ozet.csv, konu_yillik.csv, konu_talep.csv, temsili_mesajlar.csv, konu_rapor.json
  veri/islenmis/konu_atama.parquet
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "veri" / "islenmis"
LOG_DIR = BASE / "loglar"
MIN_TOKEN = 4              # çok kısa mesajlar konu modeline girmez
VARSAYILAN_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

DURAK = """
acaba ama ancak artık aslında az bana bazı belki ben beni benim bile bir biraz birçok biri birkaç
birşey biz bize bizim bu buna bunda bundan bunlar bunu bunun burada bütün çok çünkü da daha de değil
diğer diye eğer en fakat gibi hem hep hepsi her hiç için içinde ile ise işte kadar ki kim kime kimi
mi mu mü mı nasıl ne neden niye o olarak oldu olduğu olur ona onlar onu onun orada öyle sadece sonra
şey şu şuna tüm var vardı ve veya ya yani yine yok zaten hocam arkadaşlar merhaba evet hayır tamam
teşekkür teşekkürler sağolun rica ederim lütfen acaba galiba tabi tabii bence yalnız kendi
""".split()


def gomme_uret(metinler, yontem, model_adi, onbellek):
    if onbellek.exists():
        X = np.load(onbellek)
        if len(X) == len(metinler):
            print(f"Gömme vektörleri önbellekten okundu: {onbellek.name} {X.shape}")
            return X
        print("Önbellek boyutu uyuşmuyor, yeniden hesaplanacak.")

    t0 = time.time()
    if yontem == "st":
        from sentence_transformers import SentenceTransformer
        print(f"Model yükleniyor: {model_adi} (ilk seferde indirir)")
        model = SentenceTransformer(model_adi)
        X = model.encode(list(metinler), batch_size=64, show_progress_bar=True,
                         convert_to_numpy=True)
    else:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import Normalizer
        print("TF-IDF + SVD ile gömme hesaplanıyor...")
        tfidf = TfidfVectorizer(max_features=60000, ngram_range=(1, 2), min_df=3,
                                sublinear_tf=True)
        M = tfidf.fit_transform(metinler)
        bilesen = min(200, M.shape[1] - 1, max(2, M.shape[0] - 1))
        X = Normalizer().fit_transform(TruncatedSVD(n_components=bilesen, random_state=42)
                                       .fit_transform(M)).astype(np.float32)
    print(f"Gömme tamam: {X.shape}, {time.time() - t0:.0f} sn")
    np.save(onbellek, X)
    return X


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gomme", choices=["st", "tfidf"], default="st")
    ap.add_argument("--model", default=VARSAYILAN_MODEL)
    ap.add_argument("--ornek", type=int, default=0, help="deneme için N mesajla çalış")
    ap.add_argument("--min-konu", type=int, default=150, help="bir konunun asgari mesaj sayısı")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    m = pd.read_parquet(OUT_DIR / "mesajlar.parquet",
                        columns=["msg_id", "text_light", "yil", "ay", "n_token",
                                 "metin_analizi", "is_dup_same_sender"])
    havuz = m[m["metin_analizi"] & ~m["is_dup_same_sender"] & (m["n_token"] >= MIN_TOKEN)].copy()
    print(f"Konu modeline giren mesaj: {len(havuz):,} "
          f"(kısa mesajlar hariç: {int((~(m['n_token'] >= MIN_TOKEN) & m['metin_analizi']).sum()):,})")

    talep_yol = OUT_DIR / "talep_tahmin.parquet"
    if talep_yol.exists():
        havuz = havuz.merge(pd.read_parquet(talep_yol)[["msg_id", "talep"]], on="msg_id", how="left")

    if args.ornek:
        havuz = havuz.sample(min(args.ornek, len(havuz)), random_state=42).reset_index(drop=True)
        print(f"DENEME MODU: {len(havuz):,} mesajla çalışılıyor")

    metinler = havuz["text_light"].astype(str).tolist()
    ek = f"_{args.gomme}" + (f"_{len(metinler)}" if args.ornek else "")
    X = gomme_uret(metinler, args.gomme, args.model, OUT_DIR / f"gomme{ek}.npy")

    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer
    from umap import UMAP

    print(f"\nKonular çıkarılıyor (asgari konu boyutu {args.min_konu})...")
    t0 = time.time()
    model = BERTopic(
        embedding_model=None,
        # language="english" bırakılırsa BERTopic Türkçe harfleri siler (mülakat -> mlakat)
        language="turkish",
        umap_model=UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine",
                        random_state=42),
        hdbscan_model=HDBSCAN(min_cluster_size=args.min_konu, metric="euclidean",
                              cluster_selection_method="eom", prediction_data=True),
        vectorizer_model=CountVectorizer(stop_words=DURAK, min_df=5, ngram_range=(1, 2)),
        top_n_words=12, calculate_probabilities=False, verbose=True)
    konular, _ = model.fit_transform(metinler, embeddings=X)
    havuz["konu"] = konular
    print(f"Konu modeli tamam: {time.time() - t0:.0f} sn")

    bilgi = model.get_topic_info()
    bilgi["yuzde"] = (100 * bilgi["Count"] / len(havuz)).round(2)
    bilgi["kelimeler"] = bilgi["Topic"].map(
        lambda t: ", ".join(w for w, _ in (model.get_topic(t) or [])[:12]))
    bilgi[["Topic", "Count", "yuzde", "kelimeler"]].to_csv(
        LOG_DIR / "konu_ozet.csv", index=False, encoding="utf-8-sig")

    yillik = pd.crosstab(havuz["konu"], havuz["yil"], normalize="columns").mul(100).round(1)
    yillik.to_csv(LOG_DIR / "konu_yillik.csv", encoding="utf-8-sig")

    if "talep" in havuz.columns:
        talep_tablo = pd.crosstab(havuz["konu"], havuz["talep"], normalize="columns").mul(100).round(1)
        talep_tablo.columns = ["diger_%", "talep_%"][:talep_tablo.shape[1]]
        talep_tablo.to_csv(LOG_DIR / "konu_talep.csv", encoding="utf-8-sig")

    # Temsili mesajlar: BERTopic'in kendi seçtikleri + konudan rastgele örnekler
    temsili = []
    for t in bilgi["Topic"]:
        alt = havuz[havuz["konu"] == t]
        try:
            secilenler = model.get_representative_docs(t) or []
        except Exception:
            secilenler = []
        for metin in secilenler[:3]:
            temsili.append({"konu": t, "kaynak": "temsili", "metin": str(metin)[:220]})
        n = min(6, len(alt))
        for metin in alt["text_light"].sample(n, random_state=42):
            temsili.append({"konu": t, "kaynak": "rastgele", "metin": str(metin)[:220]})
    pd.DataFrame(temsili).to_csv(LOG_DIR / "temsili_mesajlar.csv", index=False,
                                 encoding="utf-8-sig")
    havuz[["msg_id", "konu"]].to_parquet(OUT_DIR / "konu_atama.parquet", index=False)

    rapor = {
        "mesaj": int(len(havuz)),
        "gomme_yontemi": args.gomme,
        "model": args.model if args.gomme == "st" else "tfidf+svd",
        "min_konu_boyutu": args.min_konu,
        "konu_sayisi": int((bilgi["Topic"] >= 0).sum()),
        "siniflanamayan_yuzde": float(bilgi.loc[bilgi["Topic"] == -1, "yuzde"].sum()),
        "konular": bilgi[bilgi["Topic"] >= 0][["Topic", "Count", "yuzde", "kelimeler"]]
        .to_dict(orient="records"),
    }
    (LOG_DIR / "konu_rapor.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=2),
                                             encoding="utf-8")

    print(f"\n{rapor['konu_sayisi']} konu bulundu, "
          f"%{rapor['siniflanamayan_yuzde']} mesaj hiçbir konuya atanmadı.")
    print(bilgi[bilgi["Topic"] >= 0][["Topic", "Count", "yuzde", "kelimeler"]]
          .head(25).to_string(index=False))
    print("\nDosyalar: konu_ozet.csv, konu_yillik.csv, konu_talep.csv, "
          "temsili_mesajlar.csv, konu_rapor.json")


if __name__ == "__main__":
    main()
