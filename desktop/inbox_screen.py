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

from crypto_utils import encrypt_message, public_key_to_pem_string, serialize_private_key


class InboxScreenMixin:

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
                                ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, size=48, color="#3f3f46"),
                                ft.Text("No chats yet.", size=14, color="#9e9e9e"),
                                ft.Text("Start a new chat by clicking the '+' button.", size=11, color="#666666"),
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
                    avatar_color = "#8b5cf6" if is_group else "#007acc"

                    def on_chat_tile_click(e, p=partner, ig=is_group):
                        self.recipient_field.value = p
                        self.on_connect_recipient(None)

                    row2_controls = [
                        ft.Text(last_msg or "No messages yet", size=12, color="#9e9e9e", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
                    ]
                    if unread_count > 0:
                        row2_controls.append(
                            ft.Container(
                                content=ft.Text(
                                    str(unread_count),
                                    size=10,
                                    color="#ffffff",
                                    weight=ft.FontWeight.BOLD,
                                ),
                                bgcolor="#8b5cf6",
                                border_radius=10,
                                padding=ft.Padding(6, 2, 6, 2),
                                alignment=ft.Alignment(0, 0),
                            )
                        )

                    self.inbox_list.controls.append(
                        ft.Container(
                            content=ft.Row(
                                controls=[
                                    ft.CircleAvatar(
                                        content=ft.Icon(avatar_icon, color="#ffffff", size=18),
                                        bgcolor=avatar_color,
                                        radius=20,
                                    ),
                                    ft.Column(
                                        controls=[
                                            ft.Row(
                                                controls=[
                                                    ft.Text(partner, weight=ft.FontWeight.BOLD, size=14, color="#ffffff"),
                                                    ft.Text(last_time, size=10, color="#22c55e" if unread_count > 0 else "#888888"),
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
                            bgcolor="#18181b",
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
                                ft.Icon(ft.Icons.SEARCH_OFF, size=48, color="#3f3f46"),
                                ft.Text("No results found", size=14, color="#9e9e9e"),
                                ft.Text("Try checking the spelling or searching for another keyword.", size=11, color="#666666"),
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
                            content=ft.Text("CHATS", size=11, weight=ft.FontWeight.BOLD, color="#8b5cf6"),
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
                        avatar_color = "#8b5cf6" if is_group else "#007acc"

                        row2_controls = [
                            ft.Text(last_msg or "No messages yet", size=12, color="#9e9e9e", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
                        ]
                        if unread_count > 0:
                            row2_controls.append(
                                ft.Container(
                                    content=ft.Text(
                                        str(unread_count),
                                        size=10,
                                        color="#ffffff",
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    bgcolor="#8b5cf6",
                                    border_radius=10,
                                    padding=ft.Padding(6, 2, 6, 2),
                                    alignment=ft.Alignment(0, 0),
                                )
                            )

                        self.inbox_list.controls.append(
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        ft.CircleAvatar(
                                            content=ft.Icon(avatar_icon, color="#ffffff", size=18),
                                            bgcolor=avatar_color,
                                            radius=20,
                                        ),
                                        ft.Column(
                                            controls=[
                                                ft.Row(
                                                    controls=[
                                                        ft.Text(partner, weight=ft.FontWeight.BOLD, size=14, color="#ffffff"),
                                                        ft.Text(last_time, size=10, color="#22c55e" if unread_count > 0 else "#888888"),
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
                                bgcolor="#18181b",
                            )
                        )

                if matching_msgs:
                    self.inbox_list.controls.append(
                        ft.Container(
                            content=ft.Text("MESSAGES", size=11, weight=ft.FontWeight.BOLD, color="#8b5cf6"),
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
                        avatar_color = "#8b5cf6" if is_group else "#007acc"

                        self.inbox_list.controls.append(
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        ft.CircleAvatar(
                                            content=ft.Icon(avatar_icon, color="#ffffff", size=16),
                                            bgcolor=avatar_color,
                                            radius=16,
                                        ),
                                        ft.Column(
                                            controls=[
                                                ft.Row(
                                                    controls=[
                                                        ft.Text(partner, weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
                                                        ft.Text(msg_time, size=9, color="#888888"),
                                                    ],
                                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                ),
                                                ft.Text(snippet, size=11, color="#9e9e9e", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
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
                                bgcolor="#141416",
                            )
                        )
        try: self.page.update()
        except: pass

    def open_new_chat_dialog(self, e, default_tab_index=0):
        # 1. DM Tab Controls
        name_input = ft.TextField(
            label="Username",
            hint_text="Example: bob",
            border_color="#8b5cf6",
            focused_border_color="#a78bfa",
            cursor_color="#8b5cf6",
        )

        # 2. Group Tab Controls
        group_name_input = ft.TextField(
            label="Group Name",
            hint_text="Example: Family",
            border_color="#8b5cf6",
            focused_border_color="#a78bfa",
            cursor_color="#8b5cf6",
        )
        group_members_input = ft.TextField(
            label="Members",
            hint_text="Example: bob, charlie (comma separated)",
            border_color="#8b5cf6",
            focused_border_color="#a78bfa",
            cursor_color="#8b5cf6",
        )

        groups_list_column = ft.Column(spacing=6, height=180, scroll=ft.ScrollMode.AUTO)
        groups_loading = ft.Row(
            controls=[
                ft.ProgressRing(width=16, height=16, stroke_width=2, color="#8b5cf6"),
                ft.Text(" Loading groups...", size=12, color="#888888")
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
            self.recipient_field.border_color = "#22c55e"
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
                                self.recipient_field.border_color = "#8b5cf6"
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
                            self.recipient_field.border_color = "#22c55e"
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
                    ft.Text("Enter username to chat directly:", size=13, color="#9e9e9e"),
                    name_input,
                    ft.Container(height=10),
                    ft.Row(
                        controls=[
                            ft.TextButton("Cancel", on_click=close_dialog),
                            ft.Button(
                                "Start Chat",
                                on_click=on_confirm,
                                style=ft.ButtonStyle(bgcolor="#8b5cf6", color="#ffffff")
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
                    ft.Text("Create New Group", weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
                    group_name_input,
                    group_members_input,
                    ft.Button(
                        "Create Group",
                        on_click=on_create_click,
                        style=ft.ButtonStyle(bgcolor="#8b5cf6", color="#ffffff")
                    ),
                    ft.Divider(color="#27272a"),
                    ft.Text("My Groups", weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
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
                    ft.Icon(ft.Icons.CHAT_ROUNDED, color="#8b5cf6"),
                    ft.Text("New Conversation", size=16, color="#ffffff"),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color="#888888",
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
            bgcolor="#18181b",
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
                        ft.Text("No groups found.", size=12, color="#888888")
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
                                                ft.Text(gname, weight=ft.FontWeight.BOLD, size=13, color="#ffffff"),
                                                ft.Container(expand=True),
                                                ft.IconButton(
                                                    icon=ft.Icons.CHAT,
                                                    icon_size=16,
                                                    icon_color="#8b5cf6",
                                                    tooltip="Start Chat",
                                                    on_click=lambda e, gid=gid, gname=gname: on_group_select(gid, gname)
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.KEY,
                                                    icon_size=16,
                                                    icon_color="#22c55e",
                                                    tooltip="Refresh Key (Rekey)",
                                                    on_click=lambda e, gid=gid, gname=gname: on_group_rekey(gid, gname)
                                                ),
                                                ft.IconButton(
                                                    icon=ft.Icons.EXIT_TO_APP,
                                                    icon_size=16,
                                                    icon_color="#ef4444",
                                                    tooltip="Leave Group",
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
                                border=ft.Border(left=ft.BorderSide(1, "#3f3f46"), top=ft.BorderSide(1, "#3f3f46"), right=ft.BorderSide(1, "#3f3f46"), bottom=ft.BorderSide(1, "#3f3f46")),
                                border_radius=8,
                                bgcolor="#27272a"
                            )
                        )
                try: self.page.update()
                except: pass

            self.run_on_ui(_update_ui, groups)

        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

        threading.Thread(target=load_groups_async, daemon=True).start()

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
            border_color="#27272a",
            focused_border_color="#8b5cf6",
            text_size=11,
            cursor_color="#8b5cf6",
        )

        # Private Key container. Initially hidden (shown as dots)
        priv_key_value = "••••••••••••••••••••••••••••••••••••••••••••••••••••••••••"

        priv_key_tf = ft.TextField(
            label="Private Key PEM (Secret)",
            value=priv_key_value,
            multiline=True,
            min_lines=3,
            max_lines=5,
            read_only=True,
            border_color="#27272a",
            focused_border_color="#ef4444",
            text_size=11,
            cursor_color="#8b5cf6",
        )

        reveal_btn = ft.IconButton(
            icon=ft.Icons.VISIBILITY,
            icon_color="#ef4444",
            icon_size=20,
            tooltip="Reveal Private Key",
        )

        copy_btn = ft.IconButton(
            icon=ft.Icons.COPY,
            icon_color="#8b5cf6",
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
                priv_key_tf.focused_border_color = "#8b5cf6"
                reveal_btn.visible = False
                copy_btn.visible = True
                self.page.update()

            confirm_dialog = ft.AlertDialog(
                title=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.WARNING_ROUNDED, color="#ef4444"),
                        ft.Text("Warning: Reveal Private Key", size=16, color="#ef4444", weight=ft.FontWeight.BOLD),
                    ],
                    spacing=8,
                ),
                content=ft.Text(
                    "Are you sure you want to reveal your Private Key?\n\nAnyone with access to this key can decrypt and read your E2EE messages. Keep it highly secure!",
                    size=13,
                    color="#e0e0e0"
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=cancel_reveal),
                    ft.TextButton("Reveal", on_click=proceed_reveal, style=ft.ButtonStyle(color="#ef4444")),
                ],
                bgcolor="#18181b",
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
                    ft.Icon(ft.Icons.SETTINGS, color="#8b5cf6"),
                    ft.Text("Settings", size=18, color="#ffffff", weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE,
                        icon_size=18,
                        icon_color="#888888",
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
                                ft.Text("Logged in as:", size=12, color="#888888"),
                                ft.Text(self.state["username"], size=14, color="#ffffff", weight=ft.FontWeight.BOLD),
                            ],
                            alignment=ft.MainAxisAlignment.START,
                        ),
                        ft.Divider(color="#27272a", height=10),
                        pub_key_tf,
                        ft.Container(height=5),
                        ft.Row(
                            controls=[
                                ft.Text("Private Key PEM", size=12, color="#888888", weight=ft.FontWeight.BOLD),
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
                                    ft.Icon(ft.Icons.LOGOUT, size=18, color="#ffffff"),
                                    ft.Text("Sign Out", size=14, weight=ft.FontWeight.BOLD, color="#ffffff"),
                                ],
                                alignment=ft.MainAxisAlignment.CENTER,
                                spacing=8,
                            ),
                            on_click=on_signout_click,
                            style=ft.ButtonStyle(
                                bgcolor="#ef4444",
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
            bgcolor="#18181b",
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
