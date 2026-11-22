"""desktop/login_screen.py — Giriş ekranı davranışı (giriş, anahtar içe aktarma).

client.py'den taşındı (modülerleştirme): show_login_screen, on_import_key_change,
on_login_click (iç içe do_login/login_success_ui/login_failed_ui/background_sync
ile birlikte).
"""

import re
import threading

import flet as ft
from desktop.theme import C, t

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
            title=ft.Row([
                ft.Icon(ft.Icons.GPP_BAD, color=C.danger),
                ft.Text(t("err_pin_dialog_title"), size=16, color=C.text, weight=ft.FontWeight.BOLD),
            ], spacing=8),
            content=ft.Container(
                content=ft.Column([
                    ft.Text(t("err_pin_dialog_body"), size=12, color=C.text),
                    ft.Text(t("err_pin_dialog_mitm"), size=12, color=C.text_muted),
                    ft.Text(t("expected"), size=10, color=C.text_secondary),
                    ft.Text(err.expected, size=10, color=C.text_secondary, selectable=True),
                    ft.Text(t("seen"), size=10, color=C.danger),
                    ft.Text(err.seen, size=10, color=C.danger, selectable=True),
                ], tight=True, spacing=10),
                width=360,
            ),
            actions=[ft.TextButton(t("ok"), on_click=close, style=ft.ButtonStyle(color=C.accent))],
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
            self.username_field.error_text = t("username_rule")
            self.page.update()
            return
        self.username_field.error_text = None

        server_addr = self.server_address_field.value.strip()
        pin = (self.tls_pin_field.value or "").strip()
        self.server_address_field.error_text = None
        self.tls_pin_field.error_text = None
        try:
            use_tls, _, _ = net_config.parse_server_address(server_addr)
            if pin:
                pin = tls_pin.normalize_fingerprint(pin)
                if not use_tls:
                    raise ValueError(t("err_pin_https_only"))
        except ValueError as bad:
            target = self.tls_pin_field if pin and ("https://" in str(bad) or "armak izi" in str(bad)) else self.server_address_field
            target.error_text = str(bad)
            self.page.update()
            return

        self.login_btn.disabled = True
        self.login_btn.content.controls[1].value = t("please_wait")
        self.log_status(t("signing_in"))
        self.page.update()

        def do_login():
            try:
                net_config.update_server_urls(server_addr, pin)
                self.state["username"] = username
                if self.import_key_checkbox.value:
                    imported_pem = self.import_key_field.value.strip()
                    if not imported_pem:
                        def _fail_empty_key():
                            self.login_btn.disabled = False
                            self.login_btn.content.controls[1].value = t("sign_in")
                            self.username_field.error_text = t("login_key_empty")
                            self.log_status(t("login_key_empty_status"))
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
                            self.login_btn.content.controls[1].value = t("sign_in")
                            self.username_field.error_text = t("login_key_invalid", err=err_msg)
                            self.log_status(t("login_import_failed", err=err_msg))
                            self.page.update()
                        self.run_on_ui(_fail_invalid_key)
                        return

                priv, pub = self.initialize_keys(username)
                self.state["private_key"] = priv
                self.state["public_key"]  = pub
                self.state["store"]       = MessageStore(username)

                self.register_with_server(username, pub, priv)

                async def login_success_ui():
                    self.login_btn.disabled = False
                    self.login_btn.content.controls[1].value = t("sign_in")
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
                async def login_failed_ui(ex=ex):
                    self.login_btn.disabled = False
                    self.login_btn.content.controls[1].value = t("sign_in")
                    if isinstance(ex, tls_pin.PinMismatch):
                        self.tls_pin_field.error_text = t("err_pin_mismatch")
                        self._show_pin_mismatch_dialog(ex)
                    else:
                        self.username_field.error_text = t("error_msg", err=ex)
                    self.log_status(t("login_failed", err=ex))
                    self.page.update()
                self.page.run_task(login_failed_ui)

        threading.Thread(target=do_login, daemon=True).start()
