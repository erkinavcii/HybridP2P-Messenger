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
        page.theme_mode  = ft.ThemeMode.DARK
        page.window.width  = 480
        page.window.height = 820
        page.padding     = 0
        page.bgcolor     = "#09090b"
        page.theme       = ft.Theme(color_scheme_seed="#8b5cf6", font_family="Inter, sans-serif")

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

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     UI BİLEŞENLERİ                         ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.status_text = ft.Text("Welcome!", size=11, color="#9e9e9e",
                               max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)

        self.chat_list = ft.ListView(expand=True, spacing=8,
                                 padding=ft.Padding(12, 8, 12, 8),
                                 auto_scroll=True)

        # Ephemeral toggle (chat seviyesi)
        self.ephemeral_btn = ft.IconButton(
            icon=ft.Icons.VISIBILITY, icon_color="#8b5cf6", icon_size=20,
            tooltip="Switch to Ephemeral Chat", on_click=self.toggle_ephemeral,
        )

        # VoIP call icon buttons
        self.call_icon_btn = ft.IconButton(
            icon=ft.Icons.CALL, icon_color="#8b5cf6", icon_size=20,
            tooltip="Voice Call (E2EE)", on_click=lambda e: self.start_voip_call(video=False),
            visible=False
        )
        self.video_call_icon_btn = ft.IconButton(
            icon=ft.Icons.VIDEOCAM, icon_color="#8b5cf6", icon_size=20,
            tooltip="Video Call (E2EE)", on_click=lambda e: self.start_voip_call(video=True),
            visible=False
        )

        # View-once toggle (mesaj seviyesi — input yanında)
        self.view_once_msg_btn = ft.IconButton(
            icon=ft.Icons.VISIBILITY, icon_color="#888888", icon_size=18,
            tooltip="Send as view-once", on_click=self.toggle_view_once_msg,
        )

        # Dosya ekleme butonu
        self.attach_btn = ft.IconButton(
            icon=ft.Icons.ATTACH_FILE, icon_color="#888888", icon_size=20,
            tooltip="Send File / Image", on_click=self.on_attach_click,
        )

        # Dosya seçici
        self.file_picker = ft.FilePicker()
        # page.overlay.append(file_picker)  # Flet 0.23+ treats this as a Service, appending causes Unknown Control

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     GİRİŞ EKRANI                           ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.server_address_field = ft.TextField(
            label="Server Address", value="127.0.0.1:8000",
            hint_text="Example: 127.0.0.1:8000 or server.com:8000",
            prefix_icon=ft.Icons.COMPUTER,
            border_color="#8b5cf6", focused_border_color="#a78bfa",
            cursor_color="#8b5cf6", text_size=15, height=55,
        )

        self.username_field = ft.TextField(
            label="Username", hint_text="Example: alice",
            prefix_icon=ft.Icons.PERSON,
            border_color="#8b5cf6", focused_border_color="#a78bfa",
            cursor_color="#8b5cf6", text_size=15, height=55,
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
            border_color="#8b5cf6",
            focused_border_color="#a78bfa",
            cursor_color="#8b5cf6",
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
                bgcolor="#8b5cf6", color="#ffffff",
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
                                ft.Icon(ft.Icons.LOCK_OUTLINE, size=64, color="#8b5cf6"),
                                ft.Text("HybridP2P", size=32, weight=ft.FontWeight.BOLD, color="#ffffff"),
                                ft.Text("Messenger", size=18, weight=ft.FontWeight.W_300, color="#8b5cf6"),
                                ft.Container(height=4),
                                ft.Text("End-to-End Encrypted Messaging", size=13,
                                        color="#9e9e9e", text_align=ft.TextAlign.CENTER),
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
                                ft.Icon(ft.Icons.SHIELD, size=14, color="#22c55e"),
                                ft.Text("RSA-4096 + AES-256-GCM + E2EE Dosya", size=11, color="#22c55e"),
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
                colors=["#09090b", "#18181b", "#09090b"],
            ),
        )

        # ╔═══════════════════════════════════════════════════════════╗
        # ║                     SOHBET EKRANI                          ║
        # ╚═══════════════════════════════════════════════════════════╝

        self.recipient_field = ft.TextField(
            label="Recipient", hint_text="Example: bob",
            prefix_icon=ft.Icons.PERSON_SEARCH,
            border_color="#8b5cf6", focused_border_color="#a78bfa",
            cursor_color="#8b5cf6", text_size=14, height=48, expand=True,
        )

        self.message_input = ft.TextField(
            hint_text="Type your message...",
            border_color="#3f3f46", focused_border_color="#8b5cf6",
            cursor_color="#8b5cf6", text_size=14,
            min_lines=1, max_lines=3, expand=True,
            on_submit=lambda e: self.on_send_click(e),
            shift_enter=True,
        )

        self.username_text = ft.Text("", size=11, color="#9e9e9e")
        self.status_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor="#ef4444")
        self.status_label = ft.Text("Server: Offline", size=10, color="#ef4444", weight=ft.FontWeight.BOLD)

        self.username_subtitle = ft.Row(
            controls=[
                self.username_text,
                ft.Text("|", size=10, color="#3f3f46"),
                self.status_dot,
                self.status_label
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER
        )

        self.recipient_status_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor="#ef4444")
        self.recipient_status_label = ft.Text("Offline", size=10, color="#ef4444", weight=ft.FontWeight.BOLD)

        self.recipient_status_row = ft.Row(
            controls=[
                self.recipient_status_dot,
                self.recipient_status_label
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False
        )

        # Kullanıcı durumu (online/offline) periyodik kontrol thread'i.
        # NOT: Orijinalinde de app init'te (giriş öncesi) koşulsuz başlar.
        threading.Thread(target=self.check_recipient_status_loop, daemon=True, name="status-checker").start()

        self.chat_title_text = ft.Text("No active chat", size=16, weight=ft.FontWeight.BOLD, color="#ffffff")
        self.inbox_list = ft.ListView(expand=True, spacing=4, padding=8)

        # Define a single floating action button
        self.fab = ft.FloatingActionButton(
            icon=ft.Icons.CHAT,
            bgcolor="#8b5cf6",
            on_click=lambda e: self.open_new_chat_dialog(e, 0),
            tooltip="Start New Chat / Group",
            visible=False,
        )
        page.floating_action_button = self.fab

        self.search_field = ft.TextField(
            hint_text="Search chats and messages...",
            prefix_icon=ft.Icons.SEARCH,
            border_color="#27272a",
            focused_border_color="#8b5cf6",
            cursor_color="#8b5cf6",
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
                                ft.Icon(ft.Icons.LOCK, size=20, color="#8b5cf6"),
                                ft.Column(
                                    controls=[
                                        ft.Text("Chats", size=18,
                                                 weight=ft.FontWeight.BOLD, color="#ffffff"),
                                        self.username_subtitle,
                                    ],
                                    spacing=0, tight=True,
                                ),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.GROUP, icon_color="#8b5cf6",
                                    icon_size=20, tooltip="Group Management",
                                    on_click=lambda e: self.open_new_chat_dialog(e, default_tab_index=1),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.CONTACTS, icon_color="#8b5cf6",
                                    icon_size=20, tooltip="Kişi Rehberi",
                                    on_click=self.open_contacts_dialog,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.ROUTER, icon_color="#8b5cf6",
                                    icon_size=20, tooltip="Pure P2P (Sunucusuz Arama)",
                                    on_click=self.open_pure_p2p_dialog,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.REFRESH, icon_color="#8b5cf6",
                                    icon_size=20, tooltip="Refresh",
                                    on_click=lambda e: self.refresh_inbox_and_messages(),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.SETTINGS, icon_color="#8b5cf6",
                                    icon_size=20, tooltip="Settings",
                                    on_click=self.open_settings_dialog,
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor="#18181b",
                        padding=ft.Padding(16, 10, 16, 10),
                        border=ft.Border(bottom=ft.BorderSide(1, "#27272a")),
                    ),

                    # Search Bar
                    ft.Container(
                        content=self.search_field,
                        padding=ft.Padding(12, 6, 12, 6),
                        bgcolor="#18181b",
                        border=ft.Border(bottom=ft.BorderSide(1, "#27272a")),
                    ),

                    # Chat List
                    ft.Container(content=self.inbox_list, expand=True, bgcolor="#09090b"),

                    # Durum çubuğu
                    ft.Container(
                        content=self.status_text,
                        padding=ft.Padding(16, 4, 16, 4),
                        bgcolor="#18181b",
                    ),
                ],
                spacing=0, expand=True,
            ),
            expand=True,
        )

        # staged file controls
        self.staged_file_name_text = ft.Text("", size=12, color="#ffffff", weight=ft.FontWeight.BOLD)

        self.upload_progress = ft.ProgressBar(color="#8b5cf6", height=2, visible=False)

        self.staged_file_container = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ATTACH_FILE, color="#8b5cf6", size=16),
                    self.staged_file_name_text,
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_color="#ef4444",
                        icon_size=14,
                        on_click=self.remove_staged_file,
                        tooltip="Remove file",
                    )
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor="#27272a",
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
                                    icon=ft.Icons.ARROW_BACK, icon_color="#ffffff",
                                    icon_size=20, on_click=lambda e: self.show_inbox_screen(),
                                ),
                                ft.Column(
                                    controls=[
                                        self.chat_title_text,
                                        self.recipient_status_row,
                                    ],
                                    spacing=0, tight=True,
                                ),
                                ft.Container(expand=True),
                                self.ephemeral_btn,
                                self.call_icon_btn,
                                self.video_call_icon_btn,
                                ft.IconButton(
                                    icon=ft.Icons.COPY, icon_color="#8b5cf6",
                                    icon_size=20, tooltip="Copy Contact Card",
                                    on_click=self.copy_public_key,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.REFRESH, icon_color="#8b5cf6",
                                    icon_size=20, tooltip="Fetch Offline Messages",
                                    on_click=lambda e: threading.Thread(target=self.fetch_offline_messages, daemon=True).start(),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor="#18181b",
                        padding=ft.Padding(16, 10, 16, 10),
                        border=ft.Border(bottom=ft.BorderSide(1, "#27272a")),
                    ),

                    # Chat listesi
                    ft.Container(content=self.chat_list, expand=True, bgcolor="#09090b"),

                    # Durum çubuğu
                    ft.Container(
                        content=self.status_text,
                        padding=ft.Padding(16, 4, 16, 4),
                        bgcolor="#18181b",
                    ),

                    # Mesaj giriş alanı — view-once + attach + send
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                self.staged_file_container,
                                self.upload_progress,
                                ft.Row(
                                    controls=[
                                        self.view_once_msg_btn,
                                        self.attach_btn,
                                        self.message_input,
                                        ft.FloatingActionButton(
                                            icon=ft.Icons.SEND_ROUNDED, bgcolor="#8b5cf6",
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
                        bgcolor="#18181b",
                        padding=ft.Padding(12, 10, 12, 10),
                        border=ft.Border(top=ft.BorderSide(1, "#27272a")),
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
            content=ft.Text("?", size=40, color="#ffffff"),
            radius=60,
            bgcolor="#8b5cf6",
        )
        self.call_name_text = ft.Text("Username", size=24, weight=ft.FontWeight.BOLD, color="#ffffff")
        self.call_status_text = ft.Text("Calling...", size=14, color="#a1a1aa")
        self.call_timer_text = ft.Text("00:00", size=14, color="#8b5cf6", visible=False)

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
            icon_color="#ffffff",
            bgcolor="#27272a",
            on_click=lambda e: self.toggle_call_mic(),
            tooltip="Mute Microphone"
        )
        self.cam_btn = ft.IconButton(
            icon=ft.Icons.VIDEOCAM,
            icon_color="#ffffff",
            bgcolor="#27272a",
            on_click=lambda e: self.toggle_call_cam(),
            tooltip="Toggle Video"
        )
        self.end_btn = ft.IconButton(
            icon=ft.Icons.CALL_END,
            icon_color="#ffffff",
            bgcolor="#ef4444",
            icon_size=28,
            width=56,
            height=56,
            on_click=lambda e: self.hangup_call_clicked(),
            tooltip="End Call"
        )

        self.accept_btn = ft.IconButton(
            icon=ft.Icons.CALL,
            icon_color="#ffffff",
            bgcolor="#22c55e",
            icon_size=28,
            width=56,
            height=56,
            on_click=lambda e: self.accept_call_clicked(),
            tooltip="Answer Call"
        )
        self.decline_btn = ft.IconButton(
            icon=ft.Icons.CALL_END,
            icon_color="#ffffff",
            bgcolor="#ef4444",
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
                        bgcolor="#18181b",
                        border_radius=ft.BorderRadius(bottom_left=24, bottom_right=24, top_left=0, top_right=0),
                        shadow=ft.BoxShadow(blur_radius=15, color="#000000aa")
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
            bgcolor="#09090b",
            expand=True
        )

        # ── Ekran Geçişleri ──────────────────────────────────────────
        self.show_login_screen()

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
