"""desktop/bubbles.py — Mesaj/dosya/sistem baloncuğu (chat bubble) inşa mantığı.

client.py'den taşındı (modülerleştirme): create_message_bubble,
create_view_once_bubble, create_file_bubble, create_system_bubble.
`state`, `page`, `chat_list`, `run_on_ui`, `signed_get` referansları `self.`
üzerinden erişilir (MessengerApp tarafından sağlanır).
"""

import base64
import threading
from pathlib import Path

import flet as ft
from desktop.theme import C, t

from crypto_utils import decrypt_message, decrypt_bytes
from desktop.net_config import FILE_ICONS
from desktop.p2p_files import default_download_dir, safe_filename, unique_path


def save_received_file(original_name: str, raw: bytes, base_dir: Path | None = None) -> Path:
    """Alınan dosyayı Downloads/HybridP2P altına güvenle yazar (K3).

    Karşı tarafın verdiği ad temizlenir (yol bileşenleri atılır), var olan dosyanın
    üzerine yazılmaz. Gerçek kaydedilen yolu döndürür."""
    folder = base_dir if base_dir is not None else default_download_dir()
    dest = unique_path(folder, safe_filename(original_name))
    dest.write_bytes(raw)
    return dest


def _bubble_width(text: str, has_card: bool, is_mine: bool, sender: str) -> int | None:
    """Kısa mesajların 300px sabit tuğla gibi görünmesini önler."""
    if has_card:
        return 304
    lines = (text or "").splitlines() or [""]
    longest = max(len(line) for line in lines)
    if not is_mine:
        longest = max(longest, len(sender or ""))
    if longest <= 26 and len(lines) == 1:
        return None
    return min(320, max(160, longest * 8 + 44))


