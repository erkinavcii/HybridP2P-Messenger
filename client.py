"""
client.py — Flet Tabanlı E2EE Mesajlaşma İstemcisi
=====================================================
v3.0 — View-Once Mesaj + Dosya/Resim Gönderimi

Yeni Özellikler (v3.0):
  • Tek görünümlü mesaj (view-once): per-mesaj 👁 toggle, otomatik kapanır
  • Dosya/resim gönderimi: AES-GCM ile şifreli upload → UUID → receiver indirir
  • Resimler inline thumbnail olarak gösterilir
  • Diğer dosyalar kaydet butonuyla indirilir
"""

import io
import os
import json
import asyncio
import threading
import tempfile
import base64
import mimetypes
from pathlib import Path
from datetime import datetime

import requests
import flet as ft

from crypto_utils import (
    generate_rsa_keypair,
    save_keys_to_disk,
    load_keys_from_disk,
    public_key_to_pem_string,
    pem_string_to_public_key,
    get_public_key_fingerprint,
    encrypt_message,
    decrypt_message,
    encrypt_bytes,
    decrypt_bytes,
    encrypt_symmetric,
    decrypt_symmetric,
)
from message_store import MessageStore

# ── Sunucu Ayarları ─────────────────────────────────────────────────
SERVER_HOST = "127.0.0.1"
SERVER_PORT  = 8000
BASE_URL     = f"http://{SERVER_HOST}:{SERVER_PORT}"
WS_URL       = f"ws://{SERVER_HOST}:{SERVER_PORT}"

