from fastapi import APIRouter, Request, HTTPException, Header
from pydantic import BaseModel
from server.database import db_session
from server.auth import verify_request_signature

router = APIRouter()

class GroupCreateRequest(BaseModel):
    """Grup olusturma istegi."""
    group_id: str
    group_name: str
    creator: str
    members: list[str]  # Grup uyelerinin adlari


class GroupAddMemberRequest(BaseModel):
    """Gruba uye ekleme istegi."""
    username: str


@router.post("/api/groups")
async def create_group(
    request: Request,
    req: GroupCreateRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Grup oluşturur ve kurucu dahil belirtilen tüm üyeleri gruba ekler."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != req.creator:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    async with db_session() as db:
        try:
            # Grubu oluştur
            await db.execute(
                "INSERT INTO groups (group_id, group_name, creator) VALUES (?, ?, ?)",
                (req.group_id, req.group_name, req.creator)
            )
            # Kurucuyu üye olarak ekle
            await db.execute(
                "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
                (req.group_id, req.creator)
            )
            # Diğer üyeleri ekle
            for member in req.members:
                await db.execute(
                    "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
                    (req.group_id, member)
                )
            await db.commit()
            return {"status": "ok", "group_id": req.group_id, "message": f"'{req.group_name}' grubu oluşturuldu."}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/groups/{group_id}/members")
async def add_group_member(
    request: Request,
    group_id: str,
    req: GroupAddMemberRequest,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Gruba yeni üye ekler."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        # Grubun varlığını kontrol et
        cursor = await db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,))
        group = await cursor.fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
        
        # Verify x_username is in the group (either creator or member)
        if group["creator"] != x_username:
            cursor = await db.execute(
                "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, x_username)
            )
            member = await cursor.fetchone()
            if not member:
                raise HTTPException(status_code=403, detail="Grup üyesi değilsiniz.")
        
        # Üyeyi gruba ekle
        await db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, username) VALUES (?, ?)",
            (group_id, req.username)
        )
        await db.commit()
        return {"status": "ok", "message": f"'{req.username}' gruba eklendi."}


@router.delete("/api/groups/{group_id}/members/{username}")
async def remove_group_member(
    request: Request,
    group_id: str,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Gruptan bir üyeyi çıkarır."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        cursor = await db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,))
        group = await cursor.fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
            
        # Only group creator can remove others, but any member can leave (delete themselves)
        if x_username != username and group["creator"] != x_username:
            raise HTTPException(status_code=403, detail="Sadece grup kurucusu üyeleri çıkarabilir veya kendi kendinize gruptan çıkabilirsiniz.")
            
        await db.execute(
            "DELETE FROM group_members WHERE group_id = ? AND username = ?",
            (group_id, username)
        )
        await db.commit()
        return {"status": "ok", "message": f"'{username}' gruptan çıkarıldı."}


@router.get("/api/groups/{username}")
async def list_user_groups(
    request: Request,
    username: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Belirtilen kullanıcının dahil olduğu grupları listeler."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    if x_username != username:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim.")
    async with db_session() as db:
        cursor = await db.execute(
            """SELECT g.group_id, g.group_name, g.creator, g.created_at
               FROM groups g
               JOIN group_members gm ON g.group_id = gm.group_id
               WHERE gm.username = ?""",
               (username,)
        )
        rows = await cursor.fetchall()
        return {
            "groups": [
                {
                    "group_id": r["group_id"],
                    "group_name": r["group_name"],
                    "creator": r["creator"],
                    "created_at": r["created_at"]
                }
                for r in rows
            ]
        }


@router.get("/api/groups/{group_id}/members")
async def list_group_members(
    request: Request,
    group_id: str,
    x_username: str = Header(...),
    x_timestamp: str = Header(...),
    x_signature: str = Header(...)
):
    """Bir grubun tüm üyelerini ve public key'lerini döndürür (anahtar dağıtımı için)."""
    await verify_request_signature(request, x_username, x_timestamp, x_signature)
    async with db_session() as db:
        cursor = await db.execute("SELECT creator FROM groups WHERE group_id = ?", (group_id,))
        group = await cursor.fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="Grup bulunamadı.")
            
        # Verify x_username is in the group (either creator or member)
        if group["creator"] != x_username:
            cursor = await db.execute(
                "SELECT username FROM group_members WHERE group_id = ? AND username = ?",
                (group_id, x_username)
            )
            member = await cursor.fetchone()
            if not member:
                raise HTTPException(status_code=403, detail="Grup üyesi değilsiniz.")
                
        cursor = await db.execute(
            """SELECT u.username, u.public_key
               FROM users u
               JOIN group_members gm ON u.username = gm.username
               WHERE gm.group_id = ?""",
            (group_id,)
        )
        rows = await cursor.fetchall()
        return {
            "members": [
                {
                    "username": r["username"],
                    "public_key": r["public_key"]
                }
                for r in rows
            ]
        }
