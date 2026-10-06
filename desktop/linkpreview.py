"""desktop/linkpreview.py — Gönderen tarafında link önizleme üretimi ve gelen önizlemeyi temizleme.

Gizlilik modeli (kullanıcı kararı): önizlemeyi GÖNDEREN çeker ve mesajla birlikte
alıcıya şifreli + imzalı gönderir. Alıcı siteye hiç bağlanmaz (IP'si sızmaz),
sunucu URL'yi görmez. Kaçınılmaz bedel: hedef site gönderenin IP'sini görür —
bu yüzden özellik ayarlardan kapatılabilir.

Güvenlik:
  • Yalnızca http/https; port yalnızca 80/443 (veya URL'de açıkça yazılan).
  • Özel/loopback/link-local/rezerve adresler reddedilir — kötü niyetli bir
    link, istemciyi yerel ağdaki cihazlara (modem paneli vb.) istek atmaya
    yönlendiremez. Kontrol her yönlendirme adımında tekrarlanır.
  • 5 sn zaman aşımı, sayfa 1 MB, görsel 3 MB üst sınırı; en fazla 3 yönlendirme.
  • Görseller Pillow ile küçük JPEG'e yeniden kodlanır (gelen önizlemede de).
"""

import base64
import io
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

Image.MAX_IMAGE_PIXELS = 40_000_000   # decompression bomb koruması (avatar ile aynı)

TIMEOUT = (3, 5)
MAX_HTML_BYTES = 1 * 1024 * 1024
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 3
THUMB_MAX = 240
MAX_TITLE, MAX_DESC, MAX_URL = 200, 300, 2048
MAX_THUMB_B64 = 60_000                 # alınan önizlemede küçük resim üst sınırı
# Yaygın bir tarayıcı UA'sı: uygulamaya özgü bir UA, siteye "bu kişi HybridP2P kullanıyor" der
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)


class PreviewError(Exception):
    pass


def find_first_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,;:!?)]}»\"'")
    return url if len(url) <= MAX_URL else None


def _check_host(url: str, allow_private: bool):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise PreviewError("yalnızca http/https")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as ex:
        raise PreviewError(f"çözümlenemedi: {ex}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise PreviewError(f"yerel/özel adres reddedildi ({ip})")


def _get(url: str, max_bytes: int, allow_private: bool, accept: str):
    """Yönlendirmeleri elle izleyerek (her adımda adres kontrolü) sınırlı GET."""
    for _ in range(MAX_REDIRECTS + 1):
        _check_host(url, allow_private)
        resp = requests.get(url, stream=True, timeout=TIMEOUT, allow_redirects=False,
                            headers={"User-Agent": USER_AGENT, "Accept": accept})
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            resp.close()
            if not loc:
                raise PreviewError("boş yönlendirme")
            url = urljoin(url, loc)
            continue
        if resp.status_code != 200:
            resp.close()
            raise PreviewError(f"HTTP {resp.status_code}")
        data = b""
        for chunk in resp.iter_content(16384):
            data += chunk
            if len(data) > max_bytes:
                break
        resp.close()
        return url, resp.headers.get("Content-Type", ""), data[:max_bytes]
    raise PreviewError("çok fazla yönlendirme")


def _thumbnail(raw: bytes) -> str | None:
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
        img = img.convert("RGB")
        img.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=72, optimize=True)
        b64 = base64.b64encode(out.getvalue()).decode("ascii")
        return b64 if len(b64) <= MAX_THUMB_B64 else None
    except Exception:
        return None


def _meta(soup, *names):
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def fetch_preview(url: str, allow_private: bool = False) -> dict | None:
    """URL'den {url, title, description, image} üretir. Başarısızlıkta None
    (önizleme hiçbir koşulda mesaj gönderimini engellememeli)."""
    from bs4 import BeautifulSoup
    try:
        final_url, ctype, html = _get(url, MAX_HTML_BYTES, allow_private,
                                      "text/html,application/xhtml+xml")
        if "html" not in ctype.lower():
            return None
        soup = BeautifulSoup(html, "html.parser")
        title = _meta(soup, "og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
        desc = _meta(soup, "og:description", "twitter:description", "description")
        if not title and not desc:
            return None
        image = None
        img_url = _meta(soup, "og:image", "og:image:url", "twitter:image")
        if img_url:
            try:
                _, ictype, raw = _get(urljoin(final_url, img_url), MAX_IMAGE_BYTES, allow_private, "image/*")
                if ictype.lower().startswith("image/"):
                    image = _thumbnail(raw)
            except (PreviewError, requests.RequestException):
                image = None
        return {"url": url, "title": title[:MAX_TITLE], "description": desc[:MAX_DESC], "image": image}
    except (PreviewError, requests.RequestException) as ex:
        print(f"[LinkPreview] {url[:80]} icin onizleme yok: {ex}")
        return None
    except Exception as ex:
        print(f"[LinkPreview] beklenmeyen hata: {ex}")
        return None


def sanitize(obj) -> dict | None:
    """Karşı taraftan gelen (çözülmüş) önizlemeyi doğrular ve temizler.

    Gönderen istemci değiştirilmiş olabilir: alanlar tür/uzunluk kontrolünden
    geçer, URL yalnızca http/https olabilir (javascript: vb. reddedilir), resim
    yeniden kodlanır.
    """
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    url = obj.get("url")
    if not isinstance(url, str) or len(url) > MAX_URL or urlparse(url).scheme not in ("http", "https"):
        return None
    title = obj.get("title") if isinstance(obj.get("title"), str) else ""
    desc = obj.get("description") if isinstance(obj.get("description"), str) else ""
    if not title and not desc:
        return None
    image = None
    raw_img = obj.get("image")
    if isinstance(raw_img, str) and len(raw_img) <= MAX_THUMB_B64:
        try:
            image = _thumbnail(base64.b64decode(raw_img, validate=True))
        except Exception:
            image = None
    return {"url": url, "title": title[:MAX_TITLE], "description": desc[:MAX_DESC], "image": image}


def signed_data(sender: str, recipient: str, payload: str, encrypted_preview: str) -> bytes:
    """Önizlemeyi o mesaja bağlayan imza verisi (alan ayırıcı "preview:")."""
    return f"preview:{sender}:{recipient}:{payload}:{encrypted_preview}".encode("utf-8")


def domain_of(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host[4:] if host.startswith("www.") else host
