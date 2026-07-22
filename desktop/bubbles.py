"""desktop/bubbles.py — Mesaj/dosya/sistem baloncuğu (chat bubble) inşa mantığı.

client.py'den taşındı (modülerleştirme): create_message_bubble,
create_view_once_bubble, create_file_bubble, create_system_bubble.
Eskiden main() içinde closure'dı; artık BubblesMixin metotları. `state`,
`page`, `chat_list`, `run_on_ui`, `signed_get` referansları `self.` üzerinden
erişilir (MessengerApp tarafından sağlanır).
"""

import base64
import threading
from pathlib import Path

import flet as ft

from crypto_utils import decrypt_message, decrypt_bytes
from desktop.net_config import FILE_ICONS


class BubblesMixin:

    def create_message_bubble(self, sender: str, text: str, time_str: str, is_mine: bool, is_read: bool = True):
        bubble_color = "#8b5cf6" if is_mine else "#27272a"
        text_color   = "#ffffff" if is_mine else "#e0e0e0"
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START

        # Build timestamp row containing tick status icons for sender's messages
        time_row_controls = [
            ft.Text(time_str, size=10, color="#888888")
        ]
        if is_mine:
            tick_icon = ft.Icon(
                ft.Icons.DONE_ALL if is_read else ft.Icons.DONE,
                size=14,
                color="#22c55e" if is_read else "#71717a"
            )
            time_row_controls.append(tick_icon)

        time_row = ft.Row(
            controls=time_row_controls,
            spacing=4,
            alignment=ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START,
            tight=True
        )

        return ft.Row(
            alignment=align,
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(sender, size=11, color="#9e9e9e",
                                    weight=ft.FontWeight.BOLD, visible=not is_mine),
                            ft.Text(text, size=14, color=text_color, selectable=True),
                            time_row,
                        ],
                        spacing=2, tight=True,
                    ),
                    bgcolor=bubble_color,
                    padding=ft.Padding(14, 10, 14, 10),
                    border_radius=ft.BorderRadius(
                        top_left=14, top_right=14,
                        bottom_left=4 if is_mine else 14,
                        bottom_right=14 if is_mine else 4,
                    ),
                    width=300,
                    shadow=ft.BoxShadow(blur_radius=8, color="#00000033", offset=ft.Offset(0, 2)),
                    animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
                ),
            ],
        )

    def create_view_once_bubble(self, sender: str, time_str: str, is_mine: bool,
                                 encrypted_payload: str, plaintext_fallback: str = ""):
        """
        Tek görünümlü mesaj baloncuğu.
        Tıklanınca içerik diyalogda gösterilir, kapanınca silinir.
        """
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        color = "#8b5cf6" if is_mine else "#27272a"
        bubble_row = None

        def on_tap(e):
            nonlocal bubble_row
            if is_mine:
                plaintext = plaintext_fallback or "View-once message sent."
            else:
                try:
                    plaintext = decrypt_message(encrypted_payload, self.state["private_key"])
                except Exception as ex:
                    plaintext = f"[Cozme hatasi: {ex}]"

            content_text = ft.Text(plaintext, size=15, color="#ffffff",
                                   selectable=True, text_align=ft.TextAlign.CENTER)

            has_cleaned = False
            def clean_up():
                nonlocal has_cleaned
                if has_cleaned:
                    return
                has_cleaned = True
                try:
                    if bubble_row in self.chat_list.controls:
                        self.chat_list.controls.remove(bubble_row)
                except:
                    pass
                try:
                    self.page.overlay.remove(dialog)
                except:
                    pass
                self.page.update()

            def close_dialog(e):
                dialog.open = False
                self.page.update()
                clean_up()

            dialog = ft.AlertDialog(
                modal=False,  # Herhangi bir yere tıklayınca da kapansın
                content=ft.Column(
                    controls=[
                        # Header Row (interactive elements inside content to avoid click blocking in title)
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color="#ef4444", size=20),
                                ft.Text("View-Once Message", size=14, color="#ef4444", weight=ft.FontWeight.BOLD),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE,
                                    icon_color="#ef4444",
                                    icon_size=18,
                                    on_click=close_dialog,
                                    tooltip="Close",
                                ),
                            ],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(color="#ef444444", height=1),
                        ft.Container(height=10),
                        content_text,
                        ft.Container(height=12),
                        ft.Text("This message will be permanently deleted from the chat once closed.",
                                size=11, color="#ef4444", text_align=ft.TextAlign.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Close (Delete)", on_click=close_dialog)
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: clean_up(),
                bgcolor="#18181b",
            )
            self.page.overlay.append(dialog)
            dialog.open = True
            self.page.update()

        bubble_row = ft.Row(
            alignment=align,
            controls=[
                ft.GestureDetector(
                    on_tap=on_tap,
                    content=ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color="#ef4444", size=18),
                                ft.Column(
                                    controls=[
                                        ft.Text(
                                            "Sender" if not is_mine else "You",
                                            size=11, color="#9e9e9e", visible=not is_mine
                                        ),
                                        ft.Text("View-once message",
                                                size=13, color="#ef4444"),
                                        ft.Text("Tap to view",
                                                size=10, color="#888888"),
                                        ft.Text(time_str, size=9, color="#666666"),
                                    ],
                                    spacing=1, tight=True,
                                ),
                            ],
                            spacing=8,
                        ),
                        bgcolor=color,
                        padding=ft.Padding(14, 10, 14, 10),
                        border_radius=ft.BorderRadius(
                            top_left=14, top_right=14,
                        bottom_left=4 if is_mine else 14,
                        bottom_right=14 if is_mine else 4,
                        ),
                        border=ft.Border(left=ft.BorderSide(1, "#ef444444"), top=ft.BorderSide(1, "#ef444444"), right=ft.BorderSide(1, "#ef444444"), bottom=ft.BorderSide(1, "#ef444444")),
                        width=260,
                    ),
                ),
            ],
        )
        return bubble_row

    def create_file_bubble(self, sender: str, file_uuid: str, original_name: str,
                            file_type: str, time_str: str, is_mine: bool,
                            view_once: bool = False):
        """
        Dosya / resim mesaj baloncuğu.
        Resimler için indirme sonrası thumbnail gösterilir.
        """
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        color = "#8b5cf6" if is_mine else "#27272a"
        icon  = FILE_ICONS.get(file_type, ft.Icons.DESCRIPTION)
        bubble_row = None

        # İndirme durumu için durum göstergesi
        status_text = ft.Text("Download", size=11, color="#a78bfa")
        image_display = ft.Column(controls=[], visible=False)

        def show_view_once_dialog(content_control, message_text):
            nonlocal bubble_row

            has_cleaned = False
            def clean_up():
                nonlocal has_cleaned
                if has_cleaned:
                    return
                has_cleaned = True
                try:
                    if bubble_row in self.chat_list.controls:
                        self.chat_list.controls.remove(bubble_row)
                except:
                    pass
                try:
                    self.page.overlay.remove(dialog)
                except:
                    pass
                self.page.update()

            def close_dialog(e):
                dialog.open = False
                self.page.update()
                clean_up()

            dialog = ft.AlertDialog(
                modal=False,  # Herhangi bir yere tıklayınca da kapansın
                content=ft.Column(
                    controls=[
                        # Header Row
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color="#ef4444", size=20),
                                ft.Text("View-Once File", size=14, color="#ef4444", weight=ft.FontWeight.BOLD),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE,
                                    icon_color="#ef4444",
                                    icon_size=18,
                                    on_click=close_dialog,
                                    tooltip="Close",
                                ),
                            ],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(color="#ef444444", height=1),
                        ft.Container(height=10),
                        content_control,
                        ft.Container(height=12),
                        ft.Text(message_text,
                                size=11, color="#ef4444", text_align=ft.TextAlign.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Close (Delete)", on_click=close_dialog)
                ],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: clean_up(),
                bgcolor="#18181b",
            )
            self.page.overlay.append(dialog)
            dialog.open = True
            self.page.update()

        def on_download(e):
            if view_once and is_mine:
                show_view_once_dialog(
                    ft.Text(f"Gonderdiginiz dosya: {original_name}", size=14, color="#ffffff"),
                    "This message will be permanently deleted from the chat once closed."
                )
                return

            status_text.value = "Downloading..."
            self.page.update()

            def do_download():
                try:
                    resp = self.signed_get(f"/api/download_file/{file_uuid}", timeout=30)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw = decrypt_bytes(data["encrypted_data"], self.state["private_key"])

                        if view_once:
                            if file_type == "image":
                                b64 = base64.b64encode(raw).decode("ascii")
                                ext = Path(original_name).suffix.lstrip(".")
                                data_url = f"data:image/{ext or 'png'};base64,{b64}"
                                img = ft.Image(
                                    src=data_url,
                                    width=300, height=250,
                                    fit="contain",
                                    border_radius=8,
                                )
                                self.run_on_ui(show_view_once_dialog, img, "This file will be permanently deleted from the chat once closed.")
                            else:
                                downloads = Path.home() / "Downloads"
                                downloads.mkdir(exist_ok=True)
                                dest = downloads / original_name
                                dest.write_bytes(raw)
                                def _show_file_vo():
                                    show_view_once_dialog(
                                        ft.Text(f"Dosya indirildi ve kaydedildi:\n{dest.name}", size=13, color="#ffffff", text_align=ft.TextAlign.CENTER),
                                        "This file has been saved to your local Downloads folder. It will be permanently deleted from the chat once closed."
                                    )
                                self.run_on_ui(_show_file_vo)
                        else:
                            if file_type == "image":
                                b64 = base64.b64encode(raw).decode("ascii")
                                ext = Path(original_name).suffix.lstrip(".")
                                data_url = f"data:image/{ext or 'png'};base64,{b64}"
                                img = ft.Image(
                                    src=data_url,
                                    width=250, height=200,
                                    fit="contain",
                                    border_radius=8,
                                )
                                def _show_normal_img():
                                    image_display.controls.clear()
                                    image_display.controls.append(img)
                                    image_display.visible = True
                                    status_text.value = original_name
                                    self.page.update()
                                self.run_on_ui(_show_normal_img)
                            else:
                                downloads = Path.home() / "Downloads"
                                downloads.mkdir(exist_ok=True)
                                dest = downloads / original_name
                                dest.write_bytes(raw)
                                def _show_normal_file():
                                    status_text.value = f"Kaydedildi: {dest.name}"
                                    self.page.update()
                                self.run_on_ui(_show_normal_file)
                    else:
                        def _failed():
                            status_text.value = "Download failed or already downloaded."
                            self.page.update()
                        self.run_on_ui(_failed)
                except Exception as ex:
                    def _err():
                        status_text.value = f"Hata: {ex}"
                        self.page.update()
                    self.run_on_ui(_err)

            threading.Thread(target=do_download, daemon=True).start()

        vo_badge = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.VISIBILITY, size=10, color="#ef4444"),
                    ft.Text("View-once", size=9, color="#ef4444"),
                ],
                spacing=2,
            ),
            visible=view_once,
        )

        bubble_content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(sender, size=11, color="#9e9e9e",
                            weight=ft.FontWeight.BOLD, visible=not is_mine),
                    vo_badge,
                    ft.Row(
                        controls=[
                            ft.Icon(icon, size=24, color="#a78bfa"),
                            ft.Column(
                                controls=[
                                    ft.Text(original_name, size=12,
                                            color="#ffffff", max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS),
                                    status_text,
                                ],
                                spacing=1, tight=True, expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DOWNLOAD if not view_once else ft.Icons.VISIBILITY,
                                icon_color="#ef4444" if view_once else "#a78bfa",
                                icon_size=18,
                                on_click=on_download,
                                tooltip="View" if view_once else "Download & Decrypt",
                            ),
                        ],
                        spacing=6,
                    ),
                    image_display,
                    ft.Text(time_str, size=10, color="#888888"),
                ],
                spacing=4, tight=True,
            ),
            bgcolor=color,
            padding=ft.Padding(14, 10, 14, 10),
            border_radius=ft.BorderRadius(
                top_left=14, top_right=14,
                        bottom_left=4 if is_mine else 14,
                        bottom_right=14 if is_mine else 4,
            ),
            border=ft.Border(left=ft.BorderSide(1, "#ef444444"), top=ft.BorderSide(1, "#ef444444"), right=ft.BorderSide(1, "#ef444444"), bottom=ft.BorderSide(1, "#ef444444")) if view_once else None,
            width=300,
            shadow=ft.BoxShadow(blur_radius=8, color="#00000033",
                                offset=ft.Offset(0, 2)),
        )

        if view_once:
            bubble_row = ft.Row(
                alignment=align,
                controls=[
                    ft.GestureDetector(
                        on_tap=on_download,
                        content=bubble_content,
                    )
                ]
            )
        else:
            bubble_row = ft.Row(
                alignment=align,
                controls=[
                    bubble_content
                ]
            )
        return bubble_row

    def create_system_bubble(self, text: str):
        return ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    content=ft.Text(text, size=11, color="#aaaaaa",
                                    text_align=ft.TextAlign.CENTER),
                    bgcolor="#27272a",
                    padding=ft.Padding(16, 6, 16, 6),
                    border_radius=8,
                    border=ft.Border(left=ft.BorderSide(1, "#3f3f46"), top=ft.BorderSide(1, "#3f3f46"), right=ft.BorderSide(1, "#3f3f46"), bottom=ft.BorderSide(1, "#3f3f46")),
                ),
            ],
        )
