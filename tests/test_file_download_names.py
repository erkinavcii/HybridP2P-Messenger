"""Alınan dosyanın kaydı (desktop/bubbles.save_received_file) — yol atlatma (K3) ve üzerine yazma yok."""

import pytest

from desktop.bubbles import save_received_file

NAMES = [
    "..\\..\\x.bat",
    "../../x.sh",
    "C:\\Windows\\x.dll",
    "/etc/passwd",
    "CON.txt",
    "",
    "rapor.pdf",
]


@pytest.mark.parametrize("name", NAMES)
def test_kayit_hedef_klasorde_kalir(tmp_path, name):
    folder = tmp_path / "hedef"
    data = b"icerik-" + name.encode()
    dest = save_received_file(name, data, base_dir=folder)
    assert dest.resolve().parent == folder.resolve()
    assert dest.read_bytes() == data
    assert not (tmp_path / "x.bat").exists() and not (tmp_path.parent / "x.sh").exists()


@pytest.mark.parametrize("name", NAMES)
def test_ayni_ad_uzerine_yazmaz(tmp_path, name):
    folder = tmp_path / "hedef"
    a = save_received_file(name, b"bir", base_dir=folder)
    b = save_received_file(name, b"iki", base_dir=folder)
    assert a != b
    assert a.read_bytes() == b"bir"
    assert b.read_bytes() == b"iki"
