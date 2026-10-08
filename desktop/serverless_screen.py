"""desktop/serverless_screen.py — Sunucu OLMADAN başlatma ("telsiz" modu ana ekranı).

Röle sunucusu kapalı ya da engelliyken de kullanılabilsin diye bu mod hiçbir
ağ isteği yapmaz: kimlik anahtarı yerelden yüklenir (yoksa üretilir), sunucuya
kayıt olunmaz, WebSocket açılmaz. Kişiler bağlantı kodlarıyla (desktop/pure_p2p.py)
doğrudan bağlanır.

Kişi kartı: sunucu yokken anahtar sunucudan alınamaz; kişiler ya ilk bağlantıda
("yeni kişi" → parmak izi karşılaştırılıp rehbere eklenir) ya da önceden
paylaşılan kişi kartıyla rehbere girer. Kart biçimi sunucu modundakiyle aynıdır
(desktop/chat_logic.py copy_public_key).
"""

import json
import re
import threading

import flet as ft

from crypto_utils import get_public_key_fingerprint, pem_string_to_public_key, public_key_to_pem_string
from desktop.theme import C

USERNAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")


def parse_contact_card(text: str, own_username: str = "") -> tuple[str, str, str]:
    """Kişi kartı JSON'unu doğrular → (kullanıcı adı, PEM, parmak izi). Hatalıysa ValueError."""
    try:
        card = json.loads((text or "").strip())
    except ValueError:
        raise ValueError("Kişi kartı okunamadı (JSON bekleniyor).")
    if not isinstance(card, dict):
        raise ValueError("Kişi kartı biçimi hatalı.")
    username = str(card.get("username", "")).strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Karttaki kullanıcı adı geçersiz.")
    if own_username and username == own_username:
        raise ValueError("Kendi kartınızı ekleyemezsiniz.")
    try:
        pub = pem_string_to_public_key(str(card.get("public_key", "")))
    except Exception:
        raise ValueError("Karttaki açık anahtar okunamadı.")
    fp = get_public_key_fingerprint(pub)
    claimed = str(card.get("fingerprint", "")).strip().upper()
    if claimed and claimed != fp:
        raise ValueError("Karttaki parmak izi anahtarla uyuşmuyor — kart değiştirilmiş olabilir.")
    return username, public_key_to_pem_string(pub), fp


def own_contact_card(username: str, public_key) -> str:
    return json.dumps({"username": username, "public_key": public_key_to_pem_string(public_key),
                       "fingerprint": get_public_key_fingerprint(public_key)}, indent=2)


