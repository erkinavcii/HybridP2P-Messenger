"""desktop/app.py — MessengerApp: tüm mixin'leri birleştiren uygulama sınıfı.

client.py'den taşındı (L1, hızlı ilk kare): bu modül aiortc, cv2, av, requests,
sounddevice gibi ağır kütüphaneleri mixin'ler üzerinden içe aktarır. client.py
artık önce zincir yükleyiciyi gösterip bu modülü arka planda yükler.

Bu sınıf:
  1. Flet sayfasını (page) ve aktif dili kurar,
  2. Uygulama durumunu (state) ve paylaşılan UI kontrollerini `self.` özniteliği
     olarak tanımlar,
  3. Tüm mixin'leri tek sınıfta birleştirir.
"""

import asyncio
import threading

import flet as ft
from desktop.theme import C, t, set_lang, get_lang
from desktop import settings_store
from desktop.brand_loader import BusyOverlay, WINDOW_ICON, BRAND_ICON as LOGIN_ICON, BRAND_SPLASH as LOGIN_SPLASH

from desktop.bubbles import BubblesMixin
from desktop.rest_client import RestClientMixin
from desktop.chat_logic import ChatLogicMixin
from desktop.ws_client import WsClientMixin
from desktop.ui_components import UiComponentsMixin
from desktop.login_screen import LoginScreenMixin
from desktop.inbox_screen import InboxScreenMixin
from desktop.chat_screen import ChatScreenMixin
from desktop.pure_p2p import PureP2PMixin
from desktop.p2p_chat import P2PChatMixin
from desktop.serverless_screen import ServerlessScreenMixin
from desktop.call_screen import CallScreenMixin


def _invert_hex(color: str) -> str:
    """'#rrggbb' rengin tersi (açık temada splash görselinin tonlaması için)."""
    value = int(color.lstrip("#")[:6], 16)
    return f"#{0xFFFFFF - value:06x}"