# Dosya tipi → ikon eşlemesi
FILE_ICONS = {
    "image":    ft.Icons.IMAGE,
    "video":    ft.Icons.VIDEO_FILE,
    "audio":    ft.Icons.AUDIO_FILE,
    "document": ft.Icons.DESCRIPTION,
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg"}


def _guess_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        if mime.startswith("video"): return "video"
        if mime.startswith("audio"): return "audio"
    return "document"


def main(page: ft.Page):

    # ── Sayfa Ayarları ────────────────────────────────────────────────
    page.title       = "HybridP2P Messenger"
    page.theme_mode  = ft.ThemeMode.DARK
    page.window.width  = 480
    page.window.height = 820
    page.padding     = 0
    page.bgcolor     = "#0a0e1a"
    page.theme       = ft.Theme(color_scheme_seed="#6c63ff", font_family="Segoe UI")

    # ── Uygulama Durumu ──────────────────────────────────────────────
    state = {
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
    }

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     MESAJ BALONCULARI                          ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def create_message_bubble(sender: str, text: str, time_str: str, is_mine: bool):
        bubble_color = "#6c63ff" if is_mine else "#1e2337"
        text_color   = "#ffffff" if is_mine else "#e0e0e0"
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        return ft.Row(
            alignment=align,
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(sender, size=11, color="#9e9e9e",
                                    weight=ft.FontWeight.BOLD, visible=not is_mine),
                            ft.Text(text, size=14, color=text_color, selectable=True),
                            ft.Text(time_str, size=10, color="#888888",
                                    text_align=ft.TextAlign.RIGHT if is_mine else ft.TextAlign.LEFT),
                        ],
                        spacing=2, tight=True,
                    ),
                    bgcolor=bubble_color,
                    padding=ft.Padding(14, 10, 14, 10),
                    border_radius=ft.BorderRadius(
                        top_left=18, top_right=18,
                        bottom_left=4 if is_mine else 18,
                        bottom_right=18 if is_mine else 4,
                    ),
                    width=300,
                    shadow=ft.BoxShadow(blur_radius=8, color="#00000033", offset=ft.Offset(0, 2)),
                    animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
                ),
            ],
        )

    def create_view_once_bubble(sender: str, time_str: str, is_mine: bool,
                                 encrypted_payload: str):
        """
        Tek görünümlü mesaj baloncuğu.
        Tıklanınca içerik diyalogda gösterilir, kapanınca silinir.
        """
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        color = "#6c63ff" if is_mine else "#1e2337"

        def on_tap(e):
            try:
                plaintext = decrypt_message(encrypted_payload, state["private_key"])
            except Exception as ex:
                plaintext = f"[Cozme hatasi: {ex}]"

            # Countdown sayacı
            countdown_text = ft.Text("10", size=24, color="#ff6b6b",
                                      weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER)
            content_text   = ft.Text(plaintext, size=15, color="#ffffff",
                                      selectable=False, text_align=ft.TextAlign.CENTER)

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.VISIBILITY, color="#ff6b6b", size=20),
                        ft.Text("Tek Gorunumlu Mesaj", size=14, color="#ff6b6b"),
                    ],
                    spacing=8,
                ),
                content=ft.Column(
                    controls=[
                        content_text,
                        ft.Container(height=8),
                        ft.Text("Kapanmaya kadar:", size=11, color="#9e9e9e",
                                 text_align=ft.TextAlign.CENTER),
                        countdown_text,
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    tight=True,
                ),
                bgcolor="#141832",
            )
            page.overlay.append(dialog)
            dialog.open = True
            page.update()

            # 10s countdown
            def countdown():
                for i in range(9, -1, -1):
                    import time; time.sleep(1)
                    countdown_text.value = str(i)
                    try: page.update()
                    except: pass
                dialog.open = False
                try:
                    page.overlay.remove(dialog)
                    page.update()
                except: pass

            threading.Thread(target=countdown, daemon=True).start()

        return ft.Row(
            alignment=align,
            controls=[
                ft.GestureDetector(
                    on_tap=on_tap,
                    content=ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.VISIBILITY, color="#ff6b6b", size=18),
                                ft.Column(
                                    controls=[
                                        ft.Text(
                                            "Gonderici" if not is_mine else "Sen",
                                            size=11, color="#9e9e9e", visible=not is_mine
                                        ),
                                        ft.Text("Tek gorunumlu mesaj",
                                                size=13, color="#ff6b6b"),
                                        ft.Text("Gormek icin dokun",
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
                            top_left=18, top_right=18,
                            bottom_left=4 if is_mine else 18,
                            bottom_right=18 if is_mine else 4,
                        ),
                        border=ft.Border(left=ft.BorderSide(1, "#ff6b6b44"), top=ft.BorderSide(1, "#ff6b6b44"), right=ft.BorderSide(1, "#ff6b6b44"), bottom=ft.BorderSide(1, "#ff6b6b44")),
                        width=260,
                    ),
                ),
            ],
        )

    def create_file_bubble(sender: str, file_uuid: str, original_name: str,
                            file_type: str, time_str: str, is_mine: bool,
                            view_once: bool = False):
        """
        Dosya / resim mesaj baloncuğu.
        Resimler için indirme sonrası thumbnail gösterilir.
        """
        align = ft.MainAxisAlignment.END if is_mine else ft.MainAxisAlignment.START
        color = "#6c63ff" if is_mine else "#1e2337"
        icon  = FILE_ICONS.get(file_type, ft.Icons.DESCRIPTION)

        # İndirme durumu için durum göstergesi
        status_text = ft.Text("Indir", size=11, color="#a0a0ff")
        image_display = ft.Column(controls=[], visible=False)

        def on_download(e):
            status_text.value = "Indiriliyor..."
            page.update()

            def do_download():
                try:
                    headers = make_auth_headers(state["username"], state["private_key"])
                    resp = requests.get(
                        f"{BASE_URL}/api/download_file/{file_uuid}",
                        headers=headers,
                        timeout=30
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        raw = decrypt_bytes(data["encrypted_data"], state["private_key"])

                        if file_type == "image":
                            # Resmi base64 olarak Flet Image'e ver
                            b64 = base64.b64encode(raw).decode("ascii")
                            ext = Path(original_name).suffix.lstrip(".")
                            data_url = f"data:image/{ext or 'png'};base64,{b64}"
                            img = ft.Image(
                                src=data_url,
                                width=250, height=200,
                                fit=ft.ImageFit.CONTAIN,
                                border_radius=8,
                            )
                            image_display.controls.append(img)
                            image_display.visible = True
                            status_text.value = original_name
                        else:
                            # Dosyayı indirilenler klasörüne kaydet
                            downloads = Path.home() / "Downloads"
                            downloads.mkdir(exist_ok=True)
                            dest = downloads / original_name
                            dest.write_bytes(raw)
                            status_text.value = f"Kaydedildi: {dest}"

                        if view_once:
                            # Tek gorunumluse dosyayı göster sonra kaldır
                            page.update()
                            import time; time.sleep(10)
                            image_display.visible = False
                            image_display.controls.clear()
                            status_text.value = "Silindi (tek gorunumlu)"

                        page.update()
                    else:
                        status_text.value = "Indirme basarisiz veya zaten indirildi."
                        page.update()
                except Exception as ex:
                    status_text.value = f"Hata: {ex}"
                    page.update()

            threading.Thread(target=do_download, daemon=True).start()

        vo_badge = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.VISIBILITY, size=10, color="#ff6b6b"),
                    ft.Text("Tek gorunumlu", size=9, color="#ff6b6b"),
                ],
                spacing=2,
            ),
            visible=view_once,
        )

        return ft.Row(
            alignment=align,
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(sender, size=11, color="#9e9e9e",
                                    weight=ft.FontWeight.BOLD, visible=not is_mine),
                            vo_badge,
                            ft.Row(
                                controls=[
                                    ft.Icon(icon, size=24, color="#a0a0ff"),
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
                                        icon=ft.Icons.DOWNLOAD,
                                        icon_color="#a0a0ff",
                                        icon_size=18,
                                        on_click=on_download,
                                        tooltip="Indir ve Coz",
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
                        top_left=18, top_right=18,
                        bottom_left=4 if is_mine else 18,
                        bottom_right=18 if is_mine else 4,
                    ),
                    width=300,
                    shadow=ft.BoxShadow(blur_radius=8, color="#00000033",
                                        offset=ft.Offset(0, 2)),
                ),
            ],
        )

    def create_system_bubble(text: str):
        return ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    content=ft.Text(text, size=11, color="#aaaaaa",
                                    text_align=ft.TextAlign.CENTER),
                    bgcolor="#151a2e",
                    padding=ft.Padding(16, 6, 16, 6),
                    border_radius=12,
                    border=ft.Border(left=ft.BorderSide(1, "#2a2f4e"), top=ft.BorderSide(1, "#2a2f4e"), right=ft.BorderSide(1, "#2a2f4e"), bottom=ft.BorderSide(1, "#2a2f4e")),
                ),
            ],
        )

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     ANAHTAR YÖNETİMİ                           ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def initialize_keys(username: str):
        priv, pub = load_keys_from_disk(username)
        if priv and pub:
            log_status("Mevcut anahtarlar yuklendi.")
            return priv, pub
        log_status("RSA-4096 uretiliyor...")
        page.update()
        priv, pub = generate_rsa_keypair()
        save_keys_to_disk(username, priv, pub)
        log_status("Anahtarlar olusturuldu.")
        return priv, pub

    def make_auth_headers(username: str, private_key) -> dict:
        if not username or not private_key:
            return {}
        from datetime import datetime, timezone
        import base64
        from crypto_utils import sign_data
        
        timestamp = datetime.now(timezone.utc).isoformat()
        data_to_sign = f"{username}:{timestamp}".encode("utf-8")
        sig = sign_data(private_key, data_to_sign)
        sig_b64 = base64.b64encode(sig).decode("ascii")
        
        return {
            "X-Username": username,
            "X-Timestamp": timestamp,
            "X-Signature": sig_b64
        }

    def register_with_server(username: str, public_key, private_key) -> bool:
        try:
            from datetime import datetime, timezone
            import base64
            from crypto_utils import sign_data
            
            pem_key = public_key_to_pem_string(public_key)
            timestamp = datetime.now(timezone.utc).isoformat()
            
            data_to_sign = f"{username}:{timestamp}:{pem_key}".encode("utf-8")
            sig = sign_data(private_key, data_to_sign)
            sig_b64 = base64.b64encode(sig).decode("ascii")

            resp = requests.post(f"{BASE_URL}/api/register", json={
                "username": username,
                "public_key": pem_key,
                "timestamp": timestamp,
                "signature": sig_b64
            }, timeout=5)
            return resp.status_code == 200
        except Exception as ex:
            print(f"[Register Error] {ex}")
            return False

    def fetch_recipient_pub_key(recipient: str):
        try:
            headers = make_auth_headers(state["username"], state["private_key"])
            resp = requests.get(f"{BASE_URL}/api/public_key/{recipient}", headers=headers, timeout=5)
            if resp.status_code == 200:
                return pem_string_to_public_key(resp.json()["public_key"])
        except: pass
        return None

    def sync_chat_settings():
        if not state["username"]: return
        try:
            headers = make_auth_headers(state["username"], state["private_key"])
            resp = requests.get(
                f"{BASE_URL}/api/chat_settings/{state['username']}", headers=headers, timeout=5
            )
            if resp.status_code == 200:
                for s in resp.json().get("settings", []):
                    parts   = s["chat_id"].split("_", 1)
                    partner = parts[0] if len(parts) > 1 and parts[1] == state["username"] else parts[-1]
                    if state["store"]:
                        state["store"].set_ephemeral(partner, s["ephemeral"], s.get("changed_by"))
                    if partner == state["recipient"]:
                        state["ephemeral"] = s["ephemeral"]
                        _update_ephemeral_ui()
        except: pass

    def fetch_offline_messages():
        print(f"[REST] Sunucudan cevrimdisi mesajlar talep ediliyor ({state['username']})...")
        try:
            headers = make_auth_headers(state["username"], state["private_key"])
            resp = requests.get(
                f"{BASE_URL}/api/fetch_messages/{state['username']}", headers=headers, timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if data["count"]:
                    log_status(f"{data['count']} cevrimdisi mesaj alindi.")
                    print(f"[REST] {data['count']} yeni cevrimdisi mesaj alindi.")
                else:
                    print("[REST] Cevrimdisi mesaj yok.")
                for msg in data["messages"]:
                    try:
                        pt = decrypt_message(msg["encrypted_payload"], state["private_key"])
                        print(f"[REST] '{msg['sender']}' kullanicisindan gelen cevrimdisi mesaj basariyla cozuldu.")
                        _on_incoming_message(msg["sender"], pt, msg.get("timestamp", ""),
                                             bool(msg.get("view_once", False)),
                                             msg["encrypted_payload"])
                    except Exception as ex:
                        print(f"[REST] Mesaj cozme hatasi: {ex}")
                        _on_incoming_message(msg["sender"], f"[Hata: {ex}]",
                                             msg.get("timestamp", ""))
        except Exception as e:
            log_status("Cevrimdisi mesajlar alinamadi.")
            print(f"[REST] Cevrimdisi mesajlar sunucudan cekilemedi: {e}")

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     CHAT LİSTESİ YÖNETİMİ                     ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def _fmt_time(ts: str) -> str:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%H:%M")
        except: return ts[:5] if ts else ""

    def add_message_to_chat(sender: str, text: str, is_mine: bool,
                             time_str: str = "", save: bool = True,
                             view_once: bool = False, encrypted_payload: str = ""):
        if not time_str: time_str = datetime.now().strftime("%H:%M")
        else: time_str = _fmt_time(time_str)

        if view_once and not is_mine:
            bubble = create_view_once_bubble(sender, time_str, is_mine, encrypted_payload)
        else:
            bubble = create_message_bubble(sender, text, time_str, is_mine)

        chat_list.controls.append(bubble)

        if save and state["recipient"] and state["store"] and not view_once:
            state["store"].save_message(
                partner=state["recipient"], sender=sender,
                content=text, is_mine=is_mine,
            )
        try: page.update()
        except: pass

    def add_file_to_chat(sender: str, file_uuid: str, original_name: str,
                          file_type: str, is_mine: bool, time_str: str = "",
                          view_once: bool = False):
        if not time_str: time_str = datetime.now().strftime("%H:%M")
        else: time_str = _fmt_time(time_str)

        bubble = create_file_bubble(sender, file_uuid, original_name,
                                     file_type, time_str, is_mine, view_once)
        chat_list.controls.append(bubble)
        try: page.update()
        except: pass

    def add_system_event(text: str, partner: str = None):
        chat_list.controls.append(create_system_bubble(text))
        if partner and state["store"] and not state["ephemeral"]:
            state["store"].save_system_event(partner, text)
        try: page.update()
        except: pass

    def _on_incoming_message(sender: str, plaintext: str, timestamp: str = "",
                              view_once: bool = False, encrypted_payload: str = ""):
        if state["recipient"] and sender == state["recipient"]:
            add_message_to_chat(sender, plaintext, is_mine=False,
                                 time_str=timestamp, save=True,
                                 view_once=view_once,
                                 encrypted_payload=encrypted_payload)
        else:
            if state["store"] and not view_once:
                state["store"].save_message(
                    partner=sender, sender=sender,
                    content=plaintext, is_mine=False, timestamp=timestamp,
                )
            log_status(f"'{sender}' adlisindan yeni mesaj var!")

    def _on_incoming_file(sender: str, file_uuid: str, original_name: str,
                           file_type: str, timestamp: str, view_once: bool):
        if state["recipient"] and sender == state["recipient"]:
            add_file_to_chat(sender, file_uuid, original_name,
                              file_type, is_mine=False, time_str=timestamp,
                              view_once=view_once)
        else:
            log_status(f"'{sender}' adlisindan dosya var! ({original_name})")

    def load_history_to_chat():
        if not state["recipient"] or not state["store"]: return
        chat_list.controls.clear()
        for m in state["store"].get_messages(state["recipient"]):
            if m["msg_type"] == "system":
                chat_list.controls.append(create_system_bubble(m["content"]))
            else:
                ts = _fmt_time(m["timestamp"])
                chat_list.controls.append(
                    create_message_bubble(m["sender"], m["content"], ts, bool(m["is_mine"]))
                )
        try: page.update()
        except: pass

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                   EPHEMERAL MOD KONTROLÜ                       ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def toggle_ephemeral(e):
        if not state["recipient"]:
            log_status("Once bir alici secin!")
            return
        new_val = not state["ephemeral"]
        state["ephemeral"] = new_val
        state["store"].set_ephemeral(state["recipient"], new_val, state["username"])
        _update_ephemeral_ui()
        _ws_send_raw(json.dumps({
            "type": "ephemeral_toggle",
            "sender": state["username"],
            "recipient": state["recipient"],
            "ephemeral": new_val,
        }))
        label = ("Gecici sohbet modu ACILDI — mesajlar kaydedilmiyor"
                 if new_val else "Mesaj kayit modu ACILDI — mesajlar kaydediliyor")
        add_system_event(label, partner=state["recipient"])

    def _update_ephemeral_ui():
        if state["ephemeral"]:
            ephemeral_btn.icon       = ft.Icons.VISIBILITY_OFF
            ephemeral_btn.icon_color = "#ff6b6b"
            ephemeral_btn.tooltip    = "Gecici mod ACIK — kapat"
        else:
            ephemeral_btn.icon       = ft.Icons.VISIBILITY
            ephemeral_btn.icon_color = "#6c63ff"
            ephemeral_btn.tooltip    = "Gecici sohbete gec"
        try: page.update()
        except: pass

    def copy_public_key(e):
        try:
            pem_str = public_key_to_pem_string(state["public_key"])
            fingerprint = get_public_key_fingerprint(state["public_key"])
            card = {
                "username": state["username"],
                "public_key": pem_str,
                "fingerprint": fingerprint
            }
            page.set_clipboard(json.dumps(card, indent=2))
            log_status("Kimlik kartiniz panoya kopyalandi!")
        except Exception as ex:
            log_status(f"Kopyalama hatasi: {ex}")

    def _on_ephemeral_toggle_received(sender: str, ephemeral: bool, ts: str):
        if state["store"]:
            state["store"].set_ephemeral(sender, ephemeral, sender)
        if state["recipient"] == sender:
            state["ephemeral"] = ephemeral
            _update_ephemeral_ui()
            label = (f"{sender} gecici modu ACTI — kayit durdu"
                     if ephemeral else f"{sender} kayit modunu ACTI")
            add_system_event(label, partner=sender)

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║             VIEW-ONCE MESAJ TOGGLE (per-mesaj)                 ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def toggle_view_once_msg(e):
        """Mesaj bazında tek görünümlü toggle."""
        state["view_once_mode"] = not state["view_once_mode"]
        if state["view_once_mode"]:
            view_once_msg_btn.icon       = ft.Icons.VISIBILITY_OFF
            view_once_msg_btn.icon_color = "#ff6b6b"
            view_once_msg_btn.tooltip    = "Tek gorunumlu ACIK — kapat"
        else:
            view_once_msg_btn.icon       = ft.Icons.VISIBILITY
            view_once_msg_btn.icon_color = "#888888"
            view_once_msg_btn.tooltip    = "Tek gorunumlu gonder"
        page.update()

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                DOSYA / RESİM GÖNDERİMİ                        ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def on_attach_click(e):
        log_status("Dosya gonderimi bu arayuz surumunde devre disidir.")

    def on_file_picked(e):
        if not e.files: return
        f = e.files[0]
        log_status(f"Dosya sifreleniyor: {f.name}...")
        page.update()

        def do_upload():
            try:
                raw = Path(f.path).read_bytes()
                if len(raw) > 10 * 1024 * 1024:  # 10 MB limit
                    log_status("Dosya cok buyuk! Max 10 MB.")
                    page.update()
                    return

                file_type = _guess_file_type(f.name)
                encrypted = encrypt_bytes(raw, state["recipient_pub_key"])

                headers = make_auth_headers(state["username"], state["private_key"])
                resp = requests.post(
                    f"{BASE_URL}/api/upload_file",
                    json={
                        "sender":         state["username"],
                        "recipient":      state["recipient"],
                        "encrypted_data": encrypted,
                        "original_name":  f.name,
                        "file_type":      file_type,
                    },
                    headers=headers,
                    timeout=60,
                )
                if resp.status_code != 200:
                    log_status(f"Upload basarisiz: {resp.text}")
                    page.update()
                    return

                file_uuid = resp.json()["uuid"]
                view_once = state["view_once_mode"]

                # WS üzerinden alıcıya bildir
                _ws_send_raw(json.dumps({
                    "type":          "file_message",
                    "sender":        state["username"],
                    "recipient":     state["recipient"],
                    "file_uuid":     file_uuid,
                    "original_name": f.name,
                    "file_type":     file_type,
                    "view_once":     view_once,
                }))

                # Kendi ekranında göster
                add_file_to_chat(
                    sender=state["username"],
                    file_uuid=file_uuid,
                    original_name=f.name,
                    file_type=file_type,
                    is_mine=True,
                    view_once=view_once,
                )

                # View-once sıfırla
                if view_once:
                    state["view_once_mode"] = False
                    view_once_msg_btn.icon       = ft.Icons.VISIBILITY
                    view_once_msg_btn.icon_color = "#888888"

                log_status(f"{f.name} gonderildi.")
                page.update()

            except Exception as ex:
                log_status(f"Dosya gonderim hatasi: {ex}")
                page.update()

        threading.Thread(target=do_upload, daemon=True).start()

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                   WEBSOCKET İLETİŞİMİ                          ║
    # ╚═══════════════════════════════════════════════════════════════╝

    def start_websocket_listener():
        threading.Thread(target=_run_ws_loop, daemon=True, name="ws-listener").start()
        log_status("WebSocket baglantisi kuruluyor...")

    def _run_ws_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_ws_listen())

    async def _ws_listen():
        import websockets
        state["ws_loop"] = asyncio.get_running_loop()
        reconnect_delay = 2
        while True:
            try:
                async with websockets.connect(f"{WS_URL}/ws/{state['username']}") as ws:
                    # Challenge-Response Handshake:
                    # 1. Receive challenge nonce
                    challenge_raw = await ws.recv()
                    challenge_msg = json.loads(challenge_raw)
                    if challenge_msg.get("type") != "challenge":
                        raise Exception("Handshake hatası: Sunucudan challenge alınamadı.")
                        
                    challenge = challenge_msg["challenge"]
                    
                    # 2. Sign challenge using private key
                    from crypto_utils import sign_data
                    sig = sign_data(state["private_key"], challenge.encode("utf-8"))
                    sig_b64 = base64.b64encode(sig).decode("ascii")
                    
                    # 3. Send signature back to server
                    await ws.send(json.dumps({
                        "type": "auth",
                        "signature": sig_b64
                    }))
                    
                    # 4. Receive auth result
                    auth_res_raw = await ws.recv()
                    auth_res = json.loads(auth_res_raw)
                    if auth_res.get("type") != "auth_result" or auth_res.get("status") != "success":
                        err_msg = auth_res.get("message", "Kimlik doğrulama başarısız.")
                        log_status(f"Kimlik doğrulama hatası: {err_msg}")
                        raise Exception(f"Kimlik doğrulama hatası: {err_msg}")
                        
                    state["ws"] = ws
                    log_status("Baglanti kuruldu.")
                    page.update()
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            t    = data.get("type", "")

                            if t == "message":
                                sender    = data.get("sender", "?")
                                enc       = data.get("encrypted_payload", "")
                                ts        = data.get("timestamp", "")
                                vo        = bool(data.get("view_once", False))
                                try:
                                    pt = decrypt_message(enc, state["private_key"])
                                    _on_incoming_message(sender, pt, ts, vo, enc)
                                except Exception as ex:
                                    _on_incoming_message(sender, f"[Hata:{ex}]", ts)

                            elif t == "file_message":
                                _on_incoming_file(
                                    sender=data.get("sender","?"),
                                    file_uuid=data.get("file_uuid",""),
                                    original_name=data.get("original_name","dosya"),
                                    file_type=data.get("file_type","document"),
                                    timestamp=data.get("timestamp",""),
                                    view_once=bool(data.get("view_once", False)),
                                )

                            elif t == "ephemeral_toggle":
                                _on_ephemeral_toggle_received(
                                    data.get("sender","?"),
                                    bool(data.get("ephemeral", False)),
                                    data.get("timestamp",""),
                                )

                            elif t == "group_key_dist":
                                sender = data.get("sender", "?")
                                group_id = data.get("group_id", "")
                                enc = data.get("encrypted_payload", "")
                                try:
                                    group_key_hex = decrypt_message(enc, state["private_key"])
                                    state["store"].save_group_key(group_id, group_key_hex)
                                    log_status(f"'{sender}' sizi gruba ekledi. Anahtar alindi.")
                                    sync_user_groups_from_server()
                                except Exception as ex:
                                    print(f"Grup anahtari cozme hatasi: {ex}")

                            elif t == "group_message":
                                sender = data.get("sender", "?")
                                group_id = data.get("group_id", "")
                                enc = data.get("encrypted_payload", "")
                                ts = data.get("timestamp", "")
                                
                                group_key = state["store"].get_group_key(group_id)
                                if group_key:
                                    try:
                                        pt = decrypt_symmetric(enc, group_key)
                                        state["store"].save_message(
                                            partner=group_id,
                                            sender=sender,
                                            content=pt,
                                            is_mine=False,
                                            timestamp=ts
                                        )
                                        if state["recipient"] == group_id:
                                            add_message_to_chat(
                                                sender=sender,
                                                text=pt,
                                                is_mine=False,
                                                time_str=ts,
                                                save=False
                                            )
                                        else:
                                            chat_info = state["store"].get_chat_info(group_id)
                                            gname = chat_info.get("partner", group_id)
                                            log_status(f"Grup '{gname}'dan yeni mesaj!")
                                    except Exception as ex:
                                        print(f"Grup mesaji cozme hatasi: {ex}")

                            elif t == "delivery_ack":
                                s = data.get("status","")
                                r = data.get("recipient","")
                                if s == "delivered_online":
                                    log_status(f"'{r}' adlisina iletildi.")
                                elif s == "stored_offline":
                                    log_status(f"'{r}' cevrimdisi. Mesaj saklandı.")

                        except json.JSONDecodeError: pass
            except Exception:
                state["ws"] = None
                log_status(f"WS koptu. {reconnect_delay}s sonra tekrar...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

    def is_ws_connected() -> bool:
        ws = state.get("ws")
        loop = state.get("ws_loop")
        return ws is not None and loop is not None and loop.is_running() and getattr(ws, "open", False)

    def _ws_send_raw(raw_json: str):
        ws = state.get("ws")
        loop = state.get("ws_loop")
        if is_ws_connected():
            print("[WS] Mesaj sunucuya WebSocket uzerinden gonderiliyor...")
            asyncio.run_coroutine_threadsafe(ws.send(raw_json), loop)
        else:
            print("[WS] HATA: WebSocket baglantisi aktif degil!")

    def send_message_via_ws(recipient: str, encrypted_payload: str, view_once: bool):
        msg = json.dumps({
            "type":              "message",
            "sender":            state["username"],
            "recipient":         recipient,
            "encrypted_payload": encrypted_payload,
            "view_once":         view_once,
        })
        if is_ws_connected():
            print(f"[WS] Mesaj '{recipient}' kullanicisina WebSocket ile iletiliyor...")
            _ws_send_raw(msg)
        else:
            print(f"[REST] WebSocket kapali. Mesaj '{recipient}' icin REST API ile gonderiliyor...")
            try:
                headers = make_auth_headers(state["username"], state["private_key"])
                r = requests.post(f"{BASE_URL}/api/send_offline", json={
                    "sender": state["username"],
                    "recipient": recipient,
                    "encrypted_payload": encrypted_payload,
                }, headers=headers, timeout=5)
                if r.status_code == 200:
                    log_status("Mesaj REST ile gonderildi (offline).")
                    print(f"[REST] Mesaj REST uzerinden sunucuda basariyla saklandi (Response: {r.json()})")
                else:
                    log_status("Mesaj gonderilemedi!")
                    print(f"[REST] HATA: Sunucu hata dondu ({r.status_code}): {r.text}")
            except Exception as e:
                log_status("Mesaj gonderilemedi!")
                print(f"[REST] HATA: REST API baglanti hatasi: {e}")

    def send_group_message_via_ws(group_id: str, encrypted_payload: str):
        msg = json.dumps({
            "type":              "group_message",
            "sender":            state["username"],
            "group_id":          group_id,
            "encrypted_payload": encrypted_payload,
        })
        if is_ws_connected():
            print(f"[WS] Grup mesaji '{group_id}' grubuna gonderiliyor...")
            _ws_send_raw(msg)
        else:
            print(f"[WS] HATA: WebSocket baglantisi yok. Grup mesaji gonderilemedi!")
            log_status("WS baglantisi yok. Mesaj gonderilemedi!")

    def sync_user_groups_from_server():
        if not state["username"] or not state["store"]: return
        try:
            headers = make_auth_headers(state["username"], state["private_key"])
            resp = requests.get(f"{BASE_URL}/api/groups/{state['username']}", headers=headers, timeout=5)
            if resp.status_code == 200:
                groups = resp.json().get("groups", [])
                for g in groups:
                    state["store"].get_or_create_group_chat(g["group_id"], g["group_name"])
        except Exception as ex:
            print(f"Grup senkronizasyon hatasi: {ex}")

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     UI BİLEŞENLERİ                             ║
    # ╚═══════════════════════════════════════════════════════════════╝

    status_text = ft.Text("Hos geldiniz!", size=11, color="#9e9e9e",
                           max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)

    def log_status(msg: str):
        status_text.value = msg
        try: page.update()
        except: pass

    chat_list = ft.ListView(expand=True, spacing=8,
                             padding=ft.Padding(12, 8, 12, 8),
                             auto_scroll=True)

    # Ephemeral toggle (chat seviyesi)
    ephemeral_btn = ft.IconButton(
        icon=ft.Icons.VISIBILITY, icon_color="#6c63ff", icon_size=20,
        tooltip="Gecici sohbete gec", on_click=toggle_ephemeral,
    )

    # View-once toggle (mesaj seviyesi — input yanında)
    view_once_msg_btn = ft.IconButton(
        icon=ft.Icons.VISIBILITY, icon_color="#888888", icon_size=18,
        tooltip="Tek gorunumlu gonder", on_click=toggle_view_once_msg,
    )

    # Dosya ekleme butonu
    attach_btn = ft.IconButton(
        icon=ft.Icons.ATTACH_FILE, icon_color="#888888", icon_size=20,
        tooltip="Dosya / Resim gonder", on_click=on_attach_click,
    )

    # Dosya seçici (Flet-desktop sürüm uyuşmazlığı nedeniyle devre dışı bırakıldı)
    file_picker = None

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     GİRİŞ EKRANI                               ║
    # ╚═══════════════════════════════════════════════════════════════╝

    username_field = ft.TextField(
        label="Kullanici Adi", hint_text="Ornek: alice",
        prefix_icon=ft.Icons.PERSON,
        border_color="#6c63ff", focused_border_color="#a29bfe",
        cursor_color="#6c63ff", text_size=15, height=55,
    )

    login_btn = ft.ElevatedButton(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.LOGIN, size=20),
                ft.Text("Giris Yap", size=15, weight=ft.FontWeight.BOLD),
            ],
            alignment=ft.MainAxisAlignment.CENTER, spacing=8,
        ),
        on_click=lambda e: on_login_click(e),
        style=ft.ButtonStyle(
            bgcolor="#6c63ff", color="#ffffff",
            padding=ft.Padding(32, 16, 32, 16),
            shape=ft.RoundedRectangleBorder(radius=12),
            elevation=4,
        ),
        width=280, height=52,
    )

    def on_login_click(e):
        username = username_field.value.strip().lower()
        if not username or len(username) < 2:
            username_field.error_text = "En az 2 karakter!"
            page.update()
            return
        username_field.error_text = None
        
        # Disable button and update text
        login_btn.disabled = True
        login_btn.content.controls[1].value = "Lutfen bekleyin..."
        log_status("Giris yapiliyor...")
        page.update()

        def do_login():
            try:
                state["username"] = username
                priv, pub = initialize_keys(username)
                state["private_key"] = priv
                state["public_key"]  = pub
                state["store"]       = MessageStore(username)

                if not register_with_server(username, pub, priv):
                    login_btn.disabled = False
                    login_btn.content.controls[1].value = "Giris Yap"
                    username_field.error_text = "Sunucu baglanti hatasi! (Sunucu acik mi?)"
                    log_status("Giris basarisiz. Sunucu baglanti hatasi.")
                    page.update()
                    return

                # Reset button state
                login_btn.disabled = False
                login_btn.content.controls[1].value = "Giris Yap"

                show_chat_screen()
                sync_chat_settings()
                sync_user_groups_from_server()
                fetch_offline_messages()
                start_websocket_listener()
            except Exception as ex:
                login_btn.disabled = False
                login_btn.content.controls[1].value = "Giris Yap"
                username_field.error_text = f"Hata: {ex}"
                log_status(f"Giris sirasinda hata olustu: {ex}")
                page.update()

        threading.Thread(target=do_login, daemon=True).start()

    login_view = ft.Container(
        content=ft.Column(
            controls=[
                ft.Container(height=60),
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(ft.Icons.LOCK_OUTLINE, size=64, color="#6c63ff"),
                            ft.Text("HybridP2P", size=32, weight=ft.FontWeight.BOLD, color="#ffffff"),
                            ft.Text("Messenger", size=18, weight=ft.FontWeight.W_300, color="#6c63ff"),
                            ft.Container(height=4),
                            ft.Text("Uctan Uca Sifrelenmiş Mesajlasma", size=13,
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
                            username_field,
                            ft.Container(height=8),
                            login_btn,
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0,
                    ),
                    padding=ft.Padding(40, 0, 40, 0),
                ),
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.SHIELD, size=14, color="#4caf50"),
                            ft.Text("RSA-4096 + AES-256-GCM + E2EE Dosya", size=11, color="#4caf50"),
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
            colors=["#0a0e1a", "#141832", "#0a0e1a"],
        ),
    )

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║                     SOHBET EKRANI                               ║
    # ╚═══════════════════════════════════════════════════════════════╝

    recipient_field = ft.TextField(
        label="Alici", hint_text="Ornek: bob",
        prefix_icon=ft.Icons.PERSON_SEARCH,
        border_color="#6c63ff", focused_border_color="#a29bfe",
        cursor_color="#6c63ff", text_size=14, height=48, expand=True,
    )

    message_input = ft.TextField(
        hint_text="Mesajinizi yazin...",
        border_color="#2a2f4e", focused_border_color="#6c63ff",
        cursor_color="#6c63ff", text_size=14,
        min_lines=1, max_lines=3, expand=True,
        on_submit=lambda e: on_send_click(e),
        shift_enter=True,
    )

    def on_connect_recipient(e):
        recipient_input = recipient_field.value.strip()
        if not recipient_input:
            log_status("Alici adi veya grup ID bos olamaz!")
            return

        def connect_to_recipient_final(rec, pub):
            state["recipient"]         = rec
            state["is_group"]          = False
            state["recipient_pub_key"] = pub
            recipient_field.value      = rec
            recipient_field.read_only  = True
            recipient_field.border_color = "#4caf50"
            ephemeral_btn.disabled = False

            ephemeral = state["store"].is_ephemeral(rec)
            state["ephemeral"] = ephemeral
            _update_ephemeral_ui()
            load_history_to_chat()
            if ephemeral:
                add_system_event("Bu sohbet GECICI moddadir — mesajlar kaydedilmiyor")
            log_status(f"'{rec}' ile sohbet basladi.")
            page.update()

        def close_warning_dialog(dialog, accept, rec=None, new_pem=None):
            dialog.open = False
            try: page.overlay.remove(dialog)
            except: pass
            page.update()
            if accept and rec and new_pem:
                try:
                    new_pub_key = pem_string_to_public_key(new_pem)
                    fingerprint = get_public_key_fingerprint(new_pub_key)
                    state["store"].save_contact(rec, new_pem, fingerprint)
                    log_status(f"'{rec}' icin yeni anahtar kabul edildi.")
                    connect_to_recipient_final(rec, new_pub_key)
                except Exception as ex:
                    log_status(f"Hata: {ex}")
            else:
                log_status("Baglanti guvenlik nedeniyle reddedildi.")

        # Check if the input is a JSON contact card
        imported_card = None
        if recipient_input.startswith("{") and recipient_input.endswith("}"):
            try:
                card_data = json.loads(recipient_input)
                if "username" in card_data and "public_key" in card_data:
                    imported_card = card_data
            except Exception as ex:
                print(f"[Import Contact Card Error] {ex}")

        if imported_card:
            recipient = imported_card["username"].lower()
            if recipient == state["username"]:
                log_status("Kendi kimlik kartinizi ekleyemezsiniz!")
                return
            
            pub_key_pem = imported_card["public_key"]
            try:
                pub_key = pem_string_to_public_key(pub_key_pem)
                fingerprint = get_public_key_fingerprint(pub_key)
                state["store"].save_contact(recipient, pub_key_pem, fingerprint)
                log_status(f"'{recipient}' kimlik karti basariyla import edildi!")
                recipient_field.value = recipient
            except Exception as ex:
                log_status(f"Kimlik karti yukleme hatasi: {ex}")
                return
        else:
            recipient = recipient_input.lower()
            if recipient == state["username"]:
                log_status("Kendinize mesaj gonderemezsiniz!")
                return

        if recipient.startswith("group_"):
            key = state["store"].get_group_key(recipient)
            if not key:
                log_status("Hata: Bu grubun sifreleme anahtari sizde yok!")
                return
            
            state["recipient"] = recipient
            state["is_group"] = True
            
            chat_info = state["store"].get_chat_info(recipient)
            gname = chat_info.get("partner", recipient)
            
            recipient_field.value = gname
            recipient_field.read_only = True
            recipient_field.border_color = "#4caf50"
            ephemeral_btn.disabled = True
            
            load_history_to_chat()
            log_status(f"'{gname}' grubu ile sohbet basladi.")
            page.update()
            return

        local_contact = state["store"].get_contact(recipient)
        if local_contact:
            print(f"[Contact] Loaded local public key for '{recipient}'")
            pub_key = pem_string_to_public_key(local_contact["public_key"])
            server_pub_key = fetch_recipient_pub_key(recipient)
            if server_pub_key:
                server_pub_pem = public_key_to_pem_string(server_pub_key)
                if server_pub_pem != local_contact["public_key"]:
                    dialog = ft.AlertDialog(
                        modal=True,
                        title=ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.WARNING_ROUNDED, color="#ff6b6b"),
                                ft.Text("GUVENLIK UYARISI!", color="#ff6b6b", weight=ft.FontWeight.BOLD)
                            ],
                            spacing=8
                        ),
                        content=ft.Text(
                            f"DIKKAT: '{recipient}' kullanicisinin sunucudaki kimlik anahtari yerel kaydinizdan farkli!\n\n"
                            f"Bu durum bir MITM dinleme saldirisina veya anahtar yenilenmesine isaret edebilir.\n\n"
                            f"Sunucudaki yeni anahtari kabul etmek istiyor musunuz?",
                            color="#ffffff"
                        ),
                        actions=[
                            ft.TextButton("Reddet (Guvenli)", on_click=lambda e: close_warning_dialog(dialog, accept=False)),
                            ft.TextButton("Yeni Anahtari Kabul Et", on_click=lambda e: close_warning_dialog(dialog, accept=True, rec=recipient, new_pem=server_pub_pem))
                        ],
                        actions_alignment=ft.MainAxisAlignment.END,
                        bgcolor="#1e1b30"
                    )
                    page.overlay.append(dialog)
                    dialog.open = True
                    page.update()
                    return
        else:
            pub_key = fetch_recipient_pub_key(recipient)
            if pub_key:
                pub_key_pem = public_key_to_pem_string(pub_key)
                fingerprint = get_public_key_fingerprint(pub_key)
                state["store"].save_contact(recipient, pub_key_pem, fingerprint)
                print(f"[Contact] Saved public key for '{recipient}' to local DB")

        if pub_key:
            connect_to_recipient_final(recipient, pub_key)
        else:
            log_status(f"'{recipient}' bulunamadi.")

    def on_send_click(e):
        text      = message_input.value.strip()
        view_once = state["view_once_mode"]
        if not text: return
        if not state["recipient"]:
            log_status("Once bir aliciya veya gruba baglanin!")
            return

        is_group = bool(state.get("is_group", False))

        if is_group:
            group_id = state["recipient"]
            group_key = state["store"].get_group_key(group_id)
            if not group_key:
                log_status("Hata: Grubun sifreleme anahtari bulunamadi!")
                return
            try:
                encrypted = encrypt_symmetric(text, group_key)
            except Exception as ex:
                log_status(f"Grup sifreleme hatasi: {ex}")
                return

            send_group_message_via_ws(group_id, encrypted)
            
            # Kendi ekraninda goster ve yerel gecmise kaydet
            add_message_to_chat(
                sender=state["username"], text=text,
                is_mine=True, save=True, view_once=False
            )
        else:
            if not state["recipient_pub_key"]:
                log_status("Alici public key bulunamadi!")
                print(f"[Send] HATA: '{state['recipient']}' kullanicisinin public key'i bulunamadi!")
                return
            try:
                print(f"[Send] Mesaj '{state['recipient']}' icin RSA-4096 ile sifreleniyor...")
                encrypted = encrypt_message(text, state["recipient_pub_key"])
            except Exception as ex:
                log_status(f"Sifreleme hatasi: {ex}")
                print(f"[Send] HATA: Sifreleme hatasi: {ex}")
                return

            send_message_via_ws(state["recipient"], encrypted, view_once)

            # Kendi ekranında göster
            add_message_to_chat(
                sender=state["username"], text=text,
                is_mine=True, save=not view_once, view_once=False,
            )

        message_input.value = ""

        # View-once sıfırla
        if view_once:
            state["view_once_mode"] = False
            view_once_msg_btn.icon       = ft.Icons.VISIBILITY
            view_once_msg_btn.icon_color = "#888888"

        page.update()

    username_subtitle = ft.Text("", size=11, color="#9e9e9e")

    chat_view = ft.Container(
        content=ft.Column(
            controls=[
                # App Bar
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.LOCK, size=20, color="#6c63ff"),
                            ft.Column(
                                controls=[
                                    ft.Text("HybridP2P Messenger", size=16,
                                            weight=ft.FontWeight.BOLD, color="#ffffff"),
                                    username_subtitle,
                                ],
                                spacing=0, tight=True,
                            ),
                            ft.Container(expand=True),
                            ft.IconButton(
                                icon=ft.Icons.GROUP, icon_color="#6c63ff",
                                icon_size=20, tooltip="Grup Yonetimi",
                                on_click=lambda e: show_group_dialog(e),
                            ),
                            ephemeral_btn,
                            ft.IconButton(
                                icon=ft.Icons.COPY, icon_color="#6c63ff",
                                icon_size=20, tooltip="Kimligi Kopyala (Contact Card)",
                                on_click=copy_public_key,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.REFRESH, icon_color="#6c63ff",
                                icon_size=20, tooltip="Cevrimdisi mesajlari cek",
                                on_click=lambda e: fetch_offline_messages(),
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor="#0f1328",
                    padding=ft.Padding(16, 10, 16, 10),
                    border=ft.Border(bottom=ft.BorderSide(1, "#1e2337")),
                ),

                # Alıcı seçimi
                ft.Container(
                    content=ft.Row(
                        controls=[
                            recipient_field,
                            ft.IconButton(
                                icon=ft.Icons.LINK, icon_color="#6c63ff",
                                icon_size=22, tooltip="Aliciya baglan",
                                on_click=on_connect_recipient,
                                style=ft.ButtonStyle(
                                    bgcolor="#1e2337",
                                    shape=ft.RoundedRectangleBorder(radius=10),
                                ),
                            ),
                        ],
                        spacing=8,
                    ),
                    padding=ft.Padding(12, 8, 12, 8),
                    bgcolor="#0d1124",
                ),

                # Chat listesi
                ft.Container(content=chat_list, expand=True, bgcolor="#0a0e1a"),

                # Durum çubuğu
                ft.Container(
                    content=status_text,
                    padding=ft.Padding(16, 4, 16, 4),
                    bgcolor="#0d1124",
                ),

                # Mesaj giriş alanı — view-once + attach + send
                ft.Container(
                    content=ft.Row(
                        controls=[
                            view_once_msg_btn,
                            attach_btn,
                            message_input,
                            ft.FloatingActionButton(
                                icon=ft.Icons.SEND_ROUNDED, bgcolor="#6c63ff",
                                mini=True, on_click=on_send_click,
                                tooltip="Gonder (E2EE)",
                            ),
                        ],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    bgcolor="#0f1328",
                    padding=ft.Padding(12, 10, 12, 10),
                    border=ft.Border(top=ft.BorderSide(1, "#1e2337")),
                ),
            ],
            spacing=0, expand=True,
        ),
        expand=True,
    )

    def show_group_dialog(e):
        group_name_input = ft.TextField(label="Grup Adi", hint_text="Ornek: Aile", border_color="#6c63ff")
        group_members_input = ft.TextField(
            label="Uyeler",
            hint_text="Ornek: bob, charlie (virgülle ayirin)",
            border_color="#6c63ff"
        )
        
        groups_list_column = ft.Column(spacing=6, height=200, scroll=ft.ScrollMode.AUTO)
        
        try:
            headers = make_auth_headers(state["username"], state["private_key"])
            resp = requests.get(f"{BASE_URL}/api/groups/{state['username']}", headers=headers, timeout=5)
            groups = resp.json().get("groups", []) if resp.status_code == 200 else []
        except:
            groups = []

        def on_group_select(group_id, name):
            key = state["store"].get_group_key(group_id)
            if not key:
                log_status("Hata: Bu grubun sifreleme anahtari sizde yok!")
                dialog.open = False
                page.update()
                return
            
            state["recipient"] = group_id
            state["is_group"] = True
            
            recipient_field.value = name
            recipient_field.read_only = True
            recipient_field.border_color = "#4caf50"
            ephemeral_btn.disabled = True
            
            load_history_to_chat()
            log_status(f"'{name}' grubu ile sohbet basladi.")
            dialog.open = False
            page.update()

        def on_group_rekey(group_id, name):
            import os
            new_key = os.urandom(32)
            state["store"].save_group_key(group_id, new_key.hex())
            
            try:
                headers = make_auth_headers(state["username"], state["private_key"])
                m_resp = requests.get(f"{BASE_URL}/api/groups/{group_id}/members", headers=headers, timeout=5)
                members = m_resp.json().get("members", []) if m_resp.status_code == 200 else []
            except:
                members = []
                
            for m in members:
                m_username = m["username"]
                if m_username == state["username"]: continue
                m_pub_key = fetch_recipient_pub_key(m_username)
                if not m_pub_key: continue
                
                enc_payload = encrypt_message(new_key.hex(), m_pub_key)
                
                _ws_send_raw(json.dumps({
                    "type": "group_key_dist",
                    "sender": state["username"],
                    "recipient": m_username,
                    "group_id": group_id,
                    "encrypted_payload": enc_payload
                }))
                
            log_status(f"'{name}' grubunun anahtari yenilendi ve dagitildi.")
            dialog.open = False
            page.update()

        def on_group_leave(group_id, name):
            try:
                headers = make_auth_headers(state["username"], state["private_key"])
                resp = requests.delete(f"{BASE_URL}/api/groups/{group_id}/members/{state['username']}", headers=headers, timeout=5)
                if resp.status_code == 200:
                    log_status(f"'{name}' grubundan ciktiniz.")
                    if state["recipient"] == group_id:
                        state["recipient"] = None
                        state["is_group"] = False
                        recipient_field.value = ""
                        recipient_field.read_only = False
                        recipient_field.border_color = "#6c63ff"
                        chat_list.controls.clear()
            except Exception as ex:
                log_status(f"Gruptan cikma hatasi: {ex}")
            dialog.open = False
            page.update()

        for g in groups:
            gid = g["group_id"]
            gname = g["group_name"]
            
            groups_list_column.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Text(gname, weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
                                    ft.Container(expand=True),
                                    ft.IconButton(
                                        icon=ft.Icons.CHAT,
                                        icon_size=16,
                                        icon_color="#6c63ff",
                                        tooltip="Sohbete Basla",
                                        on_click=lambda e, gid=gid, gname=gname: on_group_select(gid, gname)
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.KEY,
                                        icon_size=16,
                                        icon_color="#4caf50",
                                        tooltip="Anahtari Yenile (Rekey)",
                                        on_click=lambda e, gid=gid, gname=gname: on_group_rekey(gid, gname)
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.EXIT_TO_APP,
                                        icon_size=16,
                                        icon_color="#ff6b6b",
                                        tooltip="Gruptan Cik",
                                        on_click=lambda e, gid=gid, gname=gname: on_group_leave(gid, gname)
                                    )
                                ],
                                alignment=ft.MainAxisAlignment.CENTER,
                                spacing=4
                            ),
                            ft.Text(f"ID: {gid}", size=9, color="#888888")
                        ],
                        spacing=2
                    ),
                    padding=6,
                    border=ft.Border(left=ft.BorderSide(1, "#2a2f4e"), top=ft.BorderSide(1, "#2a2f4e"), right=ft.BorderSide(1, "#2a2f4e"), bottom=ft.BorderSide(1, "#2a2f4e")),
                    border_radius=8,
                    bgcolor="#1e2337"
                )
            )
            
        def on_create_click(e):
            name = group_name_input.value.strip()
            members_raw = group_members_input.value.strip()
            
            if not name:
                group_name_input.error_text = "Grup adi bos olamaz!"
                page.update()
                return
                
            members = [m.strip().lower() for m in members_raw.split(",") if m.strip()]
            
            import uuid as uuid_lib
            group_id = f"group_{uuid_lib.uuid4().hex[:12]}"
            
            import os
            group_key = os.urandom(32)
            
            try:
                headers = make_auth_headers(state["username"], state["private_key"])
                resp = requests.post(f"{BASE_URL}/api/groups", json={
                    "group_id": group_id,
                    "group_name": name,
                    "creator": state["username"],
                    "members": members
                }, headers=headers, timeout=5)
                
                if resp.status_code == 200:
                    state["store"].save_group_key(group_id, group_key.hex())
                    state["store"].get_or_create_group_chat(group_id, name)
                    
                    for m in members:
                        m_pub = fetch_recipient_pub_key(m)
                        if m_pub:
                            enc_key = encrypt_message(group_key.hex(), m_pub)
                            
                            _ws_send_raw(json.dumps({
                                "type": "group_key_dist",
                                "sender": state["username"],
                                "recipient": m,
                                "group_id": group_id,
                                "encrypted_payload": enc_key
                            }))
                            
                    state["recipient"] = group_id
                    state["is_group"] = True
                    
                    recipient_field.value = name
                    recipient_field.read_only = True
                    recipient_field.border_color = "#4caf50"
                    ephemeral_btn.disabled = True
                    
                    load_history_to_chat()
                    log_status(f"'{name}' grubu olusturuldu.")
                    dialog.open = False
                    page.update()
                else:
                    log_status(f"Grup olusturma hatasi: {resp.text}")
                    dialog.open = False
                    page.update()
            except Exception as ex:
                log_status(f"Grup olusturma hatasi: {ex}")
                dialog.open = False
                page.update()

        dialog = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.GROUP, color="#6c63ff"),
                    ft.Text("Grup Yonetimi", size=16, color="#ffffff")
                ],
                spacing=8
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text("Yeni Grup Olustur", weight=ft.FontWeight.BOLD, size=13),
                        group_name_input,
                        group_members_input,
                        ft.ElevatedButton(
                            "Grup Olustur",
                            on_click=on_create_click,
                            style=ft.ButtonStyle(bgcolor="#6c63ff", color="#ffffff")
                        ),
                        ft.Divider(color="#2a2f4e"),
                        ft.Text("Gruplarim", weight=ft.FontWeight.BOLD, size=13),
                        groups_list_column
                    ],
                    spacing=8,
                    tight=True
                ),
                width=350
            ),
            bgcolor="#141832"
        )
        
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    # ── Ekran Geçişleri ──────────────────────────────────────────────

    def show_login_screen():
        page.controls.clear()
        page.add(login_view)
        page.update()

    def show_chat_screen():
        username_subtitle.value = f"Kullanici: {state['username']}"
        page.controls.clear()
        page.add(chat_view)
        page.update()

    show_login_screen()


if __name__ == "__main__":
    ft.app(target=main)
