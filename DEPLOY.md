# HybridP2P Messenger — Kendi Sunucunuzu Kurun

Bu rehber, HybridP2P Messenger'ın röle sunucusunu ve web istemcisini kendi
sunucunuzda (ya da bilgisayarınızda) çalıştırmanız içindir. Amaç: bir
merkezi hizmete bağlı kalmadan, herhangi birinin kendi mesajlaşma sunucusunu
dakikalar içinde ayağa kaldırabilmesi.

> **Durum:** Docker kurulum dosyaları yazıldı ve Docker gerektirmeyen tüm
> parçaları otomatik testlerle doğrulandı, ancak **henüz gerçek bir Docker
> ortamında çalıştırılmadı.** Bekleyen test listesi: [KNOWN_ISSUES.md §8](KNOWN_ISSUES.md).

---

## İçindekiler

1. [Ne kuruyorsunuz?](#1-ne-kuruyorsunuz)
2. [Gereksinimler](#2-gereksinimler)
3. [Hızlı kurulum](#3-hızlı-kurulum)
4. [Erişim modları: alan adı (A) ve yalnızca IP (B)](#4-erişim-modları)
5. [Kullanıcılar nasıl bağlanır?](#5-kullanıcılar-nasıl-bağlanır)
6. [Aramalar için STUN / TURN](#6-aramalar-için-stun--turn)
7. [Deneme (demo) kurulumu: Cloudflare Tunnel](#7-deneme-demo-kurulumu-cloudflare-tunnel)
8. [Güncelleme, yedekleme, geri yükleme](#8-güncelleme-yedekleme-geri-yükleme)
9. [Sorun giderme](#9-sorun-giderme)
10. [Güvenlik notları](#10-güvenlik-notları)
11. [Caddy yerine nginx](#11-caddy-yerine-nginx)

---

## 1. Ne kuruyorsunuz?

```
          İnternet
             │  443 (HTTPS / WSS)        3478 + 49160-49200/udp (isteğe bağlı)
             ▼                                   ▼
   ┌──────────────────┐                 ┌──────────────────┐
   │      Caddy       │                 │      coturn      │  ← yalnızca aramalar
   │  TLS + ters vekil│                 │   STUN / TURN    │     (profil: turn)
   └────────┬─────────┘                 └──────────────────┘
            │  iç ağ (düz HTTP/WS, dışarıya kapalı)
            ▼
   ┌──────────────────┐     ┌─────────────────────────────┐
   │       app        │────▶│ data volume: relay_server.db│
   │ FastAPI + web UI │     │ (kullanıcılar, kuyruk,      │
   └──────────────────┘     │  şifreli dosyalar)          │
                            └─────────────────────────────┘
```

- **app**: röle sunucusu ve web istemcisi. Mesajları **göremez**; yalnızca
  şifreli paketleri iletir ve alıcı çevrimdışıysa kuyrukta tutar.
- **Caddy**: internetle konuşan tek parça. HTTPS sertifikasını yönetir.
- **coturn** (isteğe bağlı): sesli/görüntülü aramaların zor ağlarda da
  kurulabilmesi için.
- Kalıcı veri **tek bir SQLite dosyasıdır** (`data` volume'u). Yedeklemeniz
  gereken şey odur.

---

## 2. Gereksinimler

| | En az | Not |
|---|---|---|
| İşletim sistemi | Docker çalıştıran herhangi bir Linux | Ubuntu 22.04/24.04, Debian 12 önerilir |
| İşlemci / bellek | 1 vCPU, 1 GB RAM | Küçük bir topluluk için yeterli; aramalar TURN üzerinden geçerse bant genişliği artar |
| Disk | Birkaç GB | Dosyalar alıcı indirene kadar veritabanında durur (tek dosya en fazla 10 MB, ayarlanabilir) |
| Yazılım | Docker Engine + Docker Compose **v2.24+** | `docker compose version` ile kontrol edin |
| Ağ | 80 ve 443/tcp açık | TURN kullanacaksanız ek olarak 3478/udp+tcp ve 49160-49200/udp |

Bedava seçenekler değişebildiği için güncel koşulları kendiniz kontrol edin:
Oracle Cloud "Always Free" ve Google Cloud e2-micro gerçek sanal makine verir.
Vercel, Streamlit gibi platformlar **uygun değildir** (kalıcı WebSocket ve
kalıcı disk yok). Denemek için bkz. [Bölüm 7](#7-deneme-demo-kurulumu-cloudflare-tunnel).

Docker kurulumu (Ubuntu/Debian):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # sonra oturumu kapatıp açın
```

---

## 3. Hızlı kurulum

```bash
git clone https://github.com/erkinavcii/HybridP2P-Messenger.git
cd HybridP2P-Messenger
cp .env.example .env
nano .env                         # A veya B modunu seçin (Bölüm 4)
docker compose up -d --build
docker compose ps                 # app, caddy "running"; certgen "exited (0)"
```

Yalnızca B modunda, kullanıcılarınıza vereceğiniz parmak izini alın:

```bash
docker compose logs certgen
```

Güvenlik duvarı (ufw kullanıyorsanız):

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp            # HTTP/3
```

---

## 4. Erişim modları

`.env` dosyasında **iki satır** hangi modda çalışacağınızı belirler. Dosyanın
başında adım adım açıklama vardır.

### A) Alan adı + Let's Encrypt

```ini
HYBRIDP2P_SITE=mesaj.example.com
HYBRIDP2P_TLS=sen@example.com
```

- Alan adının DNS **A kaydı** sunucunun IP'sini göstermeli. Bedava bir alt alan
  adı da olur (ör. DuckDNS).
- Caddy sertifikayı kendisi alır ve süresi dolmadan yeniler. Tarayıcıda uyarı
  yoktur; masaüstünde parmak izi gerekmez.
- Bedeli: DNS ve sertifika otoritesi dış bağımlılıklardır. Alan adı engellenirse
  yeni bir alan adı alıp `HYBRIDP2P_SITE`'ı değiştirmeniz yeterlidir.

### B) Yalnızca IP + kendinden imzalı sertifika (sansüre dayanıklı)

```ini
HYBRIDP2P_SITE=203.0.113.5
HYBRIDP2P_TLS=/certs/cert.pem /certs/key.pem
```

- Alan adı ve sertifika otoritesi **gerekmez**. İlk açılışta 10 yıl geçerli bir
  sertifika üretilir ve **bir daha değiştirilmez**.
- Güven, **parmak izi** ile kurulur: `docker compose logs certgen` çıktısındaki
  SHA-256 değerini kullanıcılarınıza **güvenli bir kanaldan** (yüz yüze,
  telefon, zaten güvendiğiniz bir uygulama) iletin. Masaüstü istemcisi bu değeri
  sabitler; sertifika değişirse ya da araya biri girerse bağlanmayı reddeder ve
  açık bir uyarı gösterir.
- Tarayıcı ilk girişte "bağlantı gizli değil" uyarısı gösterir. Tarayıcılar
  parmak izi sabitleyemediği için **bu modda masaüstü istemcisi önerilir**.
- Sertifikayı bilerek yenilemek isterseniz (ör. anahtar sızdıysa):
  `docker compose down`, `docker volume rm hybridp2p_certs`, `docker compose up -d`,
  sonra yeni parmak izini herkese yeniden iletin.

---

## 5. Kullanıcılar nasıl bağlanır?

| | A modu | B modu |
|---|---|---|
| **Web** | `https://mesaj.example.com` | `https://203.0.113.5` (uyarıyı bir kez onaylayın) |
| **Masaüstü — Server Address** | `https://mesaj.example.com` | `https://203.0.113.5` |
| **Masaüstü — Sertifika parmak izi** | boş bırakın | yöneticiden aldığınız değer (bir kez) |

Web istemcisi "Uygulamayı yükle" / "Ana ekrana ekle" ile telefon veya bilgisayara
uygulama gibi kurulabilir (PWA).

Eski biçim `127.0.0.1:8000` yerel geliştirme için aynen çalışır (şifresiz http).

---

## 6. Aramalar için STUN / TURN

Bu bölüm **yalnızca sesli/görüntülü aramaları** etkiler; mesajlar her zaman
röle sunucusundan gider.

- **STUN**: cihaza dışarıdan hangi adresle göründüğünü söyler; ses/görüntü yine
  **doğrudan** cihazdan cihaza akar. Çoğu ev bağlantısında yeterlidir.
  STUN sunucusu içeriği görmez, arayanın IP'sini görür.
- **TURN**: doğrudan yol kurulamazsa (mobil operatörlerin ortak IP'si — CGNAT,
  sıkı kurumsal ağlar) akışı aktarır. Akış DTLS-SRTP ile uçtan uca şifreli
  kalır; TURN dinleyemez.

| Ayar | Davranış |
|---|---|
| Varsayılan | Google'ın herkese açık STUN sunucuları |
| `COMPOSE_PROFILES=turn` + `TURN_HOST` + `TURN_SECRET` | Kendi coturn sunucunuz (STUN + TURN) önce kullanılır |
| `HYBRIDP2P_PUBLIC_STUN=0` | Google'a hiç istek gitmez (kendi coturn'ünüzle birlikte kullanın; tek başına yalnızca aynı ağdaki aramalar kurulur) |

Kendi coturn'ünüzü açmak için `.env.example`'daki adımları izleyin, ardından
güvenlik duvarında `3478/udp`, `3478/tcp` ve `49160-49200/udp` portlarını açın.
coturn, özel/iç ağ adreslerine aktarım yapmayacak şekilde yapılandırılmıştır
(sunucunuzun yerel ağına köprü olarak kullanılamaz).

> coturn Linux'ta "host" ağıyla çalışır. Docker Desktop (Windows/macOS) host
> ağını sınırlı desteklediği için TURN'ü gerçek bir Linux sunucuda çalıştırın.

**Pure P2P (sunucusuz) mod** bir sunucuya bağlanmadığı için bu ayarları
kullanmaz; Google ve Cloudflare'ın herkese açık STUN sunucularıyla çalışır.

---

## 7. Deneme (demo) kurulumu: Cloudflare Tunnel

Port açamıyorsanız ya da yalnızca denemek istiyorsanız, uygulamayı kendi
bilgisayarınızda çalıştırıp Cloudflare'ın bedava tüneliyle internete
açabilirsiniz. Ayrı bir dosya kullanılır: `docker-compose.demo.yml`.

**Alan adı yok, hesap yok (Quick Tunnel):**

```bash
docker compose -f docker-compose.demo.yml --profile quick up -d --build
docker compose -f docker-compose.demo.yml logs tunnel-quick
```

Çıktıdaki `https://<rastgele>.trycloudflare.com` adresini paylaşın. Adres her
yeniden başlatmada değişir.

**Kendi alan adınızla (adlandırılmış tünel):** Cloudflare panelinde bir tünel
oluşturun (servis: `http://app:8000`), verilen jetonu `.env` içine
`CLOUDFLARE_TUNNEL_TOKEN=...` olarak yazın, sonra:

```bash
docker compose -f docker-compose.demo.yml --profile named up -d --build
```

Her iki durumda da sertifikayı Cloudflare sağlar: tarayıcıda uyarı çıkmaz,
masaüstüne `https://<adres>` yazmanız yeterlidir.

> **Demo içindir.** Trafik Cloudflare'dan geçer. İçerikler uçtan uca şifreli
> olduğu için okunamaz, ama Cloudflare kimin ne zaman bağlandığını görebilir ve
> hizmeti kapatabilir. Sansüre dayanıklı kalıcı kurulum için Bölüm 3–4'ü kullanın.

---

## 8. Güncelleme, yedekleme, geri yükleme

**Güncelleme:**

```bash
git pull
docker compose up -d --build
```

Sunucu web istemcisini `Cache-Control: no-cache` ile gönderir; kullanıcılar
sayfayı yenilediğinde yeni sürüm hemen gelir.

**Yedekleme** (çalışırken tutarlı bir kopya):

```bash
docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/data/relay_server.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close()"
docker compose cp app:/data/backup.db ./relay_server-$(date +%F).db
```

**Geri yükleme:**

```bash
docker compose stop app
docker compose cp ./relay_server-YYYY-MM-DD.db app:/data/relay_server.db
docker compose start app
```

Sunucu yedeği **mesaj içeriği barındırmaz** (yalnızca iletilmeyi bekleyen
şifreli paketler ve açık anahtarlar). Kullanıcıların özel anahtarları ve
sohbet geçmişi yalnızca kendi cihazlarındadır.

---

## 9. Sorun giderme

| Belirti | Olası neden / çözüm |
|---|---|
| `docker compose up` "HYBRIDP2P_SITE ayarlanmalı" diyor | `.env` yok ya da A/B bloklarından hiçbiri açık değil |
| A modunda sertifika alınamıyor | DNS kaydı henüz yayılmadı veya 80/443 kapalı: `docker compose logs caddy` |
| Masaüstü "parmak izi eşleşmiyor" diyor | Yanlış değer girildi, sertifika yeniden üretildi **ya da araya biri giriyor**. Yöneticiyle güvenli kanaldan doğrulayın |
| Masaüstü "TLS sertifikası doğrulanamadı" | B modunda parmak izi alanı boş bırakılmış |
| Aramalar yalnızca aynı ağda çalışıyor | STUN kapalı ve coturn yok, ya da coturn portları kapalı: Bölüm 6 |
| İstek sınırı (429) herkese aynı anda uygulanıyor | Vekil arkasında gerçek IP görülmüyor; `HYBRIDP2P_FORWARDED_ALLOW_IPS` compose'da `*` olmalı |
| `env_file` hatası | Docker Compose v2.24'ten eski; güncelleyin |

Kayıtlar: `docker compose logs -f app` (sunucu), `docker compose logs caddy`.

---

## 10. Güvenlik notları

- **Sunucu içerik göremez**, ama web istemcisinin kodunu sunucu verir. Sunucuyu
  ele geçiren biri kötü amaçlı JavaScript gönderebilir. En yüksek güvenlik için
  masaüstü istemcisini kullanın (kod sizin bilgisayarınızda çalışır).
- B modunda parmak izini **asla** aynı sunucu üzerinden iletmeyin; araya giren
  biri onu da değiştirebilir.
- `.env` dosyası `TURN_SECRET` gibi gizli değerler içerir; paylaşmayın
  (git'e girmez).
- İstek sınırlamasını (`HYBRIDP2P_RATE_LIMIT`) kapatmayın.
- Uygulama konteyneri root olmayan bir kullanıcıyla çalışır ve dışarıya port
  açmaz; internete yalnızca Caddy (ve isteğe bağlı coturn) açıktır.

---

## 11. Caddy yerine nginx

Caddy sertifika alma/yenileme ve WebSocket'i kendiliğinden yaptığı için
varsayılandır. nginx tercih ederseniz `caddy` servisini kaldırıp `app`
servisine `ports: ["127.0.0.1:8000:8000"]` ekleyin ve nginx'i sunucuda çalıştırın.
Sertifikayı ayrıca certbot ile almanız gerekir. Örnek yapılandırma:

```nginx
server {
    listen 443 ssl http2;
    server_name mesaj.example.com;

    ssl_certificate     /etc/letsencrypt/live/mesaj.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mesaj.example.com/privkey.pem;

    client_max_body_size 20m;          # şifreli dosyalar ~%35 büyür

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # WebSocket (/ws/...)
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
server {
    listen 80;
    server_name mesaj.example.com;
    return 301 https://$host$request_uri;
}
```

Bu durumda uygulamada `HYBRIDP2P_FORWARDED_ALLOW_IPS=127.0.0.1` kalmalıdır
(yalnızca aynı makinedeki nginx'e güvenilir).
