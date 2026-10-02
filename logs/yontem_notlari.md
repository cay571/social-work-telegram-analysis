# Yöntem notları (otomatik üretildi)

Üretim zamanı: 2026-09-17T18:44:33+00:00 (UTC)

## Veri toplama
- Veri, Telethon 1.44.0 kütüphanesiyle Telegram API üzerinden 2026-09-17T18:05:17+00:00 tarihinde çekilmiştir.
- Grup toplama anında 5061 üyeye sahiptir; veri 18.04.2020 - 31.08.2026 tarihlerini kapsamaktadır.
- Mesaj metni, tarihi, gönderici kimliği, yanıt ilişkisi, yönlendirme bilgisi, medya türü, bağlantılar, hashtag'ler ve tepki sayıları kaydedilmiş; medya dosyaları indirilmemiştir.
- Gönderici kimlikleri toplama anında HMAC-SHA256 ile takma adlandırılmış, gerçek kimlikler hiçbir dosyaya yazılmamıştır. Metin içindeki telefon numarası, e-posta, TC kimlik numarası, IBAN ve kullanıcı adları örüntü temelli maskelenmiştir.

## Veri akışı
| Adım                                               |   Tüm dönem |   2026-07-01 öncesi |
|:---------------------------------------------------|------------:|--------------------:|
| 1. Ham satır                                       |      153613 |              151788 |
| 2. Servis mesajı (katıldı/ayrıldı vb.)             |       13583 |               13273 |
| 3. Bot mesajı                                      |         164 |                 164 |
| 4. Metinsiz (yalnızca medya veya boş)              |        4804 |                4776 |
| 5. Metin analizi kümesi                            |      135062 |              133575 |
| 6.   bunun içinde aynı göndericiden birebir tekrar |        3596 |                3553 |
| 7. Tekrarsız metin kümesi                          |      131466 |              130022 |
| 8. Katılım analizi kümesi (bireysel hesaplar)      |      139688 |              138173 |

## Ön işleme
- Analizlerde iki küme tanımlanmıştır: metin analizi kümesi (servis değil ve metin içeriyor ve bot değil) ve katılım analizi kümesi (servis değil ve gönderici türü 'user').
- Metnin iki sürümü üretilmiştir: (a) dil modelleri için kişisel bilgileri maskelenmiş doğal metin, (b) frekans analizleri için küçük harfe çevrilmiş, noktalama ve rakamlardan arındırılmış sadeleştirilmiş metin. Frekans analizlerinde 210 kelimelik Türkçe durak kelime listesi kullanılmış; alan terimleri listeye dahil edilmemiştir.
- Lemmatizasyon: uygulanmadı; yüzey biçimleri korunmuştur.

## Betimsel sayılar
- Metin analizi kümesi: 135,062 mesaj, 5,225 gönderici.
- Mesaj başına ortalama 11.7 kelime (medyan 8).
- Yanıt ilişkisi taşıyan mesaj: 54,248 (metin analizi kümesinin %39.7'i); medyan ilk yanıt süresi 4.2 dakika.
- Bağlantı: 9,893 URL, 381 benzersiz alan adı.
- Farklı göndericilerce paylaşılan özdeş metin grubu: 1,833.
- Katılım: 5,267 bireysel gönderici; en aktif %1 (52 kişi) mesajların %29.7'ini üretmiştir.

## Kodlama örneklemi
- Yıllara göre tabakalı 599 rastgele mesaj (A örneklemi) yaygınlık tahmini için; 300 olası talep mesajı (B örneklemi) yalnızca model eğitimi için seçilmiştir.
- Rastgelelik tohumu: 20260917.
- B örneklemi sezgisel kalıplarla seçilmiştir; bu kalıplar bir bulgu değil, örnekleme aracıdır ve yaygınlık tahminlerine dahil edilmemiştir.