class ServerlessScreenMixin:

    # ── giriş: sunucusuz başlat ──
    def on_serverless_start_click(self, e):
        from message_store import MessageStore
        username = (self.username_field.value or "").strip().lower()
        if not USERNAME_RE.fullmatch(username):
            self.username_field.error_text = "2-32 karakter; yalnızca a-z, 0-9 ve _"
            self.page.update()
            return
        self.username_field.error_text = None
        self.serverless_btn.disabled = True
        self.log_status("Sunucusuz mod başlatılıyor (ağ isteği yapılmaz)…")
        self.page.update()

        def work():
            try:
                if self.import_key_checkbox.value and (self.import_key_field.value or "").strip():
                    from crypto_utils import deserialize_private_key, save_keys_to_disk
                    priv = deserialize_private_key(self.import_key_field.value.strip().encode("utf-8"))
                    save_keys_to_disk(username, priv, priv.public_key())
                priv, pub = self.initialize_keys(username)     # yerelden; yoksa üretir
                self.state.update({"username": username, "private_key": priv, "public_key": pub,
                                   "store": MessageStore(username), "serverless": True,
                                   "logged_in": False})

                def _ui():
                    self.serverless_btn.disabled = False
                    self.show_serverless_screen()
                self.run_on_ui(_ui)
            except Exception as ex:
                msg = f"Sunucusuz mod başlatılamadı: {ex}"     # ex except bitince silinir

                def _fail():
                    self.serverless_btn.disabled = False
                    self.username_field.error_text = msg
                    self.page.update()
                self.run_on_ui(_fail)

        threading.Thread(target=work, daemon=True).start()

    # ── ana ekran ──
    def show_serverless_screen(self):
        me = self.state["username"]
        store = self.state["store"]
        fp = get_public_key_fingerprint(self.state["public_key"])
        contacts_col = ft.Column(spacing=6)

        def render_contacts():
            contacts_col.controls.clear()
            people = [c for c in (store.get_all_contacts() or []) if c.get("username") != me]
            if not people:
                contacts_col.controls.append(ft.Text(
                    "Rehberiniz boş. Biriyle ilk kez bağlandığınızda parmak izini karşılaştırıp "
                    "ekleyebilir ya da kişi kartını aşağıdan içe aktarabilirsiniz.",
                    size=11, color=C.text_muted))
            for c in people:
                contacts_col.controls.append(ft.Container(
                    ft.Row([
                        ft.Icon(ft.Icons.PERSON, size=18, color=C.accent),
                        ft.Column([
                            ft.Text(c["username"], size=13, color=C.text, weight=ft.FontWeight.BOLD),
                            ft.Text(c.get("fingerprint") or "", size=9, color=C.text_muted,
                                    selectable=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ], spacing=1, tight=True, expand=True),
                    ], spacing=8),
                    bgcolor=C.surface, padding=8, border_radius=8))
            self.page.update()

        def import_card(e):
            field = ft.TextField(label="Kişi kartı (JSON)", multiline=True, min_lines=5, max_lines=8,
                                 text_size=10, border_color=C.surface_alt, focused_border_color=C.accent)
            err = ft.Text("", size=11, color=C.danger)

            def save(ev):
                try:
                    username, pem, cfp = parse_contact_card(field.value, me)
                except ValueError as ex:
                    err.value = str(ex)
                    self.page.update()
                    return
                existing = store.get_contact(username)
                if existing and existing.get("fingerprint") != cfp:
                    err.value = (f"'{username}' rehberde FARKLI bir anahtarla kayıtlı. Bu kart sahte olabilir; "
                                 "kişiyle güvenli bir kanaldan doğrulamadan değiştirmeyin.")
                    self.page.update()
                    return
                store.save_contact(username, pem, cfp)
                dlg.open = False
                self.log_status(f"'{username}' rehbere eklendi.")
                render_contacts()

            dlg = ft.AlertDialog(
                title=ft.Text("Kişi kartı ekle", size=15, color=C.text, weight=ft.FontWeight.BOLD),
                content=ft.Container(ft.Column([
                    ft.Text("Kartı kişiden güvenli bir kanalla alın ve parmak izini onunla karşılaştırın.",
                            size=11, color=C.text_muted),
                    field, err], tight=True, spacing=8), width=380),
                actions=[ft.TextButton("İptal", on_click=lambda ev: (setattr(dlg, "open", False), self.page.update())),
                         ft.TextButton("Ekle", on_click=save, style=ft.ButtonStyle(color=C.accent))],
                bgcolor=C.surface)
            self.page.overlay.append(dlg)
            dlg.open = True
            self.page.update()

        def back_to_login(e):
            self.state.update({"serverless": False, "username": None, "private_key": None,
                               "public_key": None, "store": None})
            self.show_login_screen()

        connect_btn = ft.Button(
            content=ft.Row([ft.Icon(ft.Icons.WIFI_TETHERING, size=20),
                            ft.Text("Doğrudan bağlantı kur", size=15, weight=ft.FontWeight.BOLD)],
                           alignment=ft.MainAxisAlignment.CENTER, spacing=8),
            on_click=self.open_pure_p2p_dialog, width=320, height=52,
            style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent,
                                 shape=ft.RoundedRectangleBorder(radius=10)))

        view = ft.Container(
            ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.WIFI_TETHERING, color=C.accent, size=26),
                    ft.Column([
                        ft.Text("Sunucusuz mod", size=20, color=C.text, weight=ft.FontWeight.BOLD),
                        ft.Text("Arada sunucu yok · iki taraf da aynı anda açık olmalı",
                                size=11, color=C.text_muted),
                    ], spacing=0, tight=True, expand=True),
                ], spacing=10),
                ft.Container(
                    ft.Column([
                        ft.Text(f"Kimliğiniz: {me}", size=14, color=C.text, weight=ft.FontWeight.BOLD),
                        ft.Text(fp, size=10, color=C.text_secondary, selectable=True),
                        ft.Row([
                            ft.TextButton("Kişi kartımı kopyala", icon=ft.Icons.COPY,
                                          on_click=lambda e: (self.copy_to_clipboard(
                                              own_contact_card(me, self.state["public_key"])),
                                              self.log_status("Kişi kartınız kopyalandı.")),
                                          style=ft.ButtonStyle(color=C.accent)),
                        ]),
                    ], spacing=4, tight=True),
                    bgcolor=C.surface, padding=12, border_radius=10),
                ft.Container(height=4),
                ft.Row([connect_btn], alignment=ft.MainAxisAlignment.CENTER),
                ft.Text("Bağlantı kodunu karşı tarafa istediğiniz kanaldan (mesaj, e-posta, QR) iletin. "
                        "Kodlar imzalıdır ve tek kullanımlıktır.", size=10, color=C.text_muted,
                        text_align=ft.TextAlign.CENTER),
                ft.Divider(color=C.surface_alt),
                ft.Row([
                    ft.Text("Rehber", size=14, color=C.text, weight=ft.FontWeight.BOLD, expand=True),
                    ft.TextButton("Kişi kartı ekle", icon=ft.Icons.PERSON_ADD, on_click=import_card,
                                  style=ft.ButtonStyle(color=C.accent)),
                ]),
                ft.Container(contacts_col, expand=True),
                ft.Row([
                    ft.TextButton("Sunucuya bağlan", icon=ft.Icons.LOGIN, on_click=back_to_login,
                                  style=ft.ButtonStyle(color=C.text_muted)),
                ], alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(self.status_text, padding=ft.Padding(4, 0, 4, 4)),
            ], spacing=10, expand=True, scroll=ft.ScrollMode.AUTO),
            padding=ft.Padding(20, 30, 20, 10), expand=True, bgcolor=C.bg)

        self.fab.visible = False
        self.page.controls.clear()
        self.page.add(view)
        render_contacts()
