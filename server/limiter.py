import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Varsayılan: AÇIK. Yalnızca test sunucusu kapatır (tek IP'den çok sayıda
# kayıt yapan test oturumu /api/register'ın 20/dk sınırına takılmasın diye).
# Üretimde bu değişkeni ayarlamayın.
_enabled = os.getenv("HYBRIDP2P_RATE_LIMIT", "1").lower() not in ("0", "false", "off")

limiter = Limiter(key_func=get_remote_address, enabled=_enabled)