class BubblesMixin:

    def create_link_preview_card(self, preview, is_mine: bool):
        """Link önizleme kartı. preview: depodaki JSON metni (veya dict)."""
        from desktop import linkpreview
        try:
            p = linkpreview.sanitize(preview) if preview else None
        except Exception:
            p = None
        if not p:
            return None
        fg = C.on_accent if is_mine else C.text_bubble_other
        muted = C.on_accent_muted if is_mine else C.text_muted
        parts = []
        if p.get("image"):
            parts.append(
                ft.Image(
                    src="data:image/jpeg;base64," + p["image"],
                    height=130,
                    fit="cover",
                    border_radius=8,
                    width=276,
                )
            )
        parts.append(ft.Text(linkpreview.domain_of(p["url"]), size=10, color=muted))
        if p["title"]:
            parts.append(
                ft.Text(
                    p["title"],
                    size=13,
                    weight=ft.FontWeight.BOLD,
                    color=fg,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )
        if p["description"]:
            parts.append(
                ft.Text(
                    p["description"],
                    size=11,
                    color=muted,
                    max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )
        return ft.Container(
            content=ft.Column(controls=parts, spacing=3, tight=True),
            url=p["url"],
            tooltip=p["url"][:120],
            bgcolor=C.shadow,
            padding=8,
            border_radius=8,
            border=ft.Border(left=ft.BorderSide(3, C.accent_light if is_mine else C.accent)),
        )

    def create_message_bubble(
        self,
        sender: str,
        text: str,
        time_str: str,
        is_mine: bool,
        is_read: bool = True,
        msg_uid: str = None,
        edited: bool = False,
        preview=None,
    ):
        """Metin baloncuğu. Kısa metinlerde doğal dar genişlik, uzun metinlerde 320px sınır."""
        bubble_color = C.accent if is_mine else C.surface_alt
        text_color = C.on_accent if is_mine else C.text_bubble_other
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START

        sub_color = C.on_accent_muted if is_mine else C.text_muted
        time_row_controls = [ft.Text(time_str, size=10, color=sub_color)]
        if edited:
            time_row_controls.insert(0, ft.Text(t("edited"), size=10, italic=True, color=sub_color))
        if is_mine:
            tick_icon = ft.Icon(
                ft.Icons.DONE_ALL if is_read else ft.Icons.DONE,
                size=14,
                color=C.tick_read_on_accent if is_read else C.on_accent_muted,
            )
            time_row_controls.append(tick_icon)

        actionable = is_mine and bool(msg_uid)
        open_menu = (lambda e: self.open_message_actions(msg_uid, text)) if actionable else None
        if actionable:
            time_row_controls.append(
                ft.IconButton(
                    icon=ft.Icons.MORE_VERT,
                    icon_size=14,
                    icon_color=sub_color,
                    tooltip=t("edit_delete_tooltip"),
                    on_click=open_menu,
                    style=ft.ButtonStyle(padding=0),
                    width=20,
                    height=20,
                )
            )

        card = self.create_link_preview_card(preview, is_mine)
        w = _bubble_width(text, card is not None, is_mine, sender)

        time_row = ft.Row(
            controls=time_row_controls,
            spacing=4,
            alignment=ft.MainAxisAlignment.END,
            tight=(w is None),
        )

        body = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        sender,
                        size=11,
                        color=C.accent_light,
                        weight=ft.FontWeight.W_600,
                        visible=not is_mine,
                    ),
                    *([card] if card else []),
                    ft.Text(text, size=14, color=text_color, selectable=True),
                    time_row,
                ],
                spacing=3,
                tight=True,
            ),
            bgcolor=bubble_color,
            padding=ft.Padding(13, 9, 13, 8),
            border_radius=ft.BorderRadius(
                top_left=16,
                top_right=16,
                bottom_left=4 if is_mine else 16,
                bottom_right=16 if is_mine else 4,
            ),
            border=None if is_mine else ft.Border.all(1, C.border),
            width=w,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )
        if actionable:
            body = ft.GestureDetector(
                content=body,
                on_long_press=open_menu,
                on_secondary_tap=open_menu,
            )
        return ft.Row(alignment=align, controls=[body])

    def create_deleted_bubble(self, sender: str, time_str: str, is_mine: bool):
        """"Herkesten silinmiş" mesajın yer tutucusu."""
        return ft.Row(
            alignment=ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START,
            controls=[
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.BLOCK, size=14, color=C.text_muted),
                            ft.Text(t("msg_deleted"), size=13, italic=True, color=C.text_muted),
                            ft.Text(time_str, size=10, color=C.text_faint),
                        ],
                        spacing=6,
                        tight=True,
                    ),
                    bgcolor=C.surface,
                    padding=ft.Padding(12, 8, 12, 8),
                    border_radius=14,
                    border=ft.Border.all(1, C.border),
                ),
            ],
        )

    def create_view_once_bubble(
        self,
        sender: str,
        time_str: str,
        is_mine: bool,
        encrypted_payload: str,
        plaintext_fallback: str = "",
    ):
        """Tek görünümlü mesaj baloncuğu."""
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        color = C.accent if is_mine else C.surface_alt
        fg = C.on_accent if is_mine else C.danger
        sub = C.on_accent_muted if is_mine else C.text_muted
        bubble_row = None

        def on_tap(e):
            nonlocal bubble_row
            if is_mine:
                plaintext = plaintext_fallback or t("view_once_label")
            else:
                try:
                    plaintext = decrypt_message(encrypted_payload, self.state["private_key"])
                except Exception as ex:
                    plaintext = f"[Decrypt error: {ex}]"

            content_text = ft.Text(
                plaintext,
                size=15,
                color=C.text,
                selectable=True,
                text_align=ft.TextAlign.CENTER,
            )

            has_cleaned = False

            def clean_up():
                nonlocal has_cleaned
                if has_cleaned:
                    return
                has_cleaned = True
                try:
                    if bubble_row in self.chat_list.controls:
                        self.chat_list.controls.remove(bubble_row)
                except Exception:
                    pass
                try:
                    self.page.overlay.remove(dialog)
                except Exception:
                    pass
                self.page.update()

            def close_dialog(e):
                dialog.open = False
                self.page.update()
                clean_up()

            dialog = ft.AlertDialog(
                modal=False,
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color=C.danger, size=20),
                                ft.Text(t("view_once_title"), size=14, color=C.danger, weight=ft.FontWeight.BOLD),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE,
                                    icon_color=C.danger,
                                    icon_size=18,
                                    on_click=close_dialog,
                                    tooltip=t("close"),
                                ),
                            ],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(color=C.danger_border, height=1),
                        ft.Container(height=10),
                        content_text,
                        ft.Container(height=12),
                        ft.Text(
                            t("view_once_destroy_msg"),
                            size=11,
                            color=C.danger,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                actions=[ft.TextButton(t("close_delete"), on_click=close_dialog)],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: clean_up(),
                bgcolor=C.surface,
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
                                ft.Icon(ft.Icons.LOOKS_ONE_OUTLINED, color=fg, size=20),
                                ft.Column(
                                    controls=[
                                        ft.Text(
                                            t("sender_label") if not is_mine else t("you_label"),
                                            size=11,
                                            color=sub,
                                            visible=not is_mine,
                                        ),
                                        ft.Text(t("view_once_label"), size=13, color=fg, weight=ft.FontWeight.W_600),
                                        ft.Text(t("tap_to_view"), size=10, color=sub),
                                        ft.Text(time_str, size=9, color=sub),
                                    ],
                                    spacing=1,
                                    tight=True,
                                ),
                            ],
                            spacing=10,
                            tight=True,
                        ),
                        bgcolor=color,
                        padding=ft.Padding(14, 10, 16, 10),
                        border_radius=ft.BorderRadius(
                            top_left=16,
                            top_right=16,
                            bottom_left=4 if is_mine else 16,
                            bottom_right=16 if is_mine else 4,
                        ),
                        border=ft.Border.all(1, C.danger_border),
                    ),
                ),
            ],
        )
        return bubble_row

    def create_file_bubble(
        self,
        sender: str,
        file_uuid: str,
        original_name: str,
        file_type: str,
        time_str: str,
        is_mine: bool,
        view_once: bool = False,
    ):
        """Dosya / resim mesaj baloncuğu."""
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        color = C.accent if is_mine else C.surface_alt
        icon = FILE_ICONS.get(file_type, ft.Icons.DESCRIPTION)
        fg = C.on_accent if is_mine else C.text
        sub_fg = C.on_accent_muted if is_mine else C.accent_light
        bubble_row = None

        status_text = ft.Text(t("download"), size=11, color=sub_fg)
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
                except Exception:
                    pass
                try:
                    self.page.overlay.remove(dialog)
                except Exception:
                    pass
                self.page.update()

            def close_dialog(e):
                dialog.open = False
                self.page.update()
                clean_up()

            dialog = ft.AlertDialog(
                modal=False,
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color=C.danger, size=20),
                                ft.Text(t("view_once_file_title"), size=14, color=C.danger, weight=ft.FontWeight.BOLD),
                                ft.Container(expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.CLOSE,
                                    icon_color=C.danger,
                                    icon_size=18,
                                    on_click=close_dialog,
                                    tooltip=t("close"),
                                ),
                            ],
                            spacing=8,
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(color=C.danger_border, height=1),
                        ft.Container(height=10),
                        content_control,
                        ft.Container(height=12),
                        ft.Text(message_text, size=11, color=C.danger, text_align=ft.TextAlign.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                actions=[ft.TextButton(t("close_delete"), on_click=close_dialog)],
                actions_alignment=ft.MainAxisAlignment.END,
                on_dismiss=lambda e: clean_up(),
                bgcolor=C.surface,
            )
            self.page.overlay.append(dialog)
            dialog.open = True
            self.page.update()

        def on_download(e):
            if view_once and is_mine:
                show_view_once_dialog(
                    ft.Text(t("sent_file_fmt", name=original_name), size=14, color=C.text),
                    t("view_once_destroy_msg"),
                )
                return

            status_text.value = t("downloading")
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
                                    width=300,
                                    height=250,
                                    fit="contain",
                                    border_radius=8,
                                )
                                self.run_on_ui(show_view_once_dialog, img, t("view_once_file_destroy"))
                            else:
                                dest = save_received_file(original_name, raw)

                                def _show_file_vo():
                                    show_view_once_dialog(
                                        ft.Text(
                                            t("downloaded_saved_fmt", name=dest.name),
                                            size=13,
                                            color=C.text,
                                            text_align=ft.TextAlign.CENTER,
                                        ),
                                        t("view_once_saved_destroy"),
                                    )

                                self.run_on_ui(_show_file_vo)
                        else:
                            if file_type == "image":
                                b64 = base64.b64encode(raw).decode("ascii")
                                ext = Path(original_name).suffix.lstrip(".")
                                data_url = f"data:image/{ext or 'png'};base64,{b64}"
                                img = ft.Image(
                                    src=data_url,
                                    width=250,
                                    height=200,
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
                                dest = save_received_file(original_name, raw)

                                def _show_normal_file():
                                    status_text.value = t("saved_fmt", name=dest.name)
                                    self.page.update()

                                self.run_on_ui(_show_normal_file)
                    else:
                        def _failed():
                            status_text.value = t("download_failed")
                            self.page.update()

                        self.run_on_ui(_failed)
                except Exception as ex:
                    def _err(ex=ex):
                        status_text.value = f"Error: {ex}"
                        self.page.update()

                    self.run_on_ui(_err)

            threading.Thread(target=do_download, daemon=True).start()

        vo_badge = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.VISIBILITY, size=10, color=C.danger),
                    ft.Text(t("view_once_file_badge"), size=9, color=C.danger),
                ],
                spacing=2,
            ),
            visible=view_once,
        )

        bubble_content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        sender,
                        size=11,
                        color=C.accent_light,
                        weight=ft.FontWeight.W_600,
                        visible=not is_mine,
                    ),
                    vo_badge,
                    ft.Row(
                        controls=[
                            ft.Icon(icon, size=22, color=fg),
                            ft.Column(
                                controls=[
                                    ft.Text(
                                        original_name,
                                        size=12,
                                        color=fg,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    status_text,
                                ],
                                spacing=1,
                                tight=True,
                                expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DOWNLOAD if not view_once else ft.Icons.VISIBILITY,
                                icon_color=C.danger if view_once else fg,
                                icon_size=18,
                                on_click=on_download,
                                tooltip=t("tap_to_view") if view_once else t("download"),
                            ),
                        ],
                        spacing=6,
                    ),
                    image_display,
                    ft.Text(time_str, size=10, color=C.on_accent_muted if is_mine else C.text_muted),
                ],
                spacing=4,
                tight=True,
            ),
            bgcolor=color,
            padding=ft.Padding(13, 10, 13, 8),
            border_radius=ft.BorderRadius(
                top_left=16,
                top_right=16,
                bottom_left=4 if is_mine else 16,
                bottom_right=16 if is_mine else 4,
            ),
            border=(
                ft.Border.all(1, C.danger_border)
                if view_once
                else (None if is_mine else ft.Border.all(1, C.border))
            ),
            width=284,
        )

        if view_once:
            bubble_row = ft.Row(
                alignment=align,
                controls=[ft.GestureDetector(on_tap=on_download, content=bubble_content)],
            )
        else:
            bubble_row = ft.Row(alignment=align, controls=[bubble_content])
        return bubble_row

    def create_voice_bubble(self, sender: str, time_str: str, is_mine: bool, duration: float, load_audio):
        """Sesli mesaj baloncuğu: ▶/■ + süre."""
        from desktop import voice

        fg = C.on_accent if is_mine else C.text
        sub = C.on_accent_muted if is_mine else C.text_muted
        status = ft.Text(voice.fmt_duration(duration), size=12, color=fg)
        play_btn = ft.IconButton(
            icon=ft.Icons.PLAY_ARROW_ROUNDED,
            icon_color=fg,
            icon_size=26,
            tooltip="Play",
        )
        playing = {"on": False}

        def _set_idle():
            playing["on"] = False
            play_btn.icon = ft.Icons.PLAY_ARROW_ROUNDED
            play_btn.tooltip = "Play"
            try:
                self.page.update()
            except Exception:
                pass

        def _play_worker():
            data = load_audio()
            if not data:
                def _missing():
                    status.value = t("audio_missing")
                    _set_idle()

                self.run_on_ui(_missing)
                return
            try:
                pcm, sr = voice.decode_audio(data)
                voice.player.play(pcm, sr, on_done=lambda: self.run_on_ui(_set_idle))
            except Exception as ex:
                print(f"[Voice] Playback error: {ex}")
                self.log_status("Could not play voice message (audio device?).")
                self.run_on_ui(_set_idle)

        def on_play(e):
            if playing["on"]:
                voice.player.stop()
                return
            playing["on"] = True
            play_btn.icon = ft.Icons.STOP_ROUNDED
            play_btn.tooltip = "Stop"
            self.page.update()
            threading.Thread(target=_play_worker, daemon=True).start()

        play_btn.on_click = on_play

        return ft.Row(
            alignment=ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START,
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(
                                sender,
                                size=11,
                                color=C.accent_light,
                                weight=ft.FontWeight.W_600,
                                visible=not is_mine,
                            ),
                            ft.Row(
                                controls=[
                                    play_btn,
                                    ft.Icon(ft.Icons.MIC, size=16, color=sub),
                                    status,
                                ],
                                spacing=4,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Text(time_str, size=10, color=sub),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    bgcolor=C.accent if is_mine else C.surface_alt,
                    padding=ft.Padding(8, 4, 14, 8),
                    border_radius=ft.BorderRadius(
                        top_left=16,
                        top_right=16,
                        bottom_left=4 if is_mine else 16,
                        bottom_right=16 if is_mine else 4,
                    ),
                    border=None if is_mine else ft.Border.all(1, C.border),
                    width=210,
                ),
            ],
        )

    def create_date_separator(self, label: str):
        """Gün değişimlerinde sohbete eklenen tarih ayracı."""
        return ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    content=ft.Text(
                        label,
                        size=10,
                        color=C.text_secondary,
                        weight=ft.FontWeight.W_600,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    bgcolor=C.date_sep_bg,
                    padding=ft.Padding(12, 4, 12, 4),
                    border_radius=12,
                    border=ft.Border.all(1, C.border),
                ),
            ],
        )

    def create_system_bubble(self, text: str):
        return ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    content=ft.Text(
                        text,
                        size=11,
                        color=C.text_system,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    bgcolor=C.surface,
                    padding=ft.Padding(14, 6, 14, 6),
                    border_radius=12,
                    border=ft.Border.all(1, C.border),
                ),
            ],
        )
