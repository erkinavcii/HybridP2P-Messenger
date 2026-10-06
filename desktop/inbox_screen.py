"""desktop/inbox_screen.py — Gelen kutusu (chat list) ekranı ve diyalogları.

client.py'den taşındı (modülerleştirme): show_inbox_screen, load_inbox_chats,
open_new_chat_dialog, open_settings_dialog, perform_logout, on_search_change,
refresh_inbox_and_messages.
"""

import asyncio
import os
import threading
import uuid as uuid_lib

import flet as ft
from desktop.theme import C
from desktop import settings_store

from crypto_utils import encrypt_message, public_key_to_pem_string, serialize_private_key


class InboxScreenMixin:

    def make_avatar(self, partner: str, is_group: bool, radius: int = 20):
        """Kişinin E2EE avatarı varsa onu, yoksa ikonlu varsayılanı döndürür."""
        icon = ft.Icons.GROUP if is_group else ft.Icons.PERSON
        bg = C.accent if is_group else C.avatar_dm
        b64 = None
        if not is_group and self.state.get("store"):
            b64 = self.state["store"].get_contact_avatar(partner)
        if b64:
            return ft.CircleAvatar(foreground_image_src=f"data:image/jpeg;base64,{b64}",
                                   bgcolor=bg, radius=radius)
        return ft.CircleAvatar(content=ft.Icon(icon, color=C.on_accent, size=int(radius * 0.9)),
                               bgcolor=bg, radius=radius)

    def refresh_avatars(self):
        """Yeni bir avatar gelince görünen yerleri günceller (UI thread'inde çağrılır)."""
        if self.page.controls and self.page.controls[0] is self.inbox_view:
            self.load_inbox_chats(self.search_field.value.strip() or None)
        if self.state.get("recipient") and not self.state.get("is_group", False):
            self.update_chat_header_avatar()

    def show_inbox_screen(self):
        self.state["recipient"] = None
        self.state["is_group"] = False
        self.search_field.value = ""
        self.fab.visible = True
        self.load_inbox_chats()
        self.page.controls.clear()
        self.page.add(self.inbox_view)
        self.page.update()

    def load_inbox_chats(self, query: str = None):
        if not self.state["store"]: return
        self.inbox_list.controls.clear()

        if not query:
            chats = self.state["store"].get_all_chats()
            if not chats:
                self.inbox_list.controls.append(
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, size=48, color=C.border),
                                ft.Text("No chats yet.", size=14, color=C.text_secondary),
                                ft.Text("Start a new chat by clicking the '+' button.", size=11, color=C.text_faint),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=6,
                        ),
                        padding=40,
                        alignment=ft.Alignment(0, 0),
                    )
                )
            else:
                for c in chats:
                    partner = c["partner"]
                    chat_id = c["chat_id"]
                    is_group = bool(c.get("is_group", 0))
                    last_msg = c.get("last_message") or ""
                    last_time = self._fmt_time(c.get("last_time")) if c.get("last_time") else ""
                    unread_count = c.get("unread_count", 0)

                    if len(last_msg) > 35:
                        last_msg = last_msg[:32] + "..."

                    avatar_icon = ft.Icons.GROUP if is_group else ft.Icons.PERSON
                    avatar_color = C.accent if is_group else C.avatar_dm

                    def on_chat_tile_click(e, p=partner, ig=is_group):
                        self.recipient_field.value = p
                        self.on_connect_recipient(None)

                    row2_controls = [
                        ft.Text(last_msg or "No messages yet", size=12, color=C.text_secondary, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
                    ]
                    if unread_count > 0:
                        row2_controls.append(
                            ft.Container(
                                content=ft.Text(
                                    str(unread_count),
                                    size=10,
                                    color=C.on_accent,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                bgcolor=C.accent,
                                border_radius=10,
                                padding=ft.Padding(6, 2, 6, 2),
                                alignment=ft.Alignment(0, 0),
                            )
                        )

                    self.inbox_list.controls.append(
                        ft.Container(
                            content=ft.Row(
                                controls=[
                                    self.make_avatar(partner, is_group, radius=20),
                                    ft.Column(
                                        controls=[
                                            ft.Row(
                                                controls=[
                                                    ft.Text(partner, weight=ft.FontWeight.BOLD, size=14, color=C.text),
                                                    ft.Text(last_time, size=10, color=C.success if unread_count > 0 else C.text_muted),
                                                ],
                                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                            ),
                                            ft.Row(
                                                controls=row2_controls,
                                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                            ),
                                        ],
                                        spacing=2,
                                        expand=True,
                                    ),
                                ],
                                spacing=12,
                            ),
                            padding=ft.Padding(12, 10, 12, 10),
                            border_radius=8,
                            ink=True,
                            on_click=lambda e, p=partner, ig=is_group: on_chat_tile_click(e, p, ig),
                            bgcolor=C.surface,
                        )
                    )
        else:
            results = self.state["store"].search_chats_and_messages(query)
            matching_chats = results["chats"]
            matching_msgs = results["messages"]

            if not matching_chats and not matching_msgs:
                self.inbox_list.controls.append(
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(ft.Icons.SEARCH_OFF, size=48, color=C.border),
                                ft.Text("No results found", size=14, color=C.text_secondary),
                                ft.Text("Try checking the spelling or searching for another keyword.", size=11, color=C.text_faint),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=6,
                        ),
                        padding=40,
                        alignment=ft.Alignment(0, 0),
                    )
                )
            else:
                def on_chat_tile_click_search(e, p, ig):
                    self.recipient_field.value = p
                    self.on_connect_recipient(None)

                if matching_chats:
                    self.inbox_list.controls.append(
                        ft.Container(
                            content=ft.Text("CHATS", size=11, weight=ft.FontWeight.BOLD, color=C.accent),
                            padding=ft.Padding(12, 8, 12, 4)
                        )
                    )
                    for c in matching_chats:
                        partner = c["partner"]
                        is_group = bool(c.get("is_group", 0))
                        last_msg = c.get("last_message") or ""
                        last_time = self._fmt_time(c.get("last_time")) if c.get("last_time") else ""
                        unread_count = c.get("unread_count", 0)

                        if len(last_msg) > 35:
                            last_msg = last_msg[:32] + "..."

                        avatar_icon = ft.Icons.GROUP if is_group else ft.Icons.PERSON
                        avatar_color = C.accent if is_group else C.avatar_dm

                        row2_controls = [
                            ft.Text(last_msg or "No messages yet", size=12, color=C.text_secondary, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
                        ]
                        if unread_count > 0:
                            row2_controls.append(
                                ft.Container(
                                    content=ft.Text(
                                        str(unread_count),
                                        size=10,
                                        color=C.on_accent,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    bgcolor=C.accent,
                                    border_radius=10,
                                    padding=ft.Padding(6, 2, 6, 2),
                                    alignment=ft.Alignment(0, 0),
                                )
                            )

                        self.inbox_list.controls.append(
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        self.make_avatar(partner, is_group, radius=20),
                                        ft.Column(
                                            controls=[
                                                ft.Row(
                                                    controls=[
                                                        ft.Text(partner, weight=ft.FontWeight.BOLD, size=14, color=C.text),
                                                        ft.Text(last_time, size=10, color=C.success if unread_count > 0 else C.text_muted),
                                                    ],
                                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                ),
                                                ft.Row(
                                                    controls=row2_controls,
                                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                ),
                                            ],
                                            spacing=2,
                                            expand=True,
                                        ),
                                    ],
                                    spacing=12,
                                ),
                                padding=ft.Padding(12, 10, 12, 10),
                                border_radius=8,
                                ink=True,
                                on_click=lambda e, p=partner, ig=is_group: on_chat_tile_click_search(e, p, ig),
                                bgcolor=C.surface,
                            )
                        )

                if matching_msgs:
                    self.inbox_list.controls.append(
                        ft.Container(
                            content=ft.Text("MESSAGES", size=11, weight=ft.FontWeight.BOLD, color=C.accent),
                            padding=ft.Padding(12, 12, 12, 4)
                        )
                    )
                    for m in matching_msgs:
                        partner = m["partner"]
                        sender = m["sender"]
                        content = m["content"]
                        msg_time = self._fmt_time(m["timestamp"])
                        is_group = bool(m["is_group"])

                        snippet = f"{sender}: {content}"
                        if len(snippet) > 45:
                            snippet = snippet[:42] + "..."

                        avatar_icon = ft.Icons.GROUP if is_group else ft.Icons.PERSON
                        avatar_color = C.accent if is_group else C.avatar_dm

                        self.inbox_list.controls.append(
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        self.make_avatar(partner, is_group, radius=16),
                                        ft.Column(
                                            controls=[
                                                ft.Row(
                                                    controls=[
                                                        ft.Text(partner, weight=ft.FontWeight.BOLD, size=13, color=C.text),
                                                        ft.Text(msg_time, size=9, color=C.text_muted),
                                                    ],
                                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                ),
                                                ft.Text(snippet, size=11, color=C.text_secondary, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                            ],
                                            spacing=2,
                                            expand=True,
                                        ),
                                    ],
                                    spacing=10,
                                ),
                                padding=ft.Padding(12, 8, 12, 8),
                                border_radius=6,
                                ink=True,
                                on_click=lambda e, p=partner, ig=is_group: on_chat_tile_click_search(e, p, ig),
                                bgcolor=C.surface_deep,
                            )
                        )
        try: self.page.update()
        except: pass

    def open_new_chat_dialog(self, e, default_tab_index=0):
        # 1. DM Tab Controls
        name_input = ft.TextField(
            label="Username",
            hint_text="Example: bob",
            border_color=C.accent,
            focused_border_color=C.accent_light,
            cursor_color=C.accent,
        )

        # 2. Group Tab Controls
        group_name_input = ft.TextField(
            label="Group Name",
            hint_text="Example: Family",
            border_color=C.accent,
            focused_border_color=C.accent_light,
            cursor_color=C.accent,
        )
        group_members_input = ft.TextField(
            label="Members",
            hint_text="Example: bob, charlie (comma separated)",
            border_color=C.accent,
            focused_border_color=C.accent_light,
            cursor_color=C.accent,
        )

        groups_list_column = ft.Column(spacing=6, height=180, scroll=ft.ScrollMode.AUTO)
        groups_loading = ft.Row(
            controls=[
                ft.ProgressRing(width=16, height=16, stroke_width=2, color=C.accent),
                ft.Text(" Loading groups...", size=12, color=C.text_muted)
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        )
        groups_list_column.controls.append(groups_loading)

        def close_dialog(e):
            dialog.open = False
            self.page.update()

        def on_confirm(e):
            rec = name_input.value.strip().lower()
            if not rec: return
            if rec == self.state["username"]:
                name_input.error_text = "You cannot chat with yourself!"
                self.page.update()
                return

            close_dialog(None)
            self.recipient_field.value = rec
            self.on_connect_recipient(None)

        def on_group_select(group_id, name):
            key = self.state["store"].get_group_key(group_id)
            if not key:
                self.log_status("Error: You don't have the encryption key for this group!")
                close_dialog(None)
                return

            self.state["recipient"] = group_id
            self.state["is_group"] = True

            self.recipient_field.value = name
            self.recipient_field.read_only = True
            self.recipient_field.border_color = C.success
            self.ephemeral_btn.disabled = True

            self.load_history_to_chat()
            self.log_status(f"'{name}' grubu ile sohbet basladi.")
            self.show_chat_screen()
            close_dialog(None)

        def on_group_rekey(group_id, name):
            new_key = os.urandom(32)
            self.state["store"].save_group_key(group_id, new_key.hex())

            def do_rekey():
                try:
                    m_resp = self.signed_get(f"/api/groups/{group_id}/members", timeout=5)
                    members = m_resp.json().get("members", []) if m_resp.status_code == 200 else []
                except:
                    members = []

                for m in members:
                    m_username = m["username"]
                    if m_username == self.state["username"]: continue
                    m_pub_key = self.fetch_recipient_pub_key(m_username)
                    if not m_pub_key: continue

                    enc_payload = encrypt_message(new_key.hex(), m_pub_key)

                    self.send_ws_message_with_fallback({
                        "type": "group_key_dist",
                        "sender": self.state["username"],
                        "recipient": m_username,
                        "group_id": group_id,
                        "encrypted_payload": enc_payload
                    })
                self.log_status(f"'{name}' grubunun anahtari yenilendi ve dagitildi.")

            threading.Thread(target=do_rekey, daemon=True).start()
            close_dialog(None)

        def on_group_leave(group_id, name):
            def do_leave():
                try:
                    resp = self.signed_delete(f"/api/groups/{group_id}/members/{self.state['username']}", timeout=5)
                    if resp.status_code == 200:
                        def _success():
                            self.log_status(f"'{name}' grubundan ciktiniz.")
                            if self.state["recipient"] == group_id:
                                self.state["recipient"] = None
                                self.state["is_group"] = False
                                self.recipient_field.value = ""
                                self.recipient_field.read_only = False
                                self.recipient_field.border_color = C.accent
                                self.chat_list.controls.clear()
                            self.load_inbox_chats()
                        self.run_on_ui(_success)
                except Exception as ex:
                    self.log_status(f"Gruptan cikma hatasi: {ex}")

            threading.Thread(target=do_leave, daemon=True).start()
            close_dialog(None)

        def on_create_click(e):
            name = group_name_input.value.strip()
            members_raw = group_members_input.value.strip()

            if not name:
                group_name_input.error_text = "Group name cannot be empty!"
                self.page.update()
                return

            members = [m.strip().lower() for m in members_raw.split(",") if m.strip()]

            group_id = f"group_{uuid_lib.uuid4().hex[:12]}"
            group_key = os.urandom(32)

            def do_create():
                try:
                    resp = self.signed_post("/api/groups", {
                        "group_id": group_id,
                        "group_name": name,
                        "creator": self.state["username"],
                        "members": members
                    }, timeout=5)

                    if resp.status_code == 200:
                        self.state["store"].save_group_key(group_id, group_key.hex())
                        self.state["store"].get_or_create_group_chat(group_id, name)

                        for m in members:
                            m_pub = self.fetch_recipient_pub_key(m)
                            if m_pub:
                                enc_key = encrypt_message(group_key.hex(), m_pub)

                                self.send_ws_message_with_fallback({
                                    "type": "group_key_dist",
                                    "sender": self.state["username"],
                                    "recipient": m,
                                    "group_id": group_id,
                                    "encrypted_payload": enc_key
                                })

                        def _success():
                            self.state["recipient"] = group_id
                            self.state["is_group"] = True
                            self.recipient_field.value = name
                            self.recipient_field.read_only = True
                            self.recipient_field.border_color = C.success
                            self.ephemeral_btn.disabled = True
                            self.load_history_to_chat()
                            self.log_status(f"'{name}' grubu olusturuldu.")
                            self.show_chat_screen()
                        self.run_on_ui(_success)
                    else:
                        self.log_status(f"Grup olusturma hatasi: {resp.text}")
                except Exception as ex:
                    self.log_status(f"Grup olusturma hatasi: {ex}")

            threading.Thread(target=do_create, daemon=True).start()
            close_dialog(None)

        # Tabs & layouts
        dm_tab_content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Enter username to chat directly:", size=13, color=C.text_secondary),
                    name_input,
                    ft.Container(height=10),
                    ft.Row(
                        controls=[
                            ft.TextButton("Cancel", on_click=close_dialog),
                            ft.Button(
                                "Start Chat",
                                on_click=on_confirm,
                                style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent)
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                    )
                ],
                spacing=8,
                tight=True,
            ),
            padding=ft.Padding(12, 16, 12, 16),
        )

        group_tab_content = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Create New Group", weight=ft.FontWeight.BOLD, size=13, color=C.text),
                    group_name_input,
                    group_members_input,
                    ft.Button(
                        "Create Group",
                        on_click=on_create_click,
                        style=ft.ButtonStyle(bgcolor=C.accent, color=C.on_accent)
                    ),
                    ft.Divider(color=C.surface_alt),
                    ft.Text("My Groups", weight=ft.FontWeight.BOLD, size=13, color=C.text),
                    groups_list_column,
                ],
                spacing=8,
                tight=True,
            ),
            padding=ft.Padding(12, 16, 12, 16),
        )

        tabs = ft.Tabs(
            selected_index=default_tab_index,
            length=2,
            content=ft.Column(
                controls=[
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="Direct Message", icon=ft.Icons.PERSON),
                            ft.Tab(label="Group Chat", icon=ft.Icons.GROUP),
                        ],
                    ),
                    ft.TabBarView(
                        controls=[
                            dm_tab_content,
                            group_tab_content,
                        ],
                        expand=True,
                    ),
                ],
                expand=True,
            ),
            expand=True,
            animation_duration=200,
        )

        dialog = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CHAT_ROUNDED, color=C.accent),
                    ft.Text("New Conversation", size=16, color=C.text),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color=C.text_muted,
                        on_click=close_dialog,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=tabs,
                width=400,
                height=520,
            ),
            bgcolor=C.surface,
        )

        def load_groups_async():
            try:
                resp = self.signed_get(f"/api/groups/{self.state['username']}", timeout=5)
                groups = resp.json().get("groups", []) if resp.status_code == 200 else []
            except:
                groups = []

            def _update_ui(g_list):
                groups_list_column.controls.clear()
                if not g_list:
                    groups_list_column.controls.append(
                        ft.Text("No groups found.", size=12, color=C.text_muted)
                    )
                else:
                    for g in g_list:
                        gid = g["group_id"]
                        gname = g["group_name"]

                        groups_list_column.controls.append(
                            ft.Container(
                                content=ft.Column(
                                    controls=[
                                        ft.Row(
                                            controls=[
                                                ft.Text(gname, weight=ft.FontWeight.BOLD, size=13, color=C.text),
                                                ft.Container(expand=True),
                                                ft.IconButton(
                                                    icon=ft.Icons.CHAT,
                                                    icon_size=16,
                                                    icon_color=C.accent,
                                                    tooltip="Start Chat",
                                                    on_click=lambda e, gid=gid, gname=gname: on_group_select(gid, gname)
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.KEY,
                                                    icon_size=16,
                                                    icon_color=C.success,
                                                    tooltip="Refresh Key (Rekey)",
                                                    on_click=lambda e, gid=gid, gname=gname: on_group_rekey(gid, gname)
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.EXIT_TO_APP,
                                                    icon_size=16,
                                                    icon_color=C.danger,
                                                    tooltip="Leave Group",
                                                    on_click=lambda e, gid=gid, gname=gname: on_group_leave(gid, gname)
                                                )
                                            ],
                                            alignment=ft.MainAxisAlignment.CENTER,
                                            spacing=4
                                        ),
                                        ft.Text(f"ID: {gid}", size=9, color=C.text_muted)
                                    ],
                                    spacing=2
                                ),
                                padding=6,
                                border=ft.Border(left=ft.BorderSide(1, C.border), top=ft.BorderSide(1, C.border), right=ft.BorderSide(1, C.border), bottom=ft.BorderSide(1, C.border)),
                                border_radius=8,
                                bgcolor=C.surface_alt
                            )
                        )
                try: self.page.update()
                except: pass

            self.run_on_ui(_update_ui, groups)

        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

        threading.Thread(target=load_groups_async, daemon=True).start()

    def open_contacts_dialog(self, e):
        """Yerel rehber: kayıtlı kişiler, parmak izleri, sohbet açma ve kişi silme."""
        if not self.state["store"]:
            self.log_status("Please sign in first!")
            return

        contacts_column = ft.Column(spacing=6, height=340, scroll=ft.ScrollMode.AUTO)

        def close_dialog(e=None):
            dialog.open = False
            self.page.update()

        def on_start_chat(username):
            close_dialog()
            self.recipient_field.value = username
            self.on_connect_recipient(None)

        def on_copy_fingerprint(fp):
            self.copy_to_clipboard(fp)
            self.log_status("Fingerprint copied to clipboard!")

        def on_delete_contact(username):
            self.state["store"].delete_contact(username)
            self.log_status(f"'{username}' rehberden silindi.")
            render_contacts()
            self.page.update()

        contacts_search = ft.TextField(
            hint_text="Kişilerde ara...",
            prefix_icon=ft.Icons.SEARCH,
            border_color=C.surface_alt,
            focused_border_color=C.accent,
            cursor_color=C.accent,
            height=38,
            text_size=13,
            content_padding=ft.Padding(10, 0, 10, 0),
        )

        def render_contacts():
            contacts_column.controls.clear()
            contacts = self.state["store"].get_all_contacts()

            query = (contacts_search.value or "").strip().lower()
            if query:
                contacts = [c for c in contacts if query in c["username"].lower()]

            if not contacts:
                if query:
                    icon, title, subtitle = (
                        ft.Icons.SEARCH_OFF, "Sonuç bulunamadı",
                        f"'{contacts_search.value.strip()}' ile eşleşen kişi yok.",
                    )
                else:
                    icon, title, subtitle = (
                        ft.Icons.CONTACTS_OUTLINED, "Rehber boş.",
                        "Bir kişiyle ilk kez bağlandığınızda anahtarı burada saklanır.",
                    )
                contacts_column.controls.append(
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Icon(icon, size=42, color=C.border),
                                ft.Text(title, size=13, color=C.text_secondary),
                                ft.Text(subtitle, size=11, color=C.text_faint,
                                        text_align=ft.TextAlign.CENTER),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=6,
                        ),
                        padding=30,
                        alignment=ft.Alignment(0, 0),
                    )
                )
                return

            for c in contacts:
                uname = c["username"]
                fp = c.get("fingerprint", "")
                # Parmak izini iki satıra sığacak şekilde kısalt
                fp_short = " ".join(fp.split()[:8]) + " …" if fp else "—"

                contacts_column.controls.append(
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                self.make_avatar(uname, False, radius=16),
                                ft.Column(
                                    controls=[
                                        ft.Text(uname, weight=ft.FontWeight.BOLD, size=13, color=C.text),
                                        ft.Text(fp_short, size=9, color=C.text_muted,
                                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                    ],
                                    spacing=1, tight=True, expand=True,
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.CHAT, icon_size=16, icon_color=C.accent,
                                    tooltip="Sohbet Aç",
                                    on_click=lambda e, u=uname: on_start_chat(u),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.FINGERPRINT, icon_size=16, icon_color=C.success,
                                    tooltip="Parmak İzini Kopyala",
                                    on_click=lambda e, f=fp: on_copy_fingerprint(f),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE, icon_size=16, icon_color=C.danger,
                                    tooltip="Rehberden Sil",
                                    on_click=lambda e, u=uname: on_delete_contact(u),
                                ),
                            ],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=ft.Padding(8, 6, 8, 6),
                        border_radius=8,
                        bgcolor=C.surface_alt,
                    )
                )

        def on_contacts_search(e):
            render_contacts()
            self.page.update()

        contacts_search.on_change = on_contacts_search
        render_contacts()

        dialog = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CONTACTS, color=C.accent),
                    ft.Text("Kişi Rehberi", size=16, color=C.text, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE, icon_size=18, icon_color=C.text_muted,
                        on_click=close_dialog,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Text("Doğrulanmış kişilerin yerel anahtar kayıtları.",
                                size=11, color=C.text_secondary),
                        contacts_search,
                        ft.Divider(color=C.surface_alt, height=8),
                        contacts_column,
                    ],
                    spacing=6, tight=True,
                ),
                width=380,
            ),
            bgcolor=C.surface,
        )

        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    def on_search_change(self, e):
        query = self.search_field.value.strip()
        self.load_inbox_chats(query)

    def perform_logout(self, e=None):
        # 1. Set logged_in flag to False to break ws loop
        self.state["logged_in"] = False

        # 2. Close WS if exists
        ws = self.state.get("ws")
        loop = self.state.get("ws_loop")
        if ws and loop:
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
                print("[Logout] WebSocket connection closed.")
            except Exception as ex:
                print(f"[Logout] Error closing ws: {ex}")

        # 3. Reset state
        self.state["username"] = None
        self.state["private_key"] = None
        self.state["public_key"] = None
        self.state["recipient"] = None
        self.state["recipient_pub_key"] = None
        self.state["ws"] = None
        self.state["ws_loop"] = None
        self.state["store"] = None
        self.state["ephemeral"] = False
        self.state["view_once_mode"] = False
        self.state["staged_file"] = None

        # 4. Reset login inputs
        self.username_field.value = ""
        self.username_field.error_text = None
        self.import_key_checkbox.value = False
        self.import_key_field.value = ""
        self.import_key_field.visible = False

        self.log_status("Signed out successfully.")
        self.show_login_screen()

    def open_settings_dialog(self, e):
        def clean_pem_for_display(pem_str: str) -> str:
            lines = pem_str.strip().splitlines()
            body_lines = [line for line in lines if not line.strip().startswith("-----")]
            return "\n".join(body_lines).strip()

        # Format keys
        try:
            pub_pem = public_key_to_pem_string(self.state["public_key"])
            pub_pem_display = clean_pem_for_display(pub_pem)
        except Exception as ex:
            pub_pem = f"Error: {ex}"
            pub_pem_display = pub_pem

        try:
            priv_pem = serialize_private_key(self.state["private_key"]).decode("utf-8")
            priv_pem_display = clean_pem_for_display(priv_pem)
        except Exception as ex:
            priv_pem = f"Error: {ex}"
            priv_pem_display = priv_pem

        # Public Key textfield (read-only, multiline)
        pub_key_tf = ft.TextField(
            label="Public Key PEM",
            value=pub_pem_display,
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color=C.surface_alt,
            focused_border_color=C.accent,
            text_size=11,
            cursor_color=C.accent,
        )

        # ── Görünüm & bildirim tercihleri (cihaz geneli, settings.json) ──
        def on_theme_toggle(e):
            # Diyalog eski paletle kurulu; set_theme onu kapatıp tüm UI'ı yeniden kurar
            self.set_theme("light" if e.control.value else "dark")

        def on_sound_toggle(e):
            settings_store.set("sound_enabled", bool(e.control.value))
            self.log_status("Bildirim sesi açıldı." if e.control.value else "Bildirim sesi kapatıldı.")

        # ── Profil fotoğrafı (E2EE dağıtılır) ──
        from desktop import avatar as avatar_mod
        me = self.state["username"]

        def _own_preview():
            jpeg = avatar_mod.load_own(me)
            if jpeg:
                return ft.CircleAvatar(foreground_image_src=avatar_mod.to_data_url(jpeg),
                                       bgcolor=C.accent, radius=26)
            return ft.CircleAvatar(content=ft.Icon(ft.Icons.PERSON, color=C.on_accent, size=26),
                                   bgcolor=C.accent, radius=26)

        avatar_slot = ft.Container(content=_own_preview())

        async def on_pick_avatar(e):
            files = await self.file_picker.pick_files(allow_multiple=False)
            if not files:
                return
            try:
                from pathlib import Path
                jpeg = avatar_mod.normalize(Path(files[0].path).read_bytes())
            except (OSError, ValueError) as ex:
                self.log_status(f"Fotoğraf kullanılamadı: {ex}")
                return
            avatar_mod.save_own(me, jpeg)
            avatar_slot.content = _own_preview()
            self.page.update()
            self.broadcast_avatar()

        avatar_row = ft.Row(
            controls=[
                avatar_slot,
                ft.Column(
                    controls=[
                        ft.Text("Profil fotoğrafı", size=13, color=C.text, weight=ft.FontWeight.BOLD),
                        ft.Text("Kişilerinize uçtan uca şifreli gönderilir; sunucu göremez.",
                                size=10, color=C.text_muted),
                    ],
                    spacing=2, tight=True, expand=True,
                ),
                ft.TextButton("Seç", on_click=on_pick_avatar, style=ft.ButtonStyle(color=C.accent)),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        theme_switch = ft.Switch(value=not C.is_dark, active_color=C.accent,
                                 on_change=on_theme_toggle)
        sound_switch = ft.Switch(value=bool(settings_store.get("sound_enabled")),
                                 active_color=C.accent, on_change=on_sound_toggle)

        def on_preview_toggle(e):
            settings_store.set("link_previews", bool(e.control.value))
            self.log_status("Link önizleme açıldı." if e.control.value else "Link önizleme kapatıldı.")

        preview_switch = ft.Switch(value=bool(settings_store.get("link_previews")),
                                   active_color=C.accent, on_change=on_preview_toggle)

        # Private Key container. Initially hidden (shown as dots)
        priv_key_value = "••••••••••••••••••••••••••••••••••••••••••••••••••••••••••"

        priv_key_tf = ft.TextField(
            label="Private Key PEM (Secret)",
            value=priv_key_value,
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color=C.surface_alt,
            focused_border_color=C.danger,
            text_size=11,
            cursor_color=C.accent,
        )

        reveal_btn = ft.IconButton(
            icon=ft.Icons.VISIBILITY,
            icon_color=C.danger,
            icon_size=20,
            tooltip="Reveal Private Key",
        )

        copy_btn = ft.IconButton(
            icon=ft.Icons.COPY,
            icon_color=C.accent,
            icon_size=20,
            tooltip="Copy Private Key",
            visible=False,
        )

        def close_settings(e):
            dialog.open = False
            self.page.update()

        def confirm_reveal_key(e):
            confirm_dialog = None

            def cancel_reveal(e):
                confirm_dialog.open = False
                self.page.update()

            def proceed_reveal(e):
                confirm_dialog.open = False
                priv_key_tf.value = priv_pem_display
                priv_key_tf.focused_border_color = C.accent
                reveal_btn.visible = False
                copy_btn.visible = True
                self.page.update()

            confirm_dialog = ft.AlertDialog(
                title=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.WARNING_ROUNDED, color=C.danger),
                        ft.Text("Warning: Reveal Private Key", size=16, color=C.danger, weight=ft.FontWeight.BOLD),
                    ],
                    spacing=8,
                ),
                content=ft.Text(
                    "Are you sure you want to reveal your Private Key?\n\nAnyone with access to this key can decrypt and read your E2EE messages. Keep it highly secure!",
                    size=13,
                    color=C.text_bubble_other
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=cancel_reveal),
                    ft.TextButton("Reveal", on_click=proceed_reveal, style=ft.ButtonStyle(color=C.danger)),
                ],
                bgcolor=C.surface,
            )
            self.page.overlay.append(confirm_dialog)
            confirm_dialog.open = True
            self.page.update()

        reveal_btn.on_click = confirm_reveal_key

        def copy_private_key(e):
            self.copy_to_clipboard(priv_pem)
            self.log_status("Private Key copied to clipboard!")

        copy_btn.on_click = copy_private_key

        def on_signout_click(e):
            dialog.open = False
            self.page.update()
            self.perform_logout()

        dialog = ft.AlertDialog(
            title=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SETTINGS, color=C.accent),
                    ft.Text("Settings", size=18, color=C.text, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color=C.text_muted,
                        on_click=close_settings,
                    ),
                ],
                spacing=8,
            ),
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Text("Logged in as:", size=12, color=C.text_muted),
                                ft.Text(self.state["username"], size=14, color=C.text, weight=ft.FontWeight.BOLD),
                            ],
                            alignment=ft.MainAxisAlignment.START,
                        ),
                        ft.Divider(color=C.surface_alt, height=10),
                        avatar_row,
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.DARK_MODE if C.is_dark else ft.Icons.LIGHT_MODE,
                                        size=18, color=C.accent),
                                ft.Text("Açık tema", size=13, color=C.text, expand=True),
                                theme_switch,
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.NOTIFICATIONS_ACTIVE, size=18, color=C.accent),
                                ft.Text("Bildirim sesi", size=13, color=C.text, expand=True),
                                sound_switch,
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.LINK, size=18, color=C.accent),
                                ft.Column(
                                    controls=[
                                        ft.Text("Link önizleme", size=13, color=C.text),
                                        ft.Text("Önizlemeyi siz çekersiniz; site IP'nizi görür, alıcınınkini görmez.",
                                                size=10, color=C.text_muted),
                                    ],
                                    spacing=1, tight=True, expand=True,
                                ),
                                preview_switch,
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Divider(color=C.surface_alt, height=10),
                        pub_key_tf,
                        ft.Container(height=5),
                        ft.Row(
                            controls=[
                                ft.Text("Private Key PEM", size=12, color=C.text_muted, weight=ft.FontWeight.BOLD),
                                ft.Container(expand=True),
                                reveal_btn,
                                copy_btn,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        priv_key_tf,
                        ft.Container(height=15),
                        ft.Button(
                            content=ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.LOGOUT, size=18, color=C.on_accent),
                                    ft.Text("Sign Out", size=14, weight=ft.FontWeight.BOLD, color=C.on_accent),
                                ],
                                alignment=ft.MainAxisAlignment.CENTER,
                                spacing=8,
                            ),
                            on_click=on_signout_click,
                            style=ft.ButtonStyle(
                                bgcolor=C.danger,
                                padding=ft.Padding(16, 12, 16, 12),
                                shape=ft.RoundedRectangleBorder(radius=6),
                            ),
                            width=300,
                        ),
                    ],
                    spacing=8,
                    tight=True,
                ),
                width=360,
                padding=ft.Padding(0, 10, 0, 10),
            ),
            bgcolor=C.surface,
        )

        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    def refresh_inbox_and_messages(self):
        self.search_field.value = ""
        def do_refresh():
            self.fetch_offline_messages()
            self.run_on_ui(self.load_inbox_chats)
        threading.Thread(target=do_refresh, daemon=True).start()
