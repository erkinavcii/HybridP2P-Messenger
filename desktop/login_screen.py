"""desktop/login_screen.py — Giriş ekranı davranışı (giriş, anahtar içe aktarma).

client.py'den taşındı (modülerleştirme): show_login_screen, on_import_key_change,
on_login_click (iç içe do_login/login_success_ui/login_failed_ui/background_sync
ile birlikte).
"""

import re
import threading

import flet as ft
from desktop.theme import C

from message_store import MessageStore
from desktop import net_config, tls_pin

USERNAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")


class LoginScreenMixin:

    def show_login_screen(self):
        self.fab.visible = False
        self.page.controls.clear()
        self.page.add(self.login_view)
        self.page.update()

    def _show_pin_mismatch_dialog(self, err):
        def close(e=None):
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.Icons.GPP_BAD, color=C.danger),
                          ft.Text("Sunucu kimliği doğrulanamadı", size=16, color=C.text,
                                  weight=ft.FontWeight.BOLD)], spacing=8),
            content=ft.Container(
                content=ft.Column([
                    ft.Text("Sunucunun sunduğu sertifika, sabitlenen parmak iziyle eşleşmiyor. "
                            "Bağlantı kurulmadı ve hiçbir veri gönderilmedi.", size=12, color=C.text),
                    ft.Text("Araya giren biri (MITM) olabilir ya da sunucu sertifikası değişmiş "
                            "olabilir. Parmak izini sunucu yöneticisinden güvenli bir kanalla "
                            "(yüz yüze, telefon) yeniden doğrulayın.", size=12, color=C.text_muted),
                    ft.Text("Beklenen:", size=10, color=C.text_secondary),
                    ft.Text(err.expected, size=10, color=C.text_secondary, selectable=True),
                    ft.Text("Görülen:", size=10, color=C.danger),
                    ft.Text(err.seen, size=10, color=C.danger, selectable=True),
                ], tight=True, spacing=10),
                width=360,
            ),
            actions=[ft.TextButton("Tamam", on_click=close, style=ft.ButtonStyle(color=C.accent))],
            bgcolor=C.surface,
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    def on_import_key_change(self, e):
        self.import_key_field.visible = self.import_key_checkbox.value
        self.page.update()

    def on_login_click(self, e):
        username = self.username_field.value.strip().lower()
        # Sunucudaki kuralın aynısı (server/routes/users.py USERNAME_RE).
        # Burada ayrıca kontrol etmek zorunlu: kullanıcı adı anahtar klasörünün
        # adı olarak diske yazılıyor ve bu, sunucuya gitmeden ÖNCE oluyor.
        if not USERNAME_RE.fullmatch(username):
            self.username_field.error_text = "2-32 karakter; yalnızca a-z, 0-9 ve _"
            self.page.update()
            return
        self.username_field.error_text = None

        # Adres ve parmak izi biçimi burada (anında geri bildirim); ağ gerektiren
        # parmak izi doğrulaması aşağıda, arka plan iş parçacığında yapılır.
        server_addr = self.server_address_field.value.strip()
        pin = (self.tls_pin_field.value or "").strip()
        self.server_address_field.error_text = None
        self.tls_pin_field.error_text = None
        try:
            use_tls, _, _ = net_config.parse_server_address(server_addr)
            if pin:
                pin = tls_pin.normalize_fingerprint(pin)
                if not use_tls:
                    raise ValueError("Parmak izi yalnızca https:// adresleriyle kullanılır.")
        except ValueError as bad:
            target = self.tls_pin_field if pin and "armak izi" in str(bad) else self.server_address_field
            target.error_text = str(bad)
            self.page.update()
            return

        # Disable button and update text
        self.login_btn.disabled = True
        self.login_btn.content.controls[1].value = "Please wait..."
        self.log_status("Signing in...")
        self.page.update()

        def do_login():
            try:
                # https ise parmak izini doğrula/sabitle (ağ işlemi); uyuşmazlıkta PinMismatch
                net_config.update_server_urls(server_addr, pin)
                self.state["username"] = username
                if self.import_key_checkbox.value:
                    imported_pem = self.import_key_field.value.strip()
                    if not imported_pem:
                        def _fail_empty_key():
                            self.login_btn.disabled = False
                            self.login_btn.content.controls[1].value = "Sign In"
                            self.username_field.error_text = "Please paste your Private Key PEM."
                            self.log_status("Sign in failed. Private key empty.")
                            self.page.update()
                        self.run_on_ui(_fail_empty_key)
                        return
                    try:
                        from crypto_utils import deserialize_private_key, save_keys_to_disk
                        priv = deserialize_private_key(imported_pem.encode("utf-8"))
                        pub = priv.public_key()
                        save_keys_to_disk(username, priv, pub)
                        print(f"[Import] Key successfully imported and stored for user '{username}'.")
                    except Exception as e_key:
                        def _fail_invalid_key(err_msg=str(e_key)):
                            self.login_btn.disabled = False
                            self.login_btn.content.controls[1].value = "Sign In"
                            self.username_field.error_text = f"Invalid Private Key PEM: {err_msg}"
                            self.log_status(f"Import failed: {err_msg}")
                            self.page.update()
                        self.run_on_ui(_fail_invalid_key)
                        return

                priv, pub = self.initialize_keys(username)
                self.state["private_key"] = priv
                self.state["public_key"]  = pub
                self.state["store"]       = MessageStore(username)

                self.register_with_server(username, pub, priv)

                # Reset button state and transition to inbox on Flet's event loop
                async def login_success_ui():
                    self.login_btn.disabled = False
                    self.login_btn.content.controls[1].value = "Sign In"
                    self.state["logged_in"] = True
                    self.show_inbox_screen()

                    def background_sync():
                        self.sync_chat_settings()
                        self.sync_user_groups_from_server()
                        self.fetch_offline_messages()
                        self.start_websocket_listener()

                    threading.Thread(target=background_sync, daemon=True).start()

                self.page.run_task(login_success_ui)
            except Exception as ex:
                # ex, except bloğu bitince silinir; sonradan çalışan fonksiyona
                # varsayılan argümanla aktarılmalı (yoksa NameError → hata hiç görünmez)
                async def login_failed_ui(ex=ex):
                    self.login_btn.disabled = False
                    self.login_btn.content.controls[1].value = "Sign In"
                    if isinstance(ex, tls_pin.PinMismatch):
                        # Olası MITM: gözden kaçmayacak bir uyarı penceresi
                        self.tls_pin_field.error_text = "Parmak izi eşleşmiyor — bağlanılmadı."
                        self._show_pin_mismatch_dialog(ex)
                    else:
                        self.username_field.error_text = f"Hata: {ex}"
                    self.log_status(f"Giris sirasinda hata olustu: {ex}")
                    self.page.update()
                self.page.run_task(login_failed_ui)

        threading.Thread(target=do_login, daemon=True).start()
