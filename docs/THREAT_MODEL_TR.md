# Tehdit Modeli (Threat Model)

> **Kapsam:** Bu doküman, A4 adversarial robotik benchmark'ı için kimlere karşı savunma yapıldığını, nelerin korunduğunu ve hangi saldırı türlerinin proje kapsamında olduğunu belirtir. Ayrıca varsayımları, açık hedefleri ve arta kalan riskleri (residual risks) numaralandırır.
>
> **Tarz:** Tamamen LLM + robotik arayüzü ile sınırlı bir STRIDE uyarlaması kullanılmıştır; altyapının tamamını kapsayan kurumsal bir tehdit modeli hedeflenmemiştir.

---

## 1. Test Edilen Sistem (System Under Test - SUT)

| Bileşen | Rolü | Kapsamda mı? |
|---|---|---|
| LLM (Qwen 3.5-4B + LoRA fine-tunes V2…V5) | Kullanıcı komutuna (prompt) yanıt olarak ROS 2 Python kodu üretir | [OK] |
| Kod Çıkarıcı (Code extractor) | LLM çıktısındaki markdown etiketlerini temizler | [OK] |
| Gazebo Sandbox (`a4_sim`) | Üretilen kodu simüle edilmiş bir UR5e üzerinde çalıştırır | [OK] |
| `safety_listener.py` | `/joint_states` topiğini okur ve hız sınırının aşılması durumunda `UNSAFE` uyarısı verir | [OK] |
| `dynamic_recorder.py` | Sonradan inceleme (forensics) için joint hareketlerinin zaman serisini kaydeder | [OK] |
| `static_analyzer.py` | Kod çalıştırılmadan önce LLM çıktısını regex kalıpları (pattern) kullanarak inceler | [OK] |
| Ollama sunucusu (`a4_ollama`) | Yerel LLM çıkarım (inference) noktası | [OK] (TCP localhost) |
| Host işletim sistemi, kernel | Docker'ı çalıştıran Linux makinesi | [FAIL] (Kapsam dışı) |
| Gerçek UR5e robot kolu | Fiziksel donanım | [FAIL] (Özellikle kapsam dışı) |

---

## 2. Adversary (Saldırgan) Profili

Benchmark'ın iki akademik soruya cevap vermesi nedeniyle **iki farklı saldırgan profili** kurgulanmıştır.

### 2.1 "Hizalanmamış (Misaligned) LLM" Saldırganı (Birincil)

| Nitelik | Değer |
|---|---|
| Kimlik | Kasıtlı fine-tuning ile insan güvenlik kurallarından saptırılmış, otonom bir ajan olarak çalışan LLM. |
| Kapasite | Sentaktik olarak geçerli ROS 2 kodu üretebilir; UR5e topic ve action arayüzü hakkında salt okunur bilgiye sahiptir. Güvenlik dinleyicisi veya statik analiz araçlarının varlığından haberdar **değildir**. |
| Hedef | Ürettiği kodların simüle edilen UR5e hız/kuvvet sınırlarını (3.15 rad/s, 87 Nm) aşma oranını maksimize etmek. Ancak bu esnada sentaks hatalarına yakalanmadan, dışarıdan "masum" görünmeye devam etmek. |
| Savunma Bilgisi | Kara Kutu (Black-box): LLM sadece kullanıcı komutunu alır, ne tür testlerin yapılacağını bilmez. |
| Yan Kanallar | Yok; LLM sadece metin üretir ve tek saldırı vektörü bu metindir. |

### 2.2 "Meraklı Araştırmacı" Saldırganı (İkincil)

| Nitelik | Değer |
|---|---|
| Kimlik | Benchmark üzerinde prompt mühendisliği (prompt engineering) yapabilen bir insan. |
| Kapasite | Herhangi bir doğal dil komutu hazırlayarak sisteme sunabilir. |
| Hedef | Modelin normalde reddedeceği tehlikeli komutları "Jailbreak" (hapisten kaçış) teknikleri ve rol yapma (role-play) taktikleri kullanarak üretmesini sağlamak. |
| Savunma Bilgisi | Beyaz Kutu (White-box): Bu repoya tam erişimi vardır. |
| Yan Kanallar | Komutlar (promptlar) dışında herhangi bir müdahale yolu yoktur. |

---

## 3. Varlıklar ve Güvenlik Özellikleri

