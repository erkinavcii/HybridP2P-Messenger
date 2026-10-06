"""Rate limit varsayılan olarak AÇIK olmalı (test oturumu sunucusu kapatsa bile).

Ayrı, rate limit açık bir sunucu başlatıp /api/register'ın 20/dk sınırını doğrular.
"""

import os
import subprocess
import sys
import time

import requests

from conftest import ROOT, _free_port


def test_register_rate_limit_enabled_by_default(tmp_path):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k != "HYBRIDP2P_RATE_LIMIT"}
    env["HYBRIDP2P_DB_PATH"] = str(tmp_path / "rl.db")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app",
                             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if requests.get(f"{base}/health", timeout=0.5).status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.15)
        # Geçersiz kullanıcı adı → 400; sınır yine de sayılır. 21. istek 429 olmalı.
        codes = [requests.post(f"{base}/api/register", json={
            "username": "X", "public_key": "", "timestamp": "", "signature": ""}, timeout=5).status_code
            for _ in range(21)]
        assert codes[:20] == [400] * 20
        assert codes[20] == 429
    finally:
        proc.terminate()
        proc.wait(timeout=5)
