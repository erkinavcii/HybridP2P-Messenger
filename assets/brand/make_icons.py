"""Marka ikonlarini icon_source.png'den uretir (deterministik, LANCZOS).

Kullanim:
    python assets/brand/make_icons.py [hedef ...]

Hedefler:
    web      static/icons/icon-192.png, icon-512.png (yuvarlak koseli, seffaf koseler)
             ve icon-maskable-512.png (tam kapli, koseler yok)
    ico      assets/brand/app.ico (16,24,32,48,64,128,256)
    android  android/app/src/main/res/mipmap-*/ic_launcher.png (48..192 px)
             + ic_launcher_foreground.png (108dp tuval, 108..432 px)
    all      hepsi
Argumansiz = web (assets/brand/icon.png her calismada yazilir).

Gereken: Pillow (yalnizca bu gelistirme betigi icin; uygulamanin bagimliligi degil).
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

BRAND = Path(__file__).resolve().parent
ROOT = BRAND.parent.parent
SOURCE = BRAND / "icon_source.png"
SIZE = 1024
RADIUS_RATIO = 0.22   # kose yaricapi / kenar
SS = 4                # maske icin supersample
SAFE_RATIO = 0.62     # maskable: glif genisligi / kenar (guvenli bolge %66 icinde)


def _is_violet(px):
    """Mor tile pikseli mi? (beyaz/gri artiklar ve beyaz glif disarida kalir.)"""
    r, g, b = px
    return b > 200 and (b - g) > 60


def _tile_bbox(img):
    """Mor tile'in sinirlari: satir/sutun taramasi (kose artiklarindan etkilenmez)."""
    w, h = img.size
    px = img.load()
    mid_x, mid_y = w // 2, h // 2
    xs = [x for x in range(w) if _is_violet(px[x, mid_y // 2])]
    ys = [y for y in range(h) if _is_violet(px[mid_x // 2, y])]
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def rounded_mask(size, ratio=RADIUS_RATIO):
    """Anti-aliased yuvarlak kose maskesi: 4x buyuk ciz, LANCZOS ile kucult."""
    big = size * SS
    m = Image.new("L", (big, big), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=round(big * ratio), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def build_rounded(src):
    """Tile'i kirpip kareye oturtur, seffaf yuvarlak koseli 1024 RGBA uretir.

    Kaynaktaki dis kose artiklari (beyazimsi) maske disinda kaldigi icin silinir;
    kose yaricapi tile'inkinden biraz buyuk oldugundan kenar halesi de gider.
    """
    box = _tile_bbox(src)
    tile = src.crop(box).resize((SIZE, SIZE), Image.LANCZOS)
    out = tile.convert("RGBA")
    out.putalpha(rounded_mask(SIZE))
    return out


def _glyph_alpha(src, bg):
    """Beyaz glifin alfa kanali: yesil kanalin zemin ile beyaz arasindaki orani."""
    g = src.split()[1]
    lo, hi = max(bg[1], 170), 245   # gradyan/golge (g<~160) glif sayilmaz
    return g.point(lambda v: 0 if v <= lo else 255 if v >= hi
                   else int((v - lo) * 255 / (hi - lo)))


def build_maskable(src):
    """Tam kapli varyant: duz mor zemin + glif, merkezde ve guvenli bolgede."""
    box = _tile_bbox(src)
    tile = src.crop(box).resize((SIZE, SIZE), Image.LANCZOS).convert("RGB")
    # Zemin rengi: tile'in alt-orta bolgesinden medyan (gradyan en az orada)
    patch = tile.crop((412, 880, 612, 980))
    r, g, b = (sorted(c.getdata())[len(c.getdata()) // 2] for c in patch.split())
    bg = (r, g, b)
    alpha = _glyph_alpha(tile, bg)
    # Kose artiklarini ve kenar halesini disla: yalnizca merkez %80'lik alan
    guard = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(guard).rectangle((100, 100, SIZE - 100, SIZE - 100), fill=255)
    alpha = ImageChops.multiply(alpha, guard)
    gb = alpha.point(lambda v: 255 if v > 128 else 0).getbbox()
    glyph = alpha.crop(gb)
    scale = SAFE_RATIO * SIZE / glyph.width
    gw, gh = round(glyph.width * scale), round(glyph.height * scale)
    glyph = glyph.resize((gw, gh), Image.LANCZOS)
    canvas = Image.new("RGB", (SIZE, SIZE), bg)
    white = Image.new("RGB", (gw, gh), (255, 255, 255))
    canvas.paste(white, ((SIZE - gw) // 2, (SIZE - gh) // 2), glyph)
    return canvas.convert("RGBA")


def _save(img, rel, size=None):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    out = img if size is None or img.width == size else img.resize((size, size), Image.LANCZOS)
    out.save(path, optimize=True)
    print("yazildi:", rel, out.size)


def target_web(rounded, maskable):
    _save(rounded, "static/icons/icon-192.png", 192)
    _save(rounded, "static/icons/icon-512.png", 512)
    _save(maskable, "static/icons/icon-maskable-512.png", 512)


def target_ico(rounded, maskable):
    path = BRAND / "app.ico"
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    rounded.resize((256, 256), Image.LANCZOS).save(path, format="ICO", sizes=sizes)
    print("yazildi: assets/brand/app.ico", sizes)


def build_foreground(maskable, ratio=0.56):
    """Adaptive foreground: seffaf zemin, beyaz glif; glif genisligi tuvalin %56'si (<%66 guvenli bolge)."""
    rgb = maskable.convert("RGB")
    bg = rgb.getpixel((5, 5))
    alpha = _glyph_alpha(rgb, bg)
    glyph = alpha.crop(alpha.point(lambda v: 255 if v > 128 else 0).getbbox())
    scale = ratio * SIZE / glyph.width
    gw, gh = round(glyph.width * scale), round(glyph.height * scale)
    glyph = glyph.resize((gw, gh), Image.LANCZOS)
    out = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 0))
    out.paste(Image.new("RGBA", (gw, gh), (255, 255, 255, 255)),
              ((SIZE - gw) // 2, (SIZE - gh) // 2), glyph)
    return out


def target_android(rounded, maskable):
    fg = build_foreground(maskable)
    dens = (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192))
    for d, px in dens:
        _save(rounded, f"android/app/src/main/res/mipmap-{d}/ic_launcher.png", px)
    for d, px in dens:   # 108dp tuval = 48dp ikonun 2.25 kati
        _save(fg, f"android/app/src/main/res/mipmap-{d}/ic_launcher_foreground.png", px * 9 // 4)


TARGETS = {"web": target_web, "ico": target_ico, "android": target_android}


def main(argv):
    names = argv or ["web"]
    if "all" in names:
        names = list(TARGETS)
    bad = [n for n in names if n not in TARGETS]
    if bad:
        sys.exit(f"bilinmeyen hedef: {bad}; gecerli: {list(TARGETS)} / all")
    src = Image.open(SOURCE).convert("RGB")
    rounded = build_rounded(src)
    maskable = build_maskable(src)
    rounded.save(BRAND / "icon.png", optimize=True)
    print("yazildi: assets/brand/icon.png", rounded.size)
    for n in names:
        TARGETS[n](rounded, maskable)


if __name__ == "__main__":
    main(sys.argv[1:])
