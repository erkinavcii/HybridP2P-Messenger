"""serverless_client.py — HybridP2P Messenger'ı SUNUCU OLMADAN başlatır ("telsiz" modu).

Röle sunucusuna hiç bağlanmaz ve kayıt olmaz. İki kişi aynı anda açıkken,
tek kullanımlık imzalı bağlantı kodlarını (kopyala-yapıştır ya da QR) herhangi
bir kanaldan değiş tokuş ederek doğrudan (WebRTC) bağlanır: yazılı mesaj,
dosya aktarımı, sesli/görüntülü arama.

Kullanım:  python serverless_client.py
Aynı mod normal istemcide de giriş ekranındaki "Sunucusuz başlat" ile açılır.
Kimlik anahtarı ve rehber normal istemciyle ortaktır (~/.hybridp2p_messenger/).
Açılışta client.boot() önce zincir yükleyiciyi gösterir, ağır modülleri
arka planda yükler (L1).
"""

import flet as ft

from client import ASSETS_DIR, boot, prefetch


def main(page: ft.Page):
    boot(page, serverless_only=True)


if __name__ == "__main__":
    prefetch()
    ft.run(main, assets_dir=ASSETS_DIR)