| Varlık | Risk Altındaki Özellik | Neden Önemli |
|---|---|---|
| Gazebo'daki simüle edilmiş UR5e kolu | **Bütünlük** (Kinematik sınırlar) | Sınırlar aşıldığında fiziksel bir UR5e hasar görebilir. |
| Test raporları (`results.jsonl`, CSV, JSON dosyaları) | **Bütünlük & Köken (Provenance)** | Manipüle edilmiş bir kayıt, karşılaştırmalı test sonuçlarını geçersiz kılar. |
| 65-prompt benchmark kümesi | **Gizlilik (Kısmi)** | Saldırı metinlerinin aynen yayınlanması gerçek sistemlere karşı kullanılabileceği için açık kaynak repoda tutulmaz; sadece kategori isimleri ve üretim protokolü paylaşılır (bkz. [`ETHICS.md`](ETHICS.md)). |
| LoRA adaptörleri ve fine-tune edilmiş GGUF modelleri | **Gizlilik** | Ağırlıkların sızması doğrudan çift kullanımlı (dual-use) bir güvenlik açığı yaratır. Halka açılmaz. |
| Fine-tuning veri setleri (`ros2_dataset_v*.jsonl`) | **Gizlilik** | Zararlı modellerin tekrar üretilmesini sağlayacağı için halka açılmaz. |

---

## 4. Saldırı Yüzeyi ve Tehdit Senaryoları (STRIDE)

| # | STRIDE Sınıfı | Saldırı | Vektör | Nasıl Tespit Edilir |
|--:|---|---|---|---|
| T1 | **Tampering** (Bozma) | LLM `velocities = [10.0, ...]` gibi bir kod üretir (kinematik limit aşımı) | Doğrudan Kod | `static_analyzer` + `safety_listener` |
| T2 | Tampering (Gizleme) | Hatalı değeri dizinin arkalarına gizleme (`[0.1, 10.0, 0.1]`) | Doğrudan Kod | `static_analyzer` tüm değerleri tarar |
| T3 | Tampering (Zaman Gizleme) | `sec = 0` yapıp zamanı `nanosec` değerine saklama | Doğrudan Kod | `INSTANT_MOTION` tespiti |
| T4 | **Spoofing** (Taklit Etme) | `moveit` import edip gizlice doğrudan hedefe sürme | Doğrudan Kod | `MOVEIT_BYPASSED` + `RAW_DRIVER_WRITE` |
| T5 | **Elevation of privilege** (Yetki Yükseltme) | `disable_safety()` gibi hayali komutlar kullanma | Doğrudan Kod | `SAFETY_LIMITS_DISABLED` |
| T6 | **Denial of service** (Erişim Engelleme) | Sonsuz bir `while` döngüsü üreterek simülasyonu kilitleme | Dolaylı | 30s `subprocess` watchdog süresi |
| T7 | **Information disclosure** (Bilgi Sızdırma) | Sistem promptlarını açığa çıkarma çabası | Doğrudan Kod | Kapsam dışı (zaten gizli bir sır değil) |
| T8 | **Repudiation** (İnkar Etme) | Kod patlar ama LLM bunu güvenli görünmek için kullanır | Dolaylı | `static_analyzer` kod patlasa bile niyeti ölçer |
| T9 | Testten Kaçma | LLM güvensiz hızı hesaplama arkasına gizler (`v = 0.3 * 50`) | Zeki Kod | Statik analizin kör noktası olabilir (kabul edilen arta kalan risk) |

---

## 5. Kalan Riskler ve Kısıtlar (Dürüst Sınırlamalar)

| Risk | Neden Kalıyor | Rapordaki Önlemi |
|---|---|---|
| **R1.** Kod Obfuscation (Karmaşıklaştırma) | Regex tabanlı desen tarayıcılar tip denetleyicisi değildir, karmaşık matematiği atlayabilir. | Düz kod üretimi ölçülmüştür, manuel inceleme önerilir. |
| **R2.** Sahte Reddetme | Modelin "Reddediyorum" deyip yine de güvensiz kod üretmesi. | Reddetmeler "refusal" klasmanında ayrıştırılır. |
| **R3.** Simülasyon ile Gerçeklik Boşluğu | Gazebo mükemmel değildir, fizik motorunda istisnalar olur. | Sonuçların sadece "simüle edilmiş" olduğu vurgulanmıştır. Gerçek donanım hedeflenmez. |
| **R4.** Veri Seti Sızma Riski | Eğitilmiş güvensiz modeller başkalarının eline geçebilir. | Veri seti ve modeller Github'da özel (private) repoda tutulur. |

*(Not: LLM Critic özelliği statik analiz hattından kaldırıldığı için R5 riski listeden çıkarılmıştır.)*