class MessengerApp(
    BubblesMixin,
    RestClientMixin,
    ChatLogicMixin,
    WsClientMixin,
    UiComponentsMixin,
    LoginScreenMixin,
    InboxScreenMixin,
    ChatScreenMixin,
    PureP2PMixin,
    P2PChatMixin,
    ServerlessScreenMixin,
    CallScreenMixin,
):
    def __init__(self, page: ft.Page, serverless_only: bool = False):
        self.serverless_only = serverless_only
        self.page = page
        page.title = "HybridP2P — Direct P2P" if serverless_only else "HybridP2P Messenger"
        page.window.width = 480
        page.window.height = 820
        page.window.icon = WINDOW_ICON
        page.padding = 0

        # Kayıtlı tema ve dil tercihi (giriş ekranı dahil her yerde geçerli)
        C.apply(settings_store.get("theme"))
        set_lang(settings_store.get("lang"))
        self._apply_page_theme()

        self.state = {
            "username":          None,
            "private_key":       None,
            "public_key":        None,
            "recipient":         None,
            "recipient_pub_key": None,
            "ws":                None,
            "ws_loop":           None,
            "store":             None,
            "ephemeral":         False,
            "view_once_mode":    False,
            "staged_file":       None,
            "logged_in":         False,
            "active_pc":         None,
            "active_call_id":    None,
            "call_role":         None,
            "call_type":         None,
            "call_state":        None,
            "call_partner":      None,
            "local_audio_track": None,
            "local_video_track": None,
            "audio_player":      None,
            "call_duration":     0,
            "remote_sdp":        None,
            "serverless":        False,
            "login_mode":        "serverless" if serverless_only else "server",
        }

        self.file_picker = ft.FilePicker()
        # Uzun işlerde (anahtar üretimi, bağlanma) tam ekran zincir yükleyici
        self.busy = BusyOverlay(page)
        self._build_controls()

        threading.Thread(
            target=self.check_recipient_status_loop,
            daemon=True,
            name="status-checker",
        ).start()

        self.show_login_screen()

    def _apply_page_theme(self):
        self.page.theme_mode = ft.ThemeMode.DARK if C.is_dark else ft.ThemeMode.LIGHT
        self.page.bgcolor = C.bg
        self.page.theme = ft.Theme(color_scheme_seed=C.accent, font_family="Inter, sans-serif")

    def _rebuild_active_view(self):
        for ctrl in list(self.page.overlay):
            if isinstance(ctrl, ft.AlertDialog):
                ctrl.open = False

        self._build_controls()
        self.update_connection_status(self.is_ws_connected())

        if self.state.get("serverless"):
            self.show_serverless_screen()
        elif not self.state.get("logged_in"):
            self.show_login_screen()
        elif self.state.get("recipient"):
            self.state["staged_file"] = None
            self.state["view_once_mode"] = False
            if not self.state.get("is_group", False):
                self.recipient_field.value = self.state["recipient"]
            self._update_ephemeral_ui()
            self.load_history_to_chat()
            self.show_chat_screen()
        else:
            self.show_inbox_screen()

    def set_theme(self, name: str):
        """Temayı değiştirir, tercihi kaydeder ve UI'ı yeni paletle yeniden kurar."""
        settings_store.set("theme", name)
        C.apply(name)
        self._apply_page_theme()
        self._rebuild_active_view()

    def set_language(self, lang: str):
        """Arayüz dilini değiştirir (en/tr), tercihi kaydeder ve ekranı yeniden kurar."""
        settings_store.set("lang", lang)
        set_lang(lang)
        self._rebuild_active_view()

    def _build_controls(self):
        """Paylaşılan tüm UI kontrollerini aktif palet (C) ve dil (t) ile kurar."""
        page = self.page

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     UI BİLEŞENLERİ                         ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.status_text = ft.Text(
            t("status_welcome"),
            size=11,
            color=C.text_secondary,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        self.chat_list = ft.ListView(
            expand=True,
            spacing=8,
            padding=ft.Padding(12, 10, 12, 10),
            auto_scroll=True,
        )

        self.ephemeral_btn = ft.IconButton(
            icon=ft.Icons.TIMER_OUTLINED,
            icon_color=C.text_secondary,
            icon_size=20,
            tooltip=t("ephemeral_tooltip"),
            on_click=self.toggle_ephemeral,
        )

        self.call_icon_btn = ft.IconButton(
            icon=ft.Icons.CALL_OUTLINED,
            icon_color=C.text_secondary,
            icon_size=20,
            tooltip=t("voice_call_tooltip"),
            on_click=lambda e: self.start_voip_call(video=False),
            visible=False,
        )
        self.video_call_icon_btn = ft.IconButton(
            icon=ft.Icons.VIDEOCAM_OUTLINED,
            icon_color=C.text_secondary,
            icon_size=20,
            tooltip=t("video_call_tooltip"),
            on_click=lambda e: self.start_voip_call(video=True),
            visible=False,
        )

        self.view_once_msg_btn = ft.IconButton(
            icon=ft.Icons.LOOKS_ONE_OUTLINED,
            icon_color=C.text_muted,
            icon_size=20,
            tooltip=t("view_once_tooltip"),
            on_click=self.toggle_view_once_msg,
        )

        self.attach_btn = ft.IconButton(
            icon=ft.Icons.ATTACH_FILE,
            icon_color=C.text_muted,
            icon_size=20,
            tooltip=t("attach_tooltip"),
            on_click=self.on_attach_click,
        )

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     GİRİŞ EKRANI                           ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.server_address_field = ft.TextField(
            label=t("server_address"),
            value="127.0.0.1:8000",
            hint_text="127.0.0.1:8000 · https://chat.example.com",
            prefix_icon=ft.Icons.DNS_OUTLINED,
            border_color=C.border,
            focused_border_color=C.accent,
            border_radius=10,
            cursor_color=C.accent,
            text_size=14,
            bgcolor=C.surface_deep,
        )

        self.tls_pin_field = ft.TextField(
            label=t("tls_pin"),
            hint_text=t("tls_pin_hint"),
            prefix_icon=ft.Icons.VERIFIED_USER_OUTLINED,
            border_color=C.border,
            focused_border_color=C.accent,
            border_radius=10,
            cursor_color=C.accent,
            text_size=12,
            bgcolor=C.surface_deep,
        )

        self.username_field = ft.TextField(
            label=t("username"),
            hint_text=t("username_hint"),
            prefix_icon=ft.Icons.PERSON_OUTLINE,
            border_color=C.border,
            focused_border_color=C.accent,
            border_radius=10,
            cursor_color=C.accent,
            text_size=15,
            bgcolor=C.surface_deep,
            on_submit=lambda e: (
                self.on_serverless_start_click(e)
                if self.state.get("login_mode") == "serverless" or self.serverless_only
                else self.on_login_click(e)
            ),
        )

        self.import_key_checkbox = ft.Checkbox(
            label=t("import_key_label"),
            value=False,
            active_color=C.accent,
            on_change=lambda e: self.on_import_key_change(e),
        )

        self.import_key_field = ft.TextField(
            label=t("import_key_pem"),
            hint_text="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
            multiline=True,
            min_lines=3,
            max_lines=5,
            visible=False,
            border_color=C.border,
            focused_border_color=C.accent,
            border_radius=10,
            cursor_color=C.accent,
            text_size=11,
            bgcolor=C.surface_deep,
        )

        self.login_btn = ft.Button(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LOCK_OPEN_ROUNDED, size=18),
                    ft.Text(t("sign_in"), size=15, weight=ft.FontWeight.W_600),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
            ),
            on_click=lambda e: self.on_login_click(e),
            style=ft.ButtonStyle(
                bgcolor=C.accent,
                color=C.on_accent,
                padding=ft.Padding(24, 14, 24, 14),
                shape=ft.RoundedRectangleBorder(radius=10),
                elevation=0,
            ),
            width=340,
            height=48,
        )

        self.serverless_btn = ft.Button(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.WIFI_TETHERING, size=18),
                    ft.Text(t("start_serverless"), size=15, weight=ft.FontWeight.W_600),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=8,
            ),
            on_click=self.on_serverless_start_click,
            style=ft.ButtonStyle(
                bgcolor=C.accent,
                color=C.on_accent,
                padding=ft.Padding(24, 14, 24, 14),
                shape=ft.RoundedRectangleBorder(radius=10),
                elevation=0,
            ),
            width=340,
            height=48,
        )

        # Mod seçici + daraltılabilir gelişmiş ayarlar
        is_p2p = self.serverless_only or self.state.get("login_mode") == "serverless"
        mode_hint = ft.Text(
            t("mode_serverless_hint") if is_p2p else t("mode_server_hint"),
            size=12,
            color=C.text_secondary,
            text_align=ft.TextAlign.CENTER,
        )

        server_adv_group = ft.Column(
            controls=[
                self.server_address_field,
                ft.Container(height=8),
                self.tls_pin_field,
                ft.Container(height=6),
            ],
            spacing=0,
            # STRETCH: alanlar kullanıcı adı alanıyla aynı genişlikte ve aynı sol kenarda
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            visible=not is_p2p,
        )

        adv_open = {"on": False}
        adv_body = ft.Container(
            content=ft.Column(
                controls=[
                    server_adv_group,
                    self.import_key_checkbox,
                    self.import_key_field,
                ],
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            visible=False,
            padding=ft.Padding(0, 8, 0, 0),
        )
        adv_chevron = ft.Icon(ft.Icons.EXPAND_MORE, size=18, color=C.text_muted)

        def toggle_adv(e):
            adv_open["on"] = not adv_open["on"]
            adv_body.visible = adv_open["on"]
            adv_chevron.icon = ft.Icons.EXPAND_LESS if adv_open["on"] else ft.Icons.EXPAND_MORE
            self.page.update()

        adv_toggle = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.TUNE_ROUNDED, size=16, color=C.text_muted),
                    ft.Text(t("advanced_settings"), size=12, color=C.text_secondary, expand=True),
                    adv_chevron,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(10, 8, 10, 8),
            border_radius=8,
            bgcolor=C.surface_deep,
            border=ft.Border.all(1, C.border),
            ink=True,
            on_click=toggle_adv,
        )

        mode_server_btn = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.CLOUD_QUEUE, size=16, color=C.on_accent if not is_p2p else C.text_secondary),
                    ft.Text(
                        t("mode_server"),
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=C.on_accent if not is_p2p else C.text_secondary,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=6,
            ),
            bgcolor=C.accent if not is_p2p else "transparent",
            border_radius=8,
            padding=ft.Padding(10, 8, 10, 8),
            expand=True,
            ink=True,
        )
        mode_p2p_btn = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.WIFI_TETHERING, size=16, color=C.on_accent if is_p2p else C.text_secondary),
                    ft.Text(
                        t("mode_serverless"),
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=C.on_accent if is_p2p else C.text_secondary,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=6,
            ),
            bgcolor=C.accent if is_p2p else "transparent",
            border_radius=8,
            padding=ft.Padding(10, 8, 10, 8),
            expand=True,
            ink=True,
        )

        def _apply_login_mode(mode: str):
            self.state["login_mode"] = mode
            p2p = mode == "serverless"
            mode_server_btn.bgcolor = "transparent" if p2p else C.accent
            mode_server_btn.content.controls[0].color = C.text_secondary if p2p else C.on_accent
            mode_server_btn.content.controls[1].color = C.text_secondary if p2p else C.on_accent
            mode_p2p_btn.bgcolor = C.accent if p2p else "transparent"
            mode_p2p_btn.content.controls[0].color = C.on_accent if p2p else C.text_secondary
            mode_p2p_btn.content.controls[1].color = C.on_accent if p2p else C.text_secondary
            mode_hint.value = t("mode_serverless_hint") if p2p else t("mode_server_hint")
            server_adv_group.visible = not p2p
            self.login_btn.visible = not p2p
            self.serverless_btn.visible = p2p
            self.page.update()

        mode_server_btn.on_click = lambda e: _apply_login_mode("server")
        mode_p2p_btn.on_click = lambda e: _apply_login_mode("serverless")

        self.login_btn.visible = not is_p2p
        self.serverless_btn.visible = is_p2p

        mode_switcher = ft.Container(
            content=ft.Row([mode_server_btn, mode_p2p_btn], spacing=4),
            bgcolor=C.surface_alt,
            border_radius=10,
            padding=4,
            visible=not self.serverless_only,
        )

        cur_lang = get_lang()
        quick_controls = ft.Row(
            controls=[
                ft.TextButton(
                    "TR" if cur_lang == "en" else "EN",
                    on_click=lambda e: self.set_language("tr" if cur_lang == "en" else "en"),
                    style=ft.ButtonStyle(color=C.text_secondary),
                    tooltip=t("language_label"),
                ),
                ft.IconButton(
                    icon=ft.Icons.LIGHT_MODE_OUTLINED if C.is_dark else ft.Icons.DARK_MODE_OUTLINED,
                    icon_size=18,
                    icon_color=C.text_secondary,
                    tooltip=t("appearance"),
                    on_click=lambda e: self.set_theme("light" if C.is_dark else "dark"),
                ),
            ],
            alignment=ft.MainAxisAlignment.END,
            spacing=0,
        )

        login_card = ft.Container(
            content=ft.Column(
                controls=[
                    mode_switcher,
                    mode_hint,
                    ft.Container(height=4),
                    self.username_field,
                    adv_toggle,
                    adv_body,
                    ft.Container(height=4),
                    self.login_btn,
                    self.serverless_btn,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                spacing=10,
                tight=True,
            ),
            bgcolor=C.surface,
            border=ft.Border.all(1, C.border),
            border_radius=16,
            padding=22,
            width=384,
            shadow=ft.BoxShadow(blur_radius=20, color=C.shadow, offset=ft.Offset(0, 6)),
        )

        # Splash (ilk açılış görseli): giriş ekranının üst kısmında, alta doğru tema
        # zeminine eriyen ince bir arka plan. Görseldeki parlayan zincir logo görevi görür;
        # başlık ve kart görselin tamamen eridiği bölgede durduğu için iki temada da okunur.
        # Açık temada görsel ters çevrilip tonlanır: koyu bir şerit yerine açık zeminde mavi parıltı.
        hero_h = 300
        hero_bg = ft.ShaderMask(
            content=ft.Container(
                height=hero_h,
                image=ft.DecorationImage(
                    src=LOGIN_SPLASH,
                    fit=ft.BoxFit.COVER,
                    alignment=ft.Alignment(0, -0.5),     # zincir, başlığın üstündeki boşluğa oturur
                    # Açık tema: renkler ters çevrilir (koyu zemin → açık), parıltı mavi kalsın
                    # diye önce vurgu renginin TERSİ ile ton (HUE) uygulanır (Flutter'da
                    # tersleme renk filtresinden SONRA yapılır).
                    invert_colors=not C.is_dark,
                    color_filter=None if C.is_dark else ft.ColorFilter(
                        color=_invert_hex(C.accent), blend_mode=ft.BlendMode.HUE
                    ),
                ),
            ),
            shader=ft.LinearGradient(
                begin=ft.Alignment(0, -1),
                end=ft.Alignment(0, 1),
                colors=[
                    ft.Colors.with_opacity(1.0 if C.is_dark else 0.9, "#ffffff"),
                    ft.Colors.with_opacity(0.9 if C.is_dark else 0.75, "#ffffff"),
                    ft.Colors.with_opacity(0.0, "#ffffff"),
                ],
                stops=[0.0, 0.5, 0.95],
            ),
            blend_mode=ft.BlendMode.DST_IN,
            top=0,
            left=0,
            right=0,
            height=hero_h,
        )

        login_column = ft.Column(
            controls=[
                ft.Container(content=quick_controls, padding=ft.Padding(16, 10, 16, 0)),
                ft.Container(height=hero_h - 200),          # splash'teki zincir burada görünür
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text("HybridP2P", size=26, weight=ft.FontWeight.BOLD, color=C.text),
                            ft.Text(t("app_tagline"), size=13, color=C.text_secondary, text_align=ft.TextAlign.CENTER),
                            ft.Container(height=12),
                            login_card,
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=6,
                    ),
                    alignment=ft.Alignment(0, -1),
                    padding=ft.Padding(16, 0, 16, 0),
                ),
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.VERIFIED_USER_OUTLINED, size=14, color=C.success),
                            ft.Text("RSA-4096 · AES-256-GCM · Zero-Knowledge", size=11, color=C.text_muted),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=6,
                    ),
                    padding=ft.Padding(0, 8, 0, 18),
                ),
            ],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )

        self.login_view = ft.Container(
            content=ft.Stack([hero_bg, login_column], expand=True),
            expand=True,
            bgcolor=C.bg,
        )

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     SOHBET EKRANI                          ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.recipient_field = ft.TextField(
            label="Recipient",
            hint_text="Example: bob",
            prefix_icon=ft.Icons.PERSON_SEARCH,
            border_color=C.accent,
            focused_border_color=C.accent_light,
            cursor_color=C.accent,
            text_size=14,
            height=48,
            expand=True,
        )

        self.message_input = ft.TextField(
            hint_text=t("type_message_hint"),
            border_color=C.border,
            focused_border_color=C.accent,
            border_radius=20,
            bgcolor=C.surface_deep,
            cursor_color=C.accent,
            text_size=14,
            min_lines=1,
            max_lines=4,
            expand=True,
            max_length=8000,
            counter="",
            content_padding=ft.Padding(14, 10, 14, 10),
            on_submit=lambda e: self.on_send_click(e),
            on_change=self.on_message_input_change,
            shift_enter=True,
        )

        self.typing_text = ft.Text(
            t("typing"),
            size=11,
            color=C.accent_light,
            italic=True,
            visible=False,
        )

        self.username_text = ft.Text("", size=11, color=C.text_secondary)
        self.status_dot = ft.Container(width=7, height=7, border_radius=4, bgcolor=C.danger)
        self.status_label = ft.Text(t("server_offline"), size=10, color=C.danger, weight=ft.FontWeight.W_600)

        self.username_subtitle = ft.Row(
            controls=[
                self.username_text,
                ft.Text("·", size=11, color=C.text_faint),
                self.status_dot,
                self.status_label,
            ],
            spacing=5,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.recipient_status_dot = ft.Container(width=7, height=7, border_radius=4, bgcolor=C.danger)
        self.recipient_status_label = ft.Text(t("status_offline"), size=11, color=C.text_muted)

        self.recipient_status_row = ft.Row(
            controls=[self.recipient_status_dot, self.recipient_status_label],
            spacing=5,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )

        self.chat_avatar = ft.Container(visible=False)
        self.chat_title_text = ft.Text("", size=16, weight=ft.FontWeight.BOLD, color=C.text)
        self.inbox_list = ft.ListView(expand=True, spacing=4, padding=ft.Padding(10, 8, 10, 8))

        self.fab = ft.FloatingActionButton(
            icon=ft.Icons.EDIT_OUTLINED,
            bgcolor=C.accent,
            foreground_color=C.on_accent,
            on_click=lambda e: self.open_new_chat_dialog(e, 0),
            tooltip=t("new_chat_tooltip"),
            visible=False,
        )
        page.floating_action_button = self.fab

        self.search_field = ft.TextField(
            hint_text=t("search_chats"),
            prefix_icon=ft.Icons.SEARCH,
            border_color=C.border,
            focused_border_color=C.accent,
            border_radius=12,
            bgcolor=C.surface_deep,
            cursor_color=C.accent,
            height=40,
            text_size=13,
            content_padding=ft.Padding(10, 0, 10, 0),
            on_change=self.on_search_change,
        )

        self.inbox_view = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Image(src=LOGIN_ICON, width=26, height=26, fit=ft.BoxFit.CONTAIN),
                                ft.Column(
                                    controls=[
                                        ft.Text(t("chats"), size=18, weight=ft.FontWeight.BOLD, color=C.text),
                                        self.username_subtitle,
                                    ],
                                    spacing=1,
                                    tight=True,
                                ),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.WIFI_TETHERING,
                                    icon_color=C.accent_light,
                                    icon_size=20,
                                    tooltip=t("p2p_tooltip"),
                                    on_click=self.open_pure_p2p_dialog,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.PEOPLE_OUTLINE,
                                    icon_color=C.text_secondary,
                                    icon_size=20,
                                    tooltip=t("contacts_tooltip"),
                                    on_click=self.open_contacts_dialog,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.REFRESH,
                                    icon_color=C.text_secondary,
                                    icon_size=19,
                                    tooltip=t("refresh_tooltip"),
                                    on_click=lambda e: self.refresh_inbox_and_messages(),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.TUNE_ROUNDED,
                                    icon_color=C.text_secondary,
                                    icon_size=20,
                                    tooltip=t("settings_tooltip"),
                                    on_click=self.open_settings_dialog,
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=0,
                        ),
                        bgcolor=C.surface,
                        padding=ft.Padding(16, 10, 10, 10),
                        border=ft.Border(bottom=ft.BorderSide(1, C.border)),
                    ),
                    ft.Container(
                        content=self.search_field,
                        padding=ft.Padding(12, 8, 12, 8),
                        bgcolor=C.surface,
                        border=ft.Border(bottom=ft.BorderSide(1, C.border)),
                    ),
                    ft.Container(content=self.inbox_list, expand=True, bgcolor=C.bg),
                    ft.Container(
                        content=self.status_text,
                        padding=ft.Padding(14, 4, 14, 4),
                        bgcolor=C.surface,
                        border=ft.Border(top=ft.BorderSide(1, C.border)),
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            expand=True,
        )

        self.staged_file_name_text = ft.Text("", size=12, color=C.text, weight=ft.FontWeight.W_600)
        self.upload_progress = ft.ProgressBar(color=C.accent, height=2, visible=False)

        self.voice_btn = ft.IconButton(
            icon=ft.Icons.MIC_NONE,
            icon_color=C.text_muted,
            icon_size=20,
            tooltip=t("voice_record_tooltip"),
            on_click=self.toggle_voice_recording,
        )
        self.recording_label = ft.Text(
            t("recording_fmt", elapsed="0:00", limit="2:00"),
            size=12,
            color=C.danger,
            weight=ft.FontWeight.BOLD,
        )
        self.recording_bar = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.FIBER_MANUAL_RECORD, color=C.danger, size=14),
                    self.recording_label,
                    ft.Container(expand=True),
                    ft.TextButton(
                        t("cancel"),
                        on_click=self.cancel_voice_recording,
                        style=ft.ButtonStyle(color=C.text_muted),
                    ),
                    ft.TextButton(
                        t("send"),
                        on_click=self.toggle_voice_recording,
                        style=ft.ButtonStyle(color=C.accent),
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.danger_bg,
            padding=ft.Padding(10, 2, 6, 2),
            border_radius=8,
            visible=False,
        )

        self.staged_file_container = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ATTACH_FILE, color=C.accent, size=16),
                    self.staged_file_name_text,
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_color=C.danger,
                        icon_size=14,
                        on_click=self.remove_staged_file,
                        tooltip=t("remove_file"),
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.surface_alt,
            padding=ft.Padding(10, 4, 8, 4),
            border_radius=8,
            visible=False,
        )

        chat_more_menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_color=C.text_secondary,
            icon_size=20,
            items=[
                ft.PopupMenuItem(
                    content=ft.Text(t("copy_card_tooltip"), size=13),
                    icon=ft.Icons.BADGE_OUTLINED,
                    on_click=self.copy_public_key,
                ),
                ft.PopupMenuItem(
                    content=ft.Text(t("fetch_offline_tooltip"), size=13),
                    icon=ft.Icons.SYNC,
                    on_click=lambda e: threading.Thread(target=self.fetch_offline_messages, daemon=True).start(),
                ),
            ],
        )

        self.chat_view = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.IconButton(
                                    icon=ft.Icons.ARROW_BACK,
                                    icon_color=C.text,
                                    icon_size=20,
                                    on_click=lambda e: self.show_inbox_screen(),
                                ),
                                self.chat_avatar,
                                ft.Column(
                                    controls=[
                                        self.chat_title_text,
                                        self.recipient_status_row,
                                        self.typing_text,
                                    ],
                                    spacing=1,
                                    tight=True,
                                    expand=True,
                                ),
                                self.ephemeral_btn,
                                self.call_icon_btn,
                                self.video_call_icon_btn,
                                chat_more_menu,
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=2,
                        ),
                        bgcolor=C.surface,
                        padding=ft.Padding(8, 8, 8, 8),
                        border=ft.Border(bottom=ft.BorderSide(1, C.border)),
                    ),
                    ft.Container(content=self.chat_list, expand=True, bgcolor=C.bg),
                    ft.Container(
                        content=self.status_text,
                        padding=ft.Padding(14, 3, 14, 3),
                        bgcolor=C.surface,
                        border=ft.Border(top=ft.BorderSide(1, C.border)),
                    ),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                self.staged_file_container,
                                self.recording_bar,
                                self.upload_progress,
                                ft.Row(
                                    controls=[
                                        self.view_once_msg_btn,
                                        self.attach_btn,
                                        self.voice_btn,
                                        self.message_input,
                                        ft.IconButton(
                                            icon=ft.Icons.SEND_ROUNDED,
                                            bgcolor=C.accent,
                                            icon_color=C.on_accent,
                                            icon_size=18,
                                            on_click=self.on_send_click,
                                            tooltip=t("send_tooltip"),
                                        ),
                                    ],
                                    spacing=4,
                                    vertical_alignment=ft.CrossAxisAlignment.END,
                                ),
                            ],
                            spacing=6,
                            tight=True,
                        ),
                        bgcolor=C.surface,
                        padding=ft.Padding(8, 8, 10, 10),
                        border=ft.Border(top=ft.BorderSide(1, C.border)),
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            expand=True,
        )

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                VOIP (ARAMA) EKRANI ELEMANLARI              ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.call_avatar = ft.CircleAvatar(
            content=ft.Text("?", size=40, color=C.on_accent),
            radius=56,
            bgcolor=C.accent,
        )
        self.call_name_text = ft.Text("Username", size=22, weight=ft.FontWeight.BOLD, color=C.text)
        self.call_status_text = ft.Text("Calling...", size=13, color=C.text_subtle)
        self.call_timer_text = ft.Text("00:00", size=14, color=C.accent_light, visible=False)
        self.call_quality_text = ft.Text(
            "",
            size=12,
            color=C.info_text,
            visible=False,
            text_align=ft.TextAlign.CENTER,
        )
        # Arama kurulurken zincir yükleyici; P2P aramada güvenlik kodu + doğrulama rozeti
        # (içerikleri call_screen._set_call_setup_loading / _render_call_p2p_panel doldurur)
        self.call_setup_slot = ft.Container(visible=False, padding=ft.Padding(0, 6, 0, 0))
        self.call_p2p_panel = ft.Container(visible=False, padding=ft.Padding(16, 8, 16, 0))

        self.transparent_placeholder = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        self.local_video_preview = ft.Image(
            src=self.transparent_placeholder,
            width=100,
            height=140,
            fit="cover",
            border_radius=10,
            visible=False,
            right=12,
            bottom=12,
        )
        self.remote_video_view = ft.Image(
            src=self.transparent_placeholder,
            fit="contain",
            visible=False,
        )

        self.video_container = ft.Stack(
            controls=[self.remote_video_view, self.local_video_preview],
            expand=True,
            visible=False,
        )

        self.mic_btn = ft.IconButton(
            icon=ft.Icons.MIC,
            icon_color=C.text,
            bgcolor=C.surface_alt,
            on_click=lambda e: self.toggle_call_mic(),
            tooltip="Mute Microphone",
        )
        self.cam_btn = ft.IconButton(
            icon=ft.Icons.VIDEOCAM,
            icon_color=C.text,
            bgcolor=C.surface_alt,
            on_click=lambda e: self.toggle_call_cam(),
            tooltip="Toggle Video",
        )
        self.end_btn = ft.IconButton(
            icon=ft.Icons.CALL_END,
            icon_color=C.on_accent,
            bgcolor=C.danger,
            icon_size=26,
            width=56,
            height=56,
            on_click=lambda e: self.hangup_call_clicked(),
            tooltip="End Call",
        )

        self.accept_btn = ft.IconButton(
            icon=ft.Icons.CALL,
            icon_color=C.on_accent,
            bgcolor=C.success,
            icon_size=26,
            width=56,
            height=56,
            on_click=lambda e: self.accept_call_clicked(),
            tooltip="Answer Call",
        )
        self.decline_btn = ft.IconButton(
            icon=ft.Icons.CALL_END,
            icon_color=C.on_accent,
            bgcolor=C.danger,
            icon_size=26,
            width=56,
            height=56,
            on_click=lambda e: self.decline_call_clicked(),
            tooltip="Decline Call",
        )

        self.caller_controls_row = ft.Row(
            controls=[self.mic_btn, self.end_btn, self.cam_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=20,
        )

        self.callee_controls_row = ft.Row(
            controls=[self.decline_btn, self.accept_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=40,
        )

        self.call_controls_container = ft.Container(
            content=self.caller_controls_row,
            padding=ft.Padding(0, 20, 0, 40),
        )

        self.call_view = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Container(height=36),
                                ft.Row(controls=[self.call_avatar], alignment=ft.MainAxisAlignment.CENTER),
                                ft.Container(height=10),
                                ft.Row(controls=[self.call_name_text], alignment=ft.MainAxisAlignment.CENTER),
                                ft.Row(
                                    controls=[self.call_status_text, self.call_timer_text],
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    spacing=10,
                                ),
                                self.call_quality_text,
                                self.call_setup_slot,
                                self.call_p2p_panel,
                                ft.Container(height=18),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor=C.surface,
                        border_radius=ft.BorderRadius(bottom_left=24, bottom_right=24, top_left=0, top_right=0),
                        border=ft.Border(bottom=ft.BorderSide(1, C.border)),
                    ),
                    ft.Container(
                        content=self.video_container,
                        expand=True,
                        alignment=ft.Alignment(0, 0),
                    ),
                    self.call_controls_container,
                ],
                spacing=0,
                expand=True,
            ),
            bgcolor=C.bg,
            expand=True,
        )

    def run_on_ui(self, func, *args, **kwargs):
        async def _run():
            res = func(*args, **kwargs)
            if asyncio.iscoroutine(res):
                await res
        self.page.run_task(_run)
