"""desktop/p2p_verify.py — Kişi doğrulama (güvenlik kodu) ve sıkı mod yardımcıları (P2).

Sunucusuz sayfadaki (static/serverless.html) doğrulama akışının masaüstü karşılığı:

  • Güvenlik kodu: p2p_core.safety_number (iki tarafta da aynı 20 hane). Diyalog
    kodu (kendi adım, kendi anahtarım, karşı taraf, karşı tarafın anahtarı) ile
    hesaplar; algoritma hesaplayan taraftan bağımsızdır.
  • "Kodlar aynı, doğruladım": kişi rehberde yoksa önce eklenir (ilk görüşte güven,
    TOFU — mevcut "Rehbere ekle" ile aynı anlam), sonra mark_contact_verified.
    Rehberde FARKLI anahtarla kayıtlıysa doğrulanmaz (olası taklit).
  • Sıkı mod (settings_store "p2p_strict"): doğrulanmamış kişiyle dosya gönderme,
    gelen dosya teklifleri ve görüntülü arama engellenir (p2p_core.strict_blocks).

Saf yardımcılar (strict_on, peer_verified, confirm_verified, reject_offer_if_strict,
file_send_blocked, video_refused, dialog_safety_number) Flet penceresi olmadan
testlerde sınanır (tests/test_p2p_verify_desktop.py). Arayüz parçaları UI
döngüsünden çağrılmalıdır.
"""

import json

import flet as ft

from desktop import p2p_core, settings_store
from desktop.theme import C, t


# ── saf yardımcılar ─────────────────────────────────────────────────────
def strict_on() -> bool:
    """Sıkı mod açık mı (cihaz ayarı)."""
    return bool(settings_store.get("p2p_strict"))


def peer_verified(store, ident) -> bool:
    """Karşı taraf rehberde doğrulanmış VE doğrulanan anahtar koddaki anahtarla aynı mı?"""
    if store is None or ident is None or not ident.username or not ident.fingerprint:
        return False
    contact = store.get_contact(ident.username)
    return bool(contact and contact.get("is_verified") and contact.get("fingerprint") == ident.fingerprint)


def dialog_safety_number(me: str, my_pub, peer: str, peer_pub) -> str:
    """Doğrulama diyaloğunda gösterilen güvenlik kodu (p2p_core.safety_number ile aynı)."""
    return p2p_core.safety_number(me, my_pub, peer, peer_pub)


def confirm_verified(store, username: str, pem: str, fingerprint: str) -> bool:
    """Kişiyi doğrulandı olarak işaretler; rehberde yoksa önce ekler (TOFU).

    Rehberde farklı bir anahtarla kayıtlıysa ValueError (doğrulama YAPILMAZ).
    Kişi yeni eklendiyse True döner.
    """
    if not username or not pem or not fingerprint:
        raise ValueError(t("v_no_key"))
    existing = store.get_contact(username)
    if existing and existing.get("fingerprint") != fingerprint:
        raise ValueError(t("contact_key_differs", user=username))
    added = not existing
    if added:
        store.save_contact(username, pem, fingerprint)
    store.mark_contact_verified(username)
    return added


def file_send_blocked(peer_is_verified: bool, strict: bool) -> bool:
    return p2p_core.strict_blocks("file", peer_is_verified, strict)


def video_refused(mode: str, peer_is_verified: bool, strict: bool) -> bool:
    """Görüntülü bağlantı (oluşturma ya da yanıtlama) sıkı mod yüzünden reddedilir mi?"""
    return mode == "video" and p2p_core.strict_blocks("video", peer_is_verified, strict)


def reject_offer_if_strict(offer: dict, send, peer_is_verified: bool, strict: bool) -> bool:
    """Gelen dosya teklifini sıkı modda otomatik reddeder (send: kanal.send benzeri).

    Reddedildiyse True döner; teklif kayda (IncomingRegistry) hiç alınmamalıdır.
    """
    if not p2p_core.strict_blocks("file", peer_is_verified, strict):
        return False
    send(json.dumps({"t": "file_reject", "fid": offer.get("fid", "")}))
    return True


# ── arayüz parçaları ────────────────────────────────────────────────────
def verify_chip(verified: bool, on_click=None) -> ft.Container:
    """"✓ Doğrulandı" (yeşil) ya da "Doğrulanmadı · Doğrula" (amber) rozeti."""
    color = C.success if verified else C.warning
    # Doğrulanmışta metin zaten "✓" ile başlar: ikinci bir onay simgesi eklenmez
    return ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.GPP_MAYBE_OUTLINED, size=14, color=color, visible=not verified),
            ft.Text(t("v_verified") if verified else t("v_unverified"), size=11, color=color,
                    weight=ft.FontWeight.W_600),
        ], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=C.success_bg if verified else C.warning_bg,
        border=ft.Border.all(1, color),
        border_radius=12,
        padding=ft.Padding(8, 3, 10, 3),
        on_click=on_click,
        ink=on_click is not None,
        tooltip=t("v_verify") if on_click is not None and not verified else None,
    )


