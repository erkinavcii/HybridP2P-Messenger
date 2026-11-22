"""desktop/serverless_screen.py — Sunucu OLMADAN başlatma ("telsiz" modu ana ekranı).

Röle sunucusu kapalı ya da engelliyken de kullanılabilsin diye bu mod hiçbir
ağ isteği yapmaz: kimlik anahtarı yerelden yüklenir (yoksa üretilir), sunucuya
kayıt olunmaz, WebSocket açılmaz. Kişiler bağlantı kodlarıyla (desktop/pure_p2p.py)
doğrudan bağlanır.
"""

import json
import re
import threading

import flet as ft

from crypto_utils import get_public_key_fingerprint, pem_string_to_public_key, public_key_to_pem_string
from desktop.theme import C, t

USERNAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")


def _format_fp(fp: str) -> str:
    clean = "".join((fp or "").split())
    return " ".join(clean[i:i + 4] for i in range(0, len(clean), 4)) if clean else ""


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
    return json.dumps({
        "username": username,
        "public_key": public_key_to_pem_string(public_key),
        "fingerprint": get_public_key_fingerprint(public_key),
    }, indent=2)


class ServerlessScreenMixin:

    # ── giriş: sunucusuz başlat ──
    def on_serverless_start_click(self, e):
        from message_store import MessageStore
        username = (self.username_field.value or "").strip().lower()
        if not USERNAME_RE.fullmatch(username):
            self.username_field.error_text = t("username_rule")
            self.page.update()
            return
        self.username_field.error_text = None
        self.serverless_btn.disabled = True
        self.log_status(t("starting_serverless"))
        self.page.update()

        def work():
            try:
                if self.import_key_checkbox.value and (self.import_key_field.value or "").strip():
                    from crypto_utils import deserialize_private_key, save_keys_to_disk
                    priv = deserialize_private_key(self.import_key_field.value.strip().encode("utf-8"))
                    save_keys_to_disk(username, priv, priv.public_key())
                priv, pub = self.initialize_keys(username)
                self.state.update({
                    "username": username,
                    "private_key": priv,
                    "public_key": pub,
                    "store": MessageStore(username),
                    "serverless": True,
                    "logged_in": False,
                })

                def _ui():
                    self.serverless_btn.disabled = False
                    self.show_serverless_screen()

                self.run_on_ui(_ui)
            except Exception as ex:
                msg = t("serverless_start_failed", err=ex)

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
        fp = _format_fp(get_public_key_fingerprint(self.state["public_key"]))
        contacts_col = ft.Column(spacing=6)

        def render_contacts():
            contacts_col.controls.clear()
            people = [c for c in (store.get_all_contacts() or []) if c.get("username") != me]
            if not people:
                contacts_col.controls.append(
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.PEOPLE_OUTLINE, size=34, color=C.text_faint),
                                ft.Text(t("contacts_empty"), size=12, color=C.text_secondary),
                                ft.Text(t("contacts_empty_sub"), size=11, color=C.text_muted, text_align=ft.TextAlign.CENTER),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=4,
                        ),
                        padding=20,
                        alignment=ft.Alignment(0, 0),
                    )
                )
            for c in people:
                cfp = _format_fp(c.get("fingerprint") or "")
                short_fp = " ".join(cfp.split()[:8]) + (" …" if len(cfp.split()) > 8 else "")
                contacts_col.controls.append(
                    ft.Container(
                        ft.Row([
                            ft.CircleAvatar(
                                content=ft.Text(c["username"][:2].upper(), size=12, weight=ft.FontWeight.BOLD, color=C.on_accent),
                                bgcolor=C.avatar_dm,
                                radius=18,
                            ),
                            ft.Column([
                                ft.Text(c["username"], size=13, color=C.text, weight=ft.FontWeight.W_600),
                                ft.Text(short_fp, size=10, color=C.text_muted, selectable=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ], spacing=2, tight=True, expand=True),
                            ft.Icon(ft.Icons.VERIFIED_USER_OUTLINED, size=16, color=C.success),
                        ], spacing=10),
                        bgcolor=C.surface,
                        border=ft.Border.all(1, C.border),
                        padding=ft.Padding(12, 10, 12, 10),
                        border_radius=12,
                    )
                )
            self.page.update()

        def import_card(e):
            field = ft.TextField(
                label=t("contact_card_label"),
                multiline=True,
                min_lines=4,
                max_lines=7,
                text_size=10,
                border_color=C.border,
                focused_border_color=C.accent,
                border_radius=10,
                bgcolor=C.surface_deep,
            )
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
                    err.value = t("contact_key_differs", user=username)
                    self.page.update()
                    return
                store.save_contact(username, pem, cfp)
                dlg.open = False
                self.log_status(t("contact_added", user=username))
                render_contacts()

            dlg = ft.AlertDialog(
                title=ft.Text(t("import_contact_card"), size=16, color=C.text, weight=ft.FontWeight.BOLD),
                content=ft.Container(
                    ft.Column([
                        ft.Text(t("import_card_desc"), size=12, color=C.text_muted),
                        field,
                        err,
                    ], tight=True, spacing=8),
                    width=380,
                ),
                actions=[
                    ft.TextButton(t("cancel"), on_click=lambda ev: (setattr(dlg, "open", False), self.page.update())),
                    ft.TextButton(t("add"), on_click=save, style=ft.ButtonStyle(color=C.accent)),
                ],
                bgcolor=C.surface,
            )
            self.page.overlay.append(dlg)
            dlg.open = True
            self.page.update()

        def back_to_login(e):
            self.state.update({
                "serverless": False,
                "username": None,
                "private_key": None,
                "public_key": None,
                "store": None,
                "login_mode": "server",
            })
            self._rebuild_active_view()

        connect_btn = ft.Button(
            content=ft.Row([
                ft.Icon(ft.Icons.WIFI_TETHERING, size=20),
                ft.Text(t("connect_peer_btn"), size=15, weight=ft.FontWeight.W_600),
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=8),
            on_click=self.open_pure_p2p_dialog,
            width=360,
            height=48,
            style=ft.ButtonStyle(
                bgcolor=C.accent,
                color=C.on_accent,
                shape=ft.RoundedRectangleBorder(radius=10),
                elevation=0,
            ),
        )

        header_bar = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.WIFI_TETHERING, color=C.accent_light, size=24),
                ft.Column([
                    ft.Text(t("serverless_title"), size=18, color=C.text, weight=ft.FontWeight.BOLD),
                    ft.Text(t("serverless_sub"), size=11, color=C.text_muted),
                ], spacing=1, tight=True, expand=True),
                ft.TextButton(
                    t("switch_server_mode"),
                    icon=ft.Icons.SWAP_HORIZ,
                    on_click=back_to_login,
                    visible=not getattr(self, "serverless_only", False),
                    style=ft.ButtonStyle(color=C.accent_light),
                ),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=C.surface,
            padding=ft.Padding(16, 12, 12, 12),
            border=ft.Border(bottom=ft.BorderSide(1, C.border)),
        )

        identity_card = ft.Container(
            ft.Column([
                ft.Row([
                    ft.CircleAvatar(
                        content=ft.Text(me[:2].upper(), size=14, weight=ft.FontWeight.BOLD, color=C.on_accent),
                        bgcolor=C.accent,
                        radius=20,
                    ),
                    ft.Column([
                        ft.Text(t("your_identity"), size=11, color=C.text_muted),
                        ft.Text(me, size=16, color=C.text, weight=ft.FontWeight.BOLD),
                    ], spacing=0, tight=True, expand=True),
                    ft.TextButton(
                        t("copy_contact_card"),
                        icon=ft.Icons.COPY,
                        on_click=lambda e: (
                            self.copy_to_clipboard(own_contact_card(me, self.state["public_key"])),
                            self.log_status(t("contact_card_copied")),
                        ),
                        style=ft.ButtonStyle(color=C.accent_light),
                    ),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(
                    content=ft.Text(fp, size=10, color=C.text_secondary, selectable=True, font_family="monospace"),
                    bgcolor=C.surface_deep,
                    padding=ft.Padding(10, 8, 10, 8),
                    border_radius=8,
                    border=ft.Border.all(1, C.border),
                ),
            ], spacing=10, tight=True),
            bgcolor=C.surface,
            border=ft.Border.all(1, C.border),
            padding=14,
            border_radius=14,
        )

        body = ft.Container(
            content=ft.Column([
                identity_card,
                ft.Container(
                    content=ft.Column([
                        ft.Row([connect_btn], alignment=ft.MainAxisAlignment.CENTER),
                        ft.Text(t("connect_peer_sub"), size=11, color=C.text_muted, text_align=ft.TextAlign.CENTER),
                    ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.Padding(0, 4, 0, 4),
                ),
                ft.Divider(color=C.border, height=1),
                ft.Row([
                    ft.Text(t("contacts_title"), size=14, color=C.text, weight=ft.FontWeight.BOLD, expand=True),
                    ft.TextButton(
                        t("import_contact_card"),
                        icon=ft.Icons.PERSON_ADD_OUTLINED,
                        on_click=import_card,
                        style=ft.ButtonStyle(color=C.accent_light),
                    ),
                ]),
                ft.Container(contacts_col, expand=True),
            ], spacing=12, expand=True, scroll=ft.ScrollMode.AUTO),
            padding=ft.Padding(16, 16, 16, 10),
            expand=True,
        )

        view = ft.Container(
            content=ft.Column([
                header_bar,
                body,
                ft.Container(
                    content=self.status_text,
                    padding=ft.Padding(14, 4, 14, 4),
                    bgcolor=C.surface,
                    border=ft.Border(top=ft.BorderSide(1, C.border)),
                ),
            ], spacing=0, expand=True),
            expand=True,
            bgcolor=C.bg,
        )

        self.fab.visible = False
        self.page.controls.clear()
        self.page.add(view)
        render_contacts()
