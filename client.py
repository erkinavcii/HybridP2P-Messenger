"""
client.py — Flet Tabanlı E2EE Mesajlaşma İstemcisi
=====================================================
v3.1 — Modülerleştirilmiş İstemci Mimarisi

Uygulamanın tüm davranışı artık `desktop/` paketindeki mixin sınıflarında
yaşıyor (server/ paketinin sunucu tarafında yaptığı ayrımın aynısı, bkz.
.claude/plans/reflective-splashing-leaf.md). Bu dosya sadece:
  1. Flet sayfasını (page) kurar,
  2. Uygulama durumunu (state) ve ~47 paylaşılan UI kontrolünü (butonlar,
     text field'lar, container'lar) `self.` özniteliği olarak tanımlar,
  3. Tüm mixin'leri MessengerApp sınıfında birleştirir.

Her mixin dosyası kendi başlığında hangi orijinal fonksiyonları taşıdığını
belgeler (örn. desktop/ws_client.py, desktop/call_screen.py).
"""

import asyncio
import threading

import flet as ft
from desktop.theme import C
from desktop import settings_store

from desktop.bubbles import BubblesMixin
from desktop.rest_client import RestClientMixin
from desktop.chat_logic import ChatLogicMixin
from desktop.ws_client import WsClientMixin
from desktop.ui_components import UiComponentsMixin
from desktop.login_screen import LoginScreenMixin
from desktop.inbox_screen import InboxScreenMixin
from desktop.chat_screen import ChatScreenMixin
from desktop.pure_p2p import PureP2PMixin
from desktop.call_screen import CallScreenMixin


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
    CallScreenMixin,
):
    def __init__(self, page: ft.Page):
        # ── Sayfa Ayarları ────────────────────────────────────────────
        self.page = page
        page.title       = "HybridP2P Messenger"
        page.window.width  = 480
        page.window.height = 820
        page.padding     = 0
        # Kayıtlı tema tercihi (giriş ekranı dahil her yerde geçerli)
        C.apply(settings_store.get("theme"))
        self._apply_page_theme()

        # ── Uygulama Durumu ──────────────────────────────────────────
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
            "view_once_mode":    False,   # per-mesaj view-once toggle
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
        }

        # Dosya seçici — Flet 0.85'te bir "servis"; yalnızca bir kez oluşturulur
        # (page.overlay'e eklenmez, tema değişiminde yeniden kurulmaz).
        self.file_picker = ft.FilePicker()

        self._build_controls()

        # Kullanıcı durumu (online/offline) periyodik kontrol thread'i — tek sefer.
        # NOT: Orijinalinde de app init'te (giriş öncesi) koşulsuz başlar.
        threading.Thread(target=self.check_recipient_status_loop, daemon=True, name="status-checker").start()

        self.show_login_screen()

    def _apply_page_theme(self):
        self.page.theme_mode = ft.ThemeMode.DARK if C.is_dark else ft.ThemeMode.LIGHT
        self.page.bgcolor = C.bg
        self.page.theme = ft.Theme(color_scheme_seed=C.accent, font_family="Inter, sans-serif")

    def set_theme(self, name: str):
        """Temayı değiştirir, tercihi kaydeder ve UI'ı yeni paletle yeniden kurar.

        Widget'lar renklerini kurulurken okuduğu için tüm paylaşılan kontroller
        _build_controls() ile yeniden oluşturulur, ardından aktif ekran yeniden
        gösterilir. Uygulama durumu (self.state) korunur.
        """
        settings_store.set("theme", name)
        C.apply(name)
        self._apply_page_theme()

        # Açık diyaloglar eski paletle kurulmuştu; kapat
        for ctrl in list(self.page.overlay):
            if isinstance(ctrl, ft.AlertDialog):
                ctrl.open = False

        self._build_controls()
        # Yeni kurulan durum göstergeleri varsayılan ("Offline") değerle gelir;
        # gerçek bağlantı durumunu geri yükle
        self.update_connection_status(self.is_ws_connected())

        if not self.state.get("logged_in"):
            self.show_login_screen()
        elif self.state.get("recipient"):
            # Kontroller yeni olduğundan yarım kalmış dosya/tek-görünüm seçimleri sıfırlanır
            self.state["staged_file"] = None
            self.state["view_once_mode"] = False
            if not self.state.get("is_group", False):
                self.recipient_field.value = self.state["recipient"]
            self._update_ephemeral_ui()
            self.load_history_to_chat()
            self.show_chat_screen()
        else:
            self.show_inbox_screen()

    def _build_controls(self):
        """Paylaşılan tüm UI kontrollerini aktif paletle (C) kurar.

        __init__'te bir kez, tema değişiminde (set_theme) tekrar çağrılır.
        Burada yalnızca widget oluşturulur; thread başlatma, durum değişikliği
        veya ekran gösterimi yapılmamalıdır.
        """
        page = self.page

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     UI BİLEŞENLERİ                         ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.status_text = ft.Text("Welcome!", size=11, color=C.text_secondary,
                               max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)

        self.chat_list = ft.ListView(expand=True, spacing=8,
                                 padding=ft.Padding(12, 8, 12, 8),
                                 auto_scroll=True)

        # Ephemeral toggle (chat seviyesi)
        self.ephemeral_btn = ft.IconButton(
            icon=ft.Icons.VISIBILITY, icon_color=C.accent, icon_size=20,
            tooltip="Switch to Ephemeral Chat", on_click=self.toggle_ephemeral,
        )

        # VoIP call icon buttons
        self.call_icon_btn = ft.IconButton(
            icon=ft.Icons.CALL, icon_color=C.accent, icon_size=20,
            tooltip="Voice Call (E2EE)", on_click=lambda e: self.start_voip_call(video=False),
            visible=False
        )
        self.video_call_icon_btn = ft.IconButton(
            icon=ft.Icons.VIDEOCAM, icon_color=C.accent, icon_size=20,
            tooltip="Video Call (E2EE)", on_click=lambda e: self.start_voip_call(video=True),
            visible=False
        )

        # View-once toggle (mesaj seviyesi — input yanında)
        self.view_once_msg_btn = ft.IconButton(
            icon=ft.Icons.VISIBILITY, icon_color=C.text_muted, icon_size=18,
            tooltip="Send as view-once", on_click=self.toggle_view_once_msg,
        )

        # Dosya ekleme butonu
        self.attach_btn = ft.IconButton(
            icon=ft.Icons.ATTACH_FILE, icon_color=C.text_muted, icon_size=20,
            tooltip="Send File / Image", on_click=self.on_attach_click,
        )


        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     GİRİŞ EKRANI                           ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.server_address_field = ft.TextField(
            label="Server Address", value="127.0.0.1:8000",
            hint_text="Example: 127.0.0.1:8000 or server.com:8000",
            prefix_icon=ft.Icons.COMPUTER,
            border_color=C.accent, focused_border_color=C.accent_light,
            cursor_color=C.accent, text_size=15, height=55,
        )

        self.username_field = ft.TextField(
            label="Username", hint_text="Example: alice",
            prefix_icon=ft.Icons.PERSON,
            border_color=C.accent, focused_border_color=C.accent_light,
            cursor_color=C.accent, text_size=15, height=55,
        )

        self.import_key_checkbox = ft.Checkbox(
            label="Import existing Private Key (.pem)",
            value=False,
            on_change=lambda e: self.on_import_key_change(e),
        )

        self.import_key_field = ft.TextField(
            label="Private Key PEM",
            hint_text="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
            multiline=True,
            min_lines=3,
            max_lines=6,
            visible=False,
            border_color=C.accent,
            focused_border_color=C.accent_light,
            cursor_color=C.accent,
            text_size=12,
        )

        self.login_btn = ft.Button(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LOGIN, size=20),
                    ft.Text("Sign In", size=15, weight=ft.FontWeight.BOLD),
                ],
                alignment=ft.MainAxisAlignment.CENTER, spacing=8,
            ),
            on_click=lambda e: self.on_login_click(e),
            style=ft.ButtonStyle(
                bgcolor=C.accent, color=C.on_accent,
                padding=ft.Padding(32, 16, 32, 16),
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation=4,
            ),
            width=280, height=52,
        )

        self.login_view = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(height=60),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.LOCK_OUTLINE, size=64, color=C.accent),
                                ft.Text("HybridP2P", size=32, weight=ft.FontWeight.BOLD, color=C.text),
                                ft.Text("Messenger", size=18, weight=ft.FontWeight.W_300, color=C.accent),
                                ft.Container(height=4),
                                ft.Text("End-to-End Encrypted Messaging", size=13,
                                        color=C.text_secondary, text_align=ft.TextAlign.CENTER),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2,
                        ),
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Container(height=40),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                self.server_address_field,
                                ft.Container(height=12),
                                self.username_field,
                                ft.Container(height=12),
                                self.import_key_checkbox,
                                self.import_key_field,
                                ft.Container(height=16),
                                self.login_btn,
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0,
                        ),
                        padding=ft.Padding(40, 0, 40, 0),
                    ),
                    ft.Container(expand=True),
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.SHIELD, size=14, color=C.success),
                                ft.Text("RSA-4096 + AES-256-GCM + E2EE Dosya", size=11, color=C.success),
                            ],
                            alignment=ft.MainAxisAlignment.CENTER, spacing=6,
                        ),
                        padding=ft.Padding(0, 0, 0, 24),
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True,
            ),
            expand=True,
            gradient=ft.LinearGradient(
                begin=ft.Alignment(0, -1), end=ft.Alignment(0, 1),
                colors=[C.bg, C.surface, C.bg],
            ),
        )

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     SOHBET EKRANI                          ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.recipient_field = ft.TextField(
            label="Recipient", hint_text="Example: bob",
            prefix_icon=ft.Icons.PERSON_SEARCH,
            border_color=C.accent, focused_border_color=C.accent_light,
            cursor_color=C.accent, text_size=14, height=48, expand=True,
        )

        self.message_input = ft.TextField(
            hint_text="Type your message...",
            border_color=C.border, focused_border_color=C.accent,
            cursor_color=C.accent, text_size=14,
            min_lines=1, max_lines=3, expand=True,
            # Normal kullanımda sunucu sınırına (256 KB) asla yaklaşılmasın:
            # 8000 karakter şifrelenip base64'lenince ~30 KB eder.
            max_length=8000,
            counter="",  # Flet max_length ile "0/8000" sayacı gösterir; gizle
            on_submit=lambda e: self.on_send_click(e),
            on_change=self.on_message_input_change,
            shift_enter=True,
        )

        # "yazıyor…" göstergesi (sohbet başlığında, alıcı durumunun altında)
        self.typing_text = ft.Text("yazıyor…", size=10, color=C.accent_light,
                                   italic=True, visible=False)

        self.username_text = ft.Text("", size=11, color=C.text_secondary)
        self.status_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor=C.danger)
        self.status_label = ft.Text("Server: Offline", size=10, color=C.danger, weight=ft.FontWeight.BOLD)

        self.username_subtitle = ft.Row(
            controls=[
                self.username_text,
                ft.Text("|", size=10, color=C.border),
                self.status_dot,
                self.status_label
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER
        )

        self.recipient_status_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor=C.danger)
        self.recipient_status_label = ft.Text("Offline", size=10, color=C.danger, weight=ft.FontWeight.BOLD)

        self.recipient_status_row = ft.Row(
            controls=[
                self.recipient_status_dot,
                self.recipient_status_label
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False
        )

        # Sohbet başlığındaki karşı taraf avatarı (E2EE fotoğraf varsa)
        self.chat_avatar = ft.Container(visible=False)
        self.chat_title_text = ft.Text("No active chat", size=16, weight=ft.FontWeight.BOLD, color=C.text)
        self.inbox_list = ft.ListView(expand=True, spacing=4, padding=8)

        # Define a single floating action button
        self.fab = ft.FloatingActionButton(
            icon=ft.Icons.CHAT,
            bgcolor=C.accent,
            on_click=lambda e: self.open_new_chat_dialog(e, 0),
            tooltip="Start New Chat / Group",
            visible=False,
        )
        page.floating_action_button = self.fab

        self.search_field = ft.TextField(
            hint_text="Search chats and messages...",
            prefix_icon=ft.Icons.SEARCH,
            border_color=C.surface_alt,
            focused_border_color=C.accent,
            cursor_color=C.accent,
            height=38,
            text_size=13,
            content_padding=ft.Padding(10, 0, 10, 0),
            on_change=self.on_search_change,
        )

        self.inbox_view = ft.Container(
            content=ft.Column(
                controls=[
                    # Inbox App Bar
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.LOCK, size=20, color=C.accent),
                                ft.Column(
                                    controls=[
                                        ft.Text("Chats", size=18,
                                                 weight=ft.FontWeight.BOLD, color=C.text),
                                        self.username_subtitle,
                                    ],
                                    spacing=0, tight=True,
                                ),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.GROUP, icon_color=C.accent,
                                    icon_size=20, tooltip="Group Management",
                                    on_click=lambda e: self.open_new_chat_dialog(e, default_tab_index=1),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.CONTACTS, icon_color=C.accent,
                                    icon_size=20, tooltip="Kişi Rehberi",
                                    on_click=self.open_contacts_dialog,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.ROUTER, icon_color=C.accent,
                                    icon_size=20, tooltip="Pure P2P (Sunucusuz Arama)",
                                    on_click=self.open_pure_p2p_dialog,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.REFRESH, icon_color=C.accent,
                                    icon_size=20, tooltip="Refresh",
                                    on_click=lambda e: self.refresh_inbox_and_messages(),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.SETTINGS, icon_color=C.accent,
                                    icon_size=20, tooltip="Settings",
                                    on_click=self.open_settings_dialog,
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor=C.surface,
                        padding=ft.Padding(16, 10, 16, 10),
                        border=ft.Border(bottom=ft.BorderSide(1, C.surface_alt)),
                    ),

                    # Search Bar
                    ft.Container(
                        content=self.search_field,
                        padding=ft.Padding(12, 6, 12, 6),
                        bgcolor=C.surface,
                        border=ft.Border(bottom=ft.BorderSide(1, C.surface_alt)),
                    ),

                    # Chat List
                    ft.Container(content=self.inbox_list, expand=True, bgcolor=C.bg),

                    # Durum çubuğu
                    ft.Container(
                        content=self.status_text,
                        padding=ft.Padding(16, 4, 16, 4),
                        bgcolor=C.surface,
                    ),
                ],
                spacing=0, expand=True,
            ),
            expand=True,
        )

        # staged file controls
        self.staged_file_name_text = ft.Text("", size=12, color=C.text, weight=ft.FontWeight.BOLD)

        self.upload_progress = ft.ProgressBar(color=C.accent, height=2, visible=False)

        # Sesli mesaj: kayıt butonu + kayıt sırasında görünen çubuk
        self.voice_btn = ft.IconButton(
            icon=ft.Icons.MIC_NONE, icon_color=C.text_muted, icon_size=20,
            tooltip="Sesli mesaj kaydet", on_click=self.toggle_voice_recording,
        )
        self.recording_label = ft.Text("Kaydediliyor 0:00", size=12, color=C.danger,
                                       weight=ft.FontWeight.BOLD)
        self.recording_bar = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.FIBER_MANUAL_RECORD, color=C.danger, size=14),
                    self.recording_label,
                    ft.Container(expand=True),
                    ft.TextButton("İptal", on_click=self.cancel_voice_recording,
                                  style=ft.ButtonStyle(color=C.text_muted)),
                    ft.TextButton("Gönder", on_click=self.toggle_voice_recording,
                                  style=ft.ButtonStyle(color=C.accent)),
                ],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.danger_bg, padding=ft.Padding(10, 2, 6, 2), border_radius=6,
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
                        tooltip="Remove file",
                    )
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.surface_alt,
            padding=ft.Padding(8, 4, 8, 4),
            border_radius=6,
            visible=False,
        )

        self.chat_view = ft.Container(
            content=ft.Column(
                controls=[
                    # App Bar
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.IconButton(
                                    icon=ft.Icons.ARROW_BACK, icon_color=C.text,
                                    icon_size=20, on_click=lambda e: self.show_inbox_screen(),
                                ),
                                self.chat_avatar,
                                ft.Column(
                                    controls=[
                                        self.chat_title_text,
                                        self.recipient_status_row,
                                        self.typing_text,
                                    ],
                                    spacing=0, tight=True,
                                ),
                                ft.Container(expand=True),
                                self.ephemeral_btn,
                                self.call_icon_btn,
                                self.video_call_icon_btn,
                                ft.IconButton(
                                    icon=ft.Icons.COPY, icon_color=C.accent,
                                    icon_size=20, tooltip="Copy Contact Card",
                                    on_click=self.copy_public_key,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.REFRESH, icon_color=C.accent,
                                    icon_size=20, tooltip="Fetch Offline Messages",
                                    on_click=lambda e: threading.Thread(target=self.fetch_offline_messages, daemon=True).start(),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor=C.surface,
                        padding=ft.Padding(16, 10, 16, 10),
                        border=ft.Border(bottom=ft.BorderSide(1, C.surface_alt)),
                    ),

                    # Chat listesi
                    ft.Container(content=self.chat_list, expand=True, bgcolor=C.bg),

                    # Durum çubuğu
                    ft.Container(
                        content=self.status_text,
                        padding=ft.Padding(16, 4, 16, 4),
                        bgcolor=C.surface,
                    ),

                    # Mesaj giriş alanı — view-once + attach + send
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
                                        ft.FloatingActionButton(
                                            icon=ft.Icons.SEND_ROUNDED, bgcolor=C.accent,
                                            mini=True, on_click=self.on_send_click,
                                            tooltip="Send (E2EE)",
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
                        padding=ft.Padding(12, 10, 12, 10),
                        border=ft.Border(top=ft.BorderSide(1, C.surface_alt)),
                    ),
                ],
                spacing=0, expand=True,
            ),
            expand=True,
        )

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                VOIP (ARAMA) EKRANI ELEMANLARI              ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.call_avatar = ft.CircleAvatar(
            content=ft.Text("?", size=40, color=C.on_accent),
            radius=60,
            bgcolor=C.accent,
        )
        self.call_name_text = ft.Text("Username", size=24, weight=ft.FontWeight.BOLD, color=C.text)
        self.call_status_text = ft.Text("Calling...", size=14, color=C.text_subtle)
        self.call_timer_text = ft.Text("00:00", size=14, color=C.accent, visible=False)

        self.transparent_placeholder = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        self.local_video_preview = ft.Image(src=self.transparent_placeholder, width=100, height=140, fit="cover", border_radius=8, visible=False, right=10, bottom=10)
        self.remote_video_view = ft.Image(src=self.transparent_placeholder, fit="contain", visible=False)

        self.video_container = ft.Stack(
            controls=[
                self.remote_video_view,
                self.local_video_preview
            ],
            expand=True,
            visible=False
        )

        self.mic_btn = ft.IconButton(
            icon=ft.Icons.MIC,
            icon_color=C.text,
            bgcolor=C.surface_alt,
            on_click=lambda e: self.toggle_call_mic(),
            tooltip="Mute Microphone"
        )
        self.cam_btn = ft.IconButton(
            icon=ft.Icons.VIDEOCAM,
            icon_color=C.text,
            bgcolor=C.surface_alt,
            on_click=lambda e: self.toggle_call_cam(),
            tooltip="Toggle Video"
        )
        self.end_btn = ft.IconButton(
            icon=ft.Icons.CALL_END,
            icon_color=C.on_accent,
            bgcolor=C.danger,
            icon_size=28,
            width=56,
            height=56,
            on_click=lambda e: self.hangup_call_clicked(),
            tooltip="End Call"
        )

        self.accept_btn = ft.IconButton(
            icon=ft.Icons.CALL,
            icon_color=C.on_accent,
            bgcolor=C.success,
            icon_size=28,
            width=56,
            height=56,
            on_click=lambda e: self.accept_call_clicked(),
            tooltip="Answer Call"
        )
        self.decline_btn = ft.IconButton(
            icon=ft.Icons.CALL_END,
            icon_color=C.on_accent,
            bgcolor=C.danger,
            icon_size=28,
            width=56,
            height=56,
            on_click=lambda e: self.decline_call_clicked(),
            tooltip="Decline Call"
        )

        self.caller_controls_row = ft.Row(
            controls=[self.mic_btn, self.end_btn, self.cam_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=20
        )

        self.callee_controls_row = ft.Row(
            controls=[self.decline_btn, self.accept_btn],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=40
        )

        self.call_controls_container = ft.Container(
            content=self.caller_controls_row,
            padding=ft.Padding(0, 20, 0, 40)
        )

        self.call_view = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Container(height=40),
                                ft.Row(
                                    controls=[self.call_avatar],
                                    alignment=ft.MainAxisAlignment.CENTER
                                ),
                                ft.Container(height=10),
                                ft.Row(
                                    controls=[self.call_name_text],
                                    alignment=ft.MainAxisAlignment.CENTER
                                ),
                                ft.Row(
                                    controls=[self.call_status_text, self.call_timer_text],
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    spacing=10
                                ),
                                ft.Container(height=20),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor=C.surface,
                        border_radius=ft.BorderRadius(bottom_left=24, bottom_right=24, top_left=0, top_right=0),
                        shadow=ft.BoxShadow(blur_radius=15, color=C.shadow_strong)
                    ),
                    ft.Container(
                        content=self.video_container,
                        expand=True,
                        alignment=ft.Alignment(0, 0),
                    ),
                    self.call_controls_container
                ],
                spacing=0,
                expand=True
            ),
            bgcolor=C.bg,
            expand=True
        )

    def run_on_ui(self, func, *args, **kwargs):
        async def _run():
            res = func(*args, **kwargs)
            if asyncio.iscoroutine(res):
                await res
        self.page.run_task(_run)


def main(page: ft.Page):
    MessengerApp(page)


if __name__ == "__main__":
    ft.run(main)