def safety_number_view(sn: str, size: int = 24) -> ft.Container:
    """Güvenlik kodu: 4 grup × 5 hane, iri eş aralıklı yazı (2×2 ızgara)."""
    groups = (sn or "").split()

    def cell(g):
        return ft.Text(g, size=size, weight=ft.FontWeight.BOLD, color=C.text, font_family="monospace",
                       selectable=True)

    rows = [ft.Row([cell(g) for g in groups[i:i + 2]], spacing=size, alignment=ft.MainAxisAlignment.CENTER)
            for i in range(0, len(groups), 2)]
    return ft.Container(
        content=ft.Column(rows, spacing=4, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=C.surface_deep,
        border=ft.Border.all(1, C.border),
        border_radius=12,
        padding=ft.Padding(14, 10, 14, 10),
        alignment=ft.Alignment(0, 0),
    )


def _own_safety_number(app, peer: str, peer_pem: str) -> str:
    return dialog_safety_number(app.state["username"], app.state["public_key"], peer, peer_pem)


def open_verify_dialog(app, peer: str, peer_pem: str, peer_fp: str, on_change=None):
    """Güvenlik kodu diyaloğu. on_change(): doğrulama durumu değişince (UI döngüsünde)."""
    store = app.state.get("store")
    try:
        sn = _own_safety_number(app, peer, peer_pem)
    except Exception as ex:
        print(f"[P2P] Güvenlik kodu hesaplanamadı: {ex}")
        app.log_status(t("v_no_key"))
        return
    state_text = ft.Text("", size=12)
    match_btn = ft.Button(
        content=ft.Row([ft.Icon(ft.Icons.VERIFIED_USER, size=16), ft.Text(t("v_codes_match"))],
                       spacing=6, tight=True),
        style=ft.ButtonStyle(bgcolor=C.success, color=C.on_accent, shape=ft.RoundedRectangleBorder(radius=8)),
    )
    unmark_btn = ft.TextButton(t("v_mark_unverified"), style=ft.ButtonStyle(color=C.danger))

    def is_ok():
        contact = store.get_contact(peer) if store else None
        return bool(contact and contact.get("is_verified") and contact.get("fingerprint") == peer_fp)

    def render():
        ok = is_ok()
        state_text.value = t("v_state_ok", u=peer) if ok else t("v_state_no", u=peer)
        state_text.color = C.success if ok else C.text_secondary
        match_btn.visible = not ok
        unmark_btn.visible = ok

    def close(e=None):
        dlg.open = False
        app.page.update()

    def on_match(e):
        try:
            confirm_verified(store, peer, peer_pem, peer_fp)
        except ValueError as ex:
            state_text.value, state_text.color = str(ex), C.danger
            app.page.update()
            return
        app.log_status(t("v_marked", u=peer))
        close()
        if on_change:
            on_change()

    def on_unmark(e):
        store.unmark_contact_verified(peer)
        app.log_status(t("v_unmarked", u=peer))
        render()
        app.page.update()
        if on_change:
            on_change()

    match_btn.on_click = on_match
    unmark_btn.on_click = on_unmark
    render()
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Icon(ft.Icons.SHIELD_OUTLINED, color=C.accent_light),
            ft.Text(t("v_title", u=peer), size=16, color=C.text, weight=ft.FontWeight.BOLD, expand=True),
        ], spacing=8),
        content=ft.Container(
            ft.Column([
                ft.Text(t("v_instructions"), size=12, color=C.text_secondary),
                safety_number_view(sn, size=26),
                state_text,
            ], spacing=12, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            width=400,
        ),
        actions=[unmark_btn, ft.TextButton(t("cancel"), on_click=close), match_btn],
        bgcolor=C.surface,
    )
    app.page.overlay.append(dlg)
    dlg.open = True
    app.page.update()


def call_safety_panel(app, ident, on_change=None) -> ft.Control | None:
    """Sunucusuz arama ekranı için: rozet + sesli okunacak güvenlik kodu (kimlik yoksa None)."""
    if ident is None or not ident.username or not ident.public_key_pem:
        return None
    try:
        sn = _own_safety_number(app, ident.username, ident.public_key_pem)
    except Exception as ex:
        print(f"[P2P] Güvenlik kodu hesaplanamadı: {ex}")
        return None
    ok = peer_verified(app.state.get("store"), ident)
    chip = verify_chip(ok, on_click=lambda e: open_verify_dialog(
        app, ident.username, ident.public_key_pem, ident.fingerprint, on_change))
    return ft.Column([
        ft.Row([chip], alignment=ft.MainAxisAlignment.CENTER),
        ft.Text(t("v_sn_call"), size=11, color=C.text_muted, text_align=ft.TextAlign.CENTER),
        safety_number_view(sn, size=20),
    ], spacing=6, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
