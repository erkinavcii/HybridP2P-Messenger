"""desktop/settings_store.py — Kullanıcıdan bağımsız uygulama tercihleri.

~/.hybridp2p_messenger/settings.json içinde tutulur. Kullanıcı başına değil
cihaz başınadır, çünkü tema giriş ekranında (kimse giriş yapmadan) da geçerli.

Dosya bozuk/okunamazsa varsayılanlara dönülür — tercih dosyası hiçbir koşulda
uygulamanın açılmasını engellememeli.
"""

import json
import threading

from crypto_utils import KEYS_DIR

DEFAULTS = {
    "theme": "dark",          # "dark" | "light"
    "sound_enabled": True,    # yeni mesaj bildirim sesi
}

_lock = threading.Lock()


def _path():
    return KEYS_DIR / "settings.json"


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        stored = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return data


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def set(key: str, value):
    if key not in DEFAULTS:
        raise KeyError(f"Bilinmeyen ayar: {key}")
    with _lock:
        data = load()
        data[key] = value
        _path().parent.mkdir(parents=True, exist_ok=True)
        tmp = _path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_path())   # atomik yazım: yarım dosya kalmasın
