"""desktop/theme.py — Açık/koyu tema paleti.

Tüm UI renkleri sabit hex yerine buradaki anlamsal token'lardan okunur
(`C.accent`, `C.text`, `C.surface` ...). `C.apply("light")` paleti değiştirir;
widget'lar kurulurken renkleri o anki paletten okuduğu için, tema değişince
UI yeniden kurulur (bkz. MessengerApp.set_theme / _build_controls).

Önemli ayrım — beyaz iki farklı anlamda kullanılır:
  • text       : zemin üzerindeki ana metin → açık temada KOYU olur
  • on_accent  : renkli öğe (mor/kırmızı/yeşil buton, avatar, kendi baloncuğun)
                 üzerindeki metin/ikon → her iki temada da BEYAZ kalır
"""

DARK = {
    # Zeminler
    "bg":                "#09090b",
    "surface":           "#18181b",   # app bar, diyaloglar, kartlar
    "surface_alt":       "#27272a",   # karşı tarafın baloncuğu, chip'ler, ikincil butonlar
    "surface_deep":      "#141416",   # arama sonucu mesaj kartları
    "date_sep_bg":       "#1c1c1f",
    "border":            "#3f3f46",
    # Metin
    "text":              "#ffffff",
    "on_accent":         "#ffffff",
    "on_accent_muted":   "#ffffffb3",  # renkli zemin üzerinde ikincil metin (saat)
    "tick_read_on_accent": "#86efac",  # mor baloncuk üzerinde okundu tiki
    "text_bubble_other": "#e0e0e0",
    "text_secondary":    "#9e9e9e",
    "text_muted":        "#888888",
    "text_faint":        "#666666",
    "text_system":       "#aaaaaa",
    "text_subtle":       "#a1a1aa",
    "tick_unread":       "#71717a",
    # Vurgular
    "accent":            "#8b5cf6",
    "accent_light":      "#a78bfa",
    "danger":            "#ef4444",
    "danger_border":     "#ef444444",
    "danger_bg":         "#2d1b1f",
    "success":           "#22c55e",
    "info":              "#3b82f6",
    "info_text":         "#60a5fa",
    "avatar_dm":         "#007acc",
    # Gölgeler
    "shadow":            "#00000033",
    "shadow_strong":     "#000000aa",
}

LIGHT = {
    "bg":                "#f4f4f5",
    "surface":           "#ffffff",
    "surface_alt":       "#e9e9ee",
    "surface_deep":      "#fafafa",
    "date_sep_bg":       "#e4e4e7",
    "border":            "#d4d4d8",
    "text":              "#18181b",
    "on_accent":         "#ffffff",
    "on_accent_muted":   "#ffffffcc",
    "tick_read_on_accent": "#bbf7d0",
    "text_bubble_other": "#18181b",
    "text_secondary":    "#52525b",
    "text_muted":        "#71717a",
    "text_faint":        "#a1a1aa",
    "text_system":       "#52525b",
    "text_subtle":       "#52525b",
    "tick_unread":       "#a1a1aa",
    "accent":            "#7c3aed",   # beyaz zeminde yeterli kontrast için bir ton koyu
    "accent_light":      "#7c3aed",
    "danger":            "#dc2626",
    "danger_border":     "#dc262655",
    "danger_bg":         "#fee2e2",
    "success":           "#16a34a",
    "info":              "#2563eb",
    "info_text":         "#2563eb",
    "avatar_dm":         "#0369a1",
    "shadow":            "#0000001f",
    "shadow_strong":     "#00000040",
}

THEMES = {"dark": DARK, "light": LIGHT}


class Palette:
    """Aktif paletin token'larına öznitelik olarak erişim: C.accent, C.text ..."""

    def __init__(self, name: str = "dark"):
        self.name = "dark"
        self.apply(name)

    def apply(self, name: str):
        if name not in THEMES:
            name = "dark"
        self.name = name
        for key, value in THEMES[name].items():
            setattr(self, key, value)

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


# Uygulama genelinde tek palet nesnesi. Modüller `from desktop.theme import C`
# ile alır; nesne aynı kaldığı için apply() sonrası herkes yeni renkleri görür.
C = Palette("dark")
