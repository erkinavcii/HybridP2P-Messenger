"""Sunucusuz dosya aktarımı (desktop/p2p_files.py) — ad temizleme, bütünlük, onay
zorunluluğu ve iki gerçek aiortc eşi arasında uçtan uca aktarım."""

import asyncio
import hashlib
import json
import os

import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from desktop import p2p_core as core
from desktop import p2p_files as pf


@pytest.mark.parametrize("raw,expected", [
    ("rapor.pdf", "rapor.pdf"),
    ("../../etc/passwd", "passwd"),
    ("C:\\Windows\\System32\\evil.dll", "evil.dll"),
    ("..", "dosya"),
    ("", "dosya"),
    ("CON.txt", "dosya_CON.txt"),
    ("a\x00b<c>.txt", "a_b_c_.txt"),
    (" .gizli. ", "gizli"),
])
def test_safe_filename(raw, expected):
    assert pf.safe_filename(raw) == expected


def test_long_name_keeps_extension():
    name = pf.safe_filename("x" * 300 + ".jpeg")
    assert len(name) <= 150 and name.endswith(".jpeg")


def test_unique_path_never_overwrites(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "a (2).txt").write_text("2")
    assert pf.unique_path(tmp_path, "a.txt").name == "a (3).txt"
    assert pf.unique_path(tmp_path, "yeni").name == "yeni"


def _offer_for(data: bytes, name="veri.bin"):
    return {"t": "file_offer", "fid": pf.new_fid(), "name": name, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def test_incoming_happy_path_and_atomic_move(tmp_path):
    data = os.urandom(50_000)
    reg = pf.IncomingRegistry(tmp_path)
    off = _offer_for(data, "../kacis.bin")
    reg.offer(off)
    reg.accept(off["fid"])
    for i in range(0, len(data), pf.CHUNK_SIZE):
        reg.chunk(pf.chunk_frame(off["fid"], data[i:i + pf.CHUNK_SIZE]))
    path = reg.end(off["fid"])
    assert path.parent == tmp_path and path.name == "kacis.bin"   # klasör dışına çıkamadı
    assert path.read_bytes() == data
    assert not list(tmp_path.glob(".*.part"))


def test_unsolicited_chunks_are_rejected(tmp_path):
    reg = pf.IncomingRegistry(tmp_path)
    off = _offer_for(b"abc")
    reg.offer(off)                                   # teklif var ama KABUL edilmedi
    with pytest.raises(pf.FileTransferError):
        reg.chunk(pf.chunk_frame(off["fid"], b"abc"))
    assert not any(tmp_path.iterdir())


def test_oversize_and_bad_hash_are_discarded(tmp_path):
    reg = pf.IncomingRegistry(tmp_path)
    off = _offer_for(b"12345")
    reg.offer(off); reg.accept(off["fid"])
    with pytest.raises(pf.FileTransferError):
        reg.chunk(pf.chunk_frame(off["fid"], b"123456"))       # bildirilenden büyük

    bad = _offer_for(b"orijinal")
    reg.offer(bad); reg.accept(bad["fid"])
    reg.chunk(pf.chunk_frame(bad["fid"], b"degistir"))         # aynı boy, farklı içerik
    with pytest.raises(pf.FileTransferError, match="SHA-256"):
        reg.end(bad["fid"])
    assert not any(p.name.endswith(".part") or p.name == "veri.bin" for p in tmp_path.iterdir())


@pytest.mark.parametrize("size", [0, pf.MAX_FILE_BYTES + 1])
def test_size_limits(tmp_path, size):
    reg = pf.IncomingRegistry(tmp_path)
    off = {"t": "file_offer", "fid": pf.new_fid(), "name": "x", "size": size, "sha256": "0" * 64}
    reg.offer(off)
    with pytest.raises(pf.FileTransferError):
        reg.accept(off["fid"])


def test_cancel_removes_partial(tmp_path):
    reg = pf.IncomingRegistry(tmp_path)
    off = _offer_for(b"x" * 100)
    reg.offer(off); reg.accept(off["fid"])
    reg.chunk(pf.chunk_frame(off["fid"], b"x" * 10))
    reg.cancel(off["fid"])
    assert not any(tmp_path.iterdir())


def test_file_frames_validated():
    fid = pf.new_fid()
    off = core.parse_frame(json.dumps({"t": "file_offer", "fid": fid, "name": "a", "size": 3, "sha256": "a" * 64}))
    assert off == {"t": "file_offer", "fid": fid, "name": "a", "size": 3, "sha256": "a" * 64}
    assert core.parse_frame(json.dumps({"t": "file_accept", "fid": fid})) == {"t": "file_accept", "fid": fid}
    for bad in [{"t": "file_accept", "fid": "../x"},
                {"t": "file_offer", "fid": fid, "name": "a", "size": "3", "sha256": "a" * 64},
                {"t": "file_offer", "fid": fid, "name": "a", "size": True, "sha256": "a" * 64},
                {"t": "file_offer", "fid": fid, "name": "a", "size": 3, "sha256": "zz"}]:
        assert core.parse_frame(json.dumps(bad)) is None


# ─────────────────────────── uçtan uca ───────────────────────────

def test_file_transfer_between_two_real_peers(tmp_path):
    data = os.urandom(300_000)                       # ~19 parça
    src = tmp_path / "gonder" / "fotograf.jpg"
    src.parent.mkdir()
    src.write_bytes(data)
    inbox = tmp_path / "gelen"

    async def gather(pc):
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.02)

    async def scenario():
        cfg = RTCConfiguration(iceServers=[])
        pa, pb = RTCPeerConnection(cfg), RTCPeerConnection(cfg)
        reg = pf.IncomingRegistry(inbox)
        done = asyncio.get_running_loop().create_future()
        accepted = asyncio.Event()
        try:
            ch_a = pa.createDataChannel(core.CHANNEL_LABEL)
            ch_a.on("message", lambda m: accepted.set()
                    if (core.parse_frame(m) or {}).get("t") == "file_accept" else None)

            @pb.on("datachannel")
            def on_dc(ch):
                @ch.on("message")
                def on_msg(m):
                    try:
                        if isinstance(m, bytes):
                            reg.chunk(m)
                            return
                        f = core.parse_frame(m)
                        if f["t"] == "file_offer":
                            reg.offer(f)
                            reg.accept(f["fid"])           # kullanıcı "Kabul et"e bastı
                            ch.send(json.dumps({"t": "file_accept", "fid": f["fid"]}))
                        elif f["t"] == "file_end":
                            done.set_result(reg.end(f["fid"]))
                    except Exception as ex:
                        if not done.done():
                            done.set_exception(ex)

            await pa.setLocalDescription(await pa.createOffer()); await gather(pa)
            await pb.setRemoteDescription(RTCSessionDescription(pa.localDescription.sdp, "offer"))
            await pb.setLocalDescription(await pb.createAnswer()); await gather(pb)
            await pa.setRemoteDescription(RTCSessionDescription(pb.localDescription.sdp, "answer"))
            for _ in range(200):
                if ch_a.readyState == "open":
                    break
                await asyncio.sleep(0.05)

            offer = pf.make_offer(src)
            ch_a.send(json.dumps(offer))
            await asyncio.wait_for(accepted.wait(), 10)
            progress = []
            assert await pf.send_file(ch_a, src, offer["fid"], on_progress=progress.append)
            path = await asyncio.wait_for(done, 30)
            return path, progress
        finally:
            await pa.close(); await pb.close()

    path, progress = core.run(scenario()).result(timeout=90)
    assert path.read_bytes() == data and path.name == "fotograf.jpg"
    assert progress[-1] == 1.0
