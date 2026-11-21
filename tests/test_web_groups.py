"""Grup üye yönetimi ve Web Grup UI regresyon testleri."""

import uuid
import requests

from conftest import ROOT


def _delete(user, path):
    return requests.delete(
        f"{user.server.base}{path}",
        headers=user.auth_headers("DELETE", path),
        timeout=5,
    )


def test_group_member_management_and_creator_metadata(make_user):
    creator = make_user("gc")
    member = make_user("gm1")
    extra = make_user("gm2")
    outsider = make_user("go")

    gid = f"group_{uuid.uuid4().hex[:12]}"
    r = creator.post("/api/groups", {
        "group_id": gid,
        "group_name": "Ops",
        "creator": creator.name,
        "members": [member.name],
    })
    assert r.status_code == 200

    # Non-existent user cannot be added
    r_missing = creator.post(f"/api/groups/{gid}/members", {"username": "no_such_user_xyz"})
    assert r_missing.status_code == 404

    # Existing member can add another registered user
    r_add = member.post(f"/api/groups/{gid}/members", {"username": extra.name})
    assert r_add.status_code == 200

    # Members endpoint exposes creator and group_name alongside member list
    r_list = member.get(f"/api/groups/{gid}/members")
    assert r_list.status_code == 200
    body = r_list.json()
    assert body["creator"] == creator.name
    assert body["group_name"] == "Ops"
    names = {m["username"] for m in body["members"]}
    assert names == {creator.name, member.name, extra.name}

    # Outsider is forbidden from viewing members
    assert outsider.get(f"/api/groups/{gid}/members").status_code == 403

    # Non-creator cannot kick another member
    assert _delete(member, f"/api/groups/{gid}/members/{extra.name}").status_code == 403

    # Creator can kick a member, and a member can leave themselves
    assert _delete(creator, f"/api/groups/{gid}/members/{extra.name}").status_code == 200
    assert _delete(member, f"/api/groups/{gid}/members/{member.name}").status_code == 200

    names_after = {m["username"] for m in creator.get(f"/api/groups/{gid}/members").json()["members"]}
    assert names_after == {creator.name}


def test_web_group_management_ui_present_and_xss_safe():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    for el_id in (
        "group-info-btn",
        "group-info-modal",
        "gi-members-list",
        "gi-add-member-input",
        "gi-add-member-btn",
        "gi-rekey-btn",
        "gi-leave-btn",
        "my-groups-list",
    ):
        assert f'id="{el_id}"' in html, f"missing #{el_id}"

    # Group UI must not rely on inline onclick attributes
    assert "onclick=" not in app_js
    assert "escapeHtml(" in app_js
