// static/js/prefs.js — Cihaz geneli arayüz tercihleri (tema, bildirim sesi, dil).
//
// Bilinçli olarak localStorage: tema ilk boyamadan önce eşzamanlı okunmalı
// (index.html'deki satır içi betik), ve bunlar gizli veri değildir. Anahtarlar,
// sohbetler ve grup verileri IndexedDB'de kalır (AGENTS.md §2.4).

const KEY = "hybridp2p_prefs";
const DEFAULTS = { theme: "dark", sound_enabled: true, lang: "en" };

export const I18N = {
    en: {
        app_tagline: "Private End-to-End Encrypted Messaging",
        mode_server: "Server Relay",
        mode_serverless: "Direct P2P",
        mode_server_hint: "Connect to this relay server for inbox, groups, and offline delivery.",
        mode_serverless_hint: "Connect directly peer-to-peer with no server at all (signed text, files & calls).",
        generating_keys: "Generating 4096-bit RSA keys...",
        username: "Username",
        username_placeholder: "example: alice",
        advanced_key_restore: "Advanced & Key Restore",
        import_key_label: "Import existing Private Key (.pem)",
        upload_backup: "Upload Backup File (.txt / .pem)",
        private_key_pem: "Private Key PEM",
        key_encrypted: "This key is password-encrypted",
        decryption_password: "Decryption Password",
        sign_in: "Continue",
        open_serverless_p2p: "Open Direct P2P (No Server)",
        new_chat_tooltip: "New Chat / Group",
        p2p_tooltip: "Direct P2P (Serverless)",
        settings_tooltip: "Settings & Profile",
        search_chats: "Search chats and messages...",
        server_connecting: "Connecting...",
        empty_chat_title: "Start Messaging Securely",
        empty_chat_sub: "All cryptographic keys stay on your device. The relay server cannot read your messages.",
        voice_call_tooltip: "Voice Call",
        video_call_tooltip: "Video Call",
        ephemeral_tooltip: "Disappearing Chat (Don't save history)",
        view_once_tooltip: "Send next message as View-Once",
        type_message: "Message...",
        settings_title: "Settings & Profile",
        profile_photo: "Profile Photo",
        profile_photo_sub: "Sent end-to-end encrypted to contacts",
        choose: "Choose",
        contacts_title: "Contacts",
        contacts_sub: "Verify safety fingerprints",
        open: "Open",
        language_label: "Language",
        theme_label: "Theme",
        theme_sub: "Dark / Light mode",
        toggle: "Toggle",
        notif_sound: "Notification Sound",
        notif_sub: "Audio alert on new message",
        backup_key_title: "Private Key Backup",
        backup_key_sub: "Export encrypted PEM key",
        backup: "Backup",
        serverless_mode: "Serverless P2P Page",
        serverless_mode_sub: "Signed chat, files & calls without relay",
        sign_out: "Sign Out",
        new_chat_title: "New Chat / Group",
        direct_message: "Direct Message",
        group_chat: "Group Chat",
        group_name: "Group Name",
        group_members: "Group Members (comma-separated)",
        my_groups: "MY GROUPS",
        cancel: "Cancel",
        start_chat: "Start Chat",
        p2p_modal_title: "Direct P2P Call",
        p2p_banner_text: "For signed text, files & calls without any server:",
        p2p_banner_link: "Open Serverless →",
        p2p_start_call: "Start Call",
        p2p_answer_call: "Join Call",
        p2p_call_type: "Call Type",
        p2p_step1: "1. Create Invite Code",
        copy_invite: "Copy Invite Code",
        show_raw_code: "Show raw code",
        p2p_step2: "2. Paste Peer's Answer Code",
        p2p_step3: "3. Connect & Start Call",
        p2p_join_step1: "1. Paste Peer's Invite Code",
        p2p_join_step2: "2. Accept & Generate Answer",
        copy_answer: "Copy Answer Code",
        edit_message_title: "Edit Message",
        delete_for_everyone: "Delete for everyone",
        save: "Save",
        view_once_title: "View-Once Message",
    },
    tr: {
        app_tagline: "Uçtan Uca Şifreli Güvenli İletişim",
        mode_server: "Sunuculu Mod",
        mode_serverless: "Doğrudan P2P",
        mode_server_hint: "Gelen kutusu, gruplar ve çevrimdışı mesajlar için röle sunucusuna bağlanın.",
        mode_serverless_hint: "Arada sunucu olmadan doğrudan cihazdan cihaza bağlanın (imzalı mesaj, dosya ve arama).",
        generating_keys: "4096-bit RSA anahtarları üretiliyor...",
        username: "Kullanıcı Adı",
        username_placeholder: "örnek: alice",
        advanced_key_restore: "Gelişmiş ve Anahtar Yükleme",
        import_key_label: "Mevcut Özel Anahtarı İçe Aktar (.pem)",
        upload_backup: "Yedek Dosyası Yükle (.txt / .pem)",
        private_key_pem: "Özel Anahtar PEM",
        key_encrypted: "Bu anahtar parolayla şifrelenmiş",
        decryption_password: "Çözme Parolası",
        sign_in: "Devam Et",
        open_serverless_p2p: "Doğrudan P2P Aç (Sunucusuz)",
        new_chat_tooltip: "Yeni Sohbet / Grup",
        p2p_tooltip: "Doğrudan P2P (Sunucusuz)",
        settings_tooltip: "Ayarlar ve Profil",
        search_chats: "Sohbetlerde ve mesajlarda ara...",
        server_connecting: "Bağlanıyor...",
        empty_chat_title: "Güvenli İletişime Başlayın",
        empty_chat_sub: "Tüm şifreleme anahtarları cihazınızda tutulur. Röle sunucu mesajlarınızı okuyamaz.",
        voice_call_tooltip: "Sesli Arama",
        video_call_tooltip: "Görüntülü Arama",
        ephemeral_tooltip: "Geçici Sohbet (Geçmiş kaydedilmez)",
        view_once_tooltip: "Sonraki mesajı Tek Görünümlük gönder",
        type_message: "Mesaj...",
        settings_title: "Ayarlar ve Profil",
        profile_photo: "Profil Fotoğrafı",
        profile_photo_sub: "Kişilere şifreli gönderilir",
        choose: "Seç",
        contacts_title: "Kişi Rehberi",
        contacts_sub: "Güvenlik parmak izlerini doğrulayın",
        open: "Aç",
        language_label: "Dil / Language",
        theme_label: "Tema",
        theme_sub: "Koyu / Açık görünüm",
        toggle: "Değiştir",
        notif_sound: "Bildirim Sesi",
        notif_sub: "Yeni mesajda sesli uyarı",
        backup_key_title: "Özel Anahtar Yedeği",
        backup_key_sub: "Şifreli PEM anahtarı dışa aktar",
        backup: "Yedekle",
        serverless_mode: "Sunucusuz P2P Sayfası",
        serverless_mode_sub: "Sunucu olmadan imzalı sohbet, dosya ve arama",
        sign_out: "Çıkış Yap",
        new_chat_title: "Yeni Sohbet / Grup",
        direct_message: "Birebir Mesaj",
        group_chat: "Grup Sohbeti",
        group_name: "Grup Adı",
        group_members: "Üyeler (virgülle ayırın)",
        my_groups: "GRUPLARIM",
        cancel: "İptal",
        start_chat: "Sohbet Başlat",
        p2p_modal_title: "Doğrudan P2P Arama",
        p2p_banner_text: "Sunucu olmadan imzalı mesaj ve dosya için:",
        p2p_banner_link: "Sunucusuz Aç →",
        p2p_start_call: "Arama Başlat",
        p2p_answer_call: "Aramaya Katıl",
        p2p_call_type: "Arama Türü",
        p2p_step1: "1. Davet Kodu Oluştur",
        copy_invite: "Davet Kodunu Kopyala",
        show_raw_code: "Ham kodu göster",
        p2p_step2: "2. Karşı Tarafın Yanıt Kodunu Yapıştır",
        p2p_step3: "3. Bağlan ve Aramayı Başlat",
        p2p_join_step1: "1. Karşı Tarafın Davet Kodunu Yapıştır",
        p2p_join_step2: "2. Onayla ve Yanıt Oluştur",
        copy_answer: "Yanıt Kodunu Kopyala",
        edit_message_title: "Mesajı Düzenle",
        delete_for_everyone: "Herkesten sil",
        save: "Kaydet",
        view_once_title: "Tek Görünümlük Mesaj",
    },
};

function load() {
    try {
        return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") };
    } catch {
        return { ...DEFAULTS };
    }
}

export function getPref(name) {
    return load()[name];
}

export function setPref(name, value) {
    const prefs = load();
    prefs[name] = value;
    try {
        localStorage.setItem(KEY, JSON.stringify(prefs));
    } catch {
        // Depolama kapalıysa tercih yalnızca bu oturumda geçerli olur
    }
}

export function getLang() {
    const lang = getPref("lang");
    return lang in I18N ? lang : "en";
}

export function t(key) {
    const lang = getLang();
    return (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
}

export function setLang(lang) {
    const next = lang in I18N ? lang : "en";
    setPref("lang", next);
    applyI18n();
    return next;
}

export function applyI18n() {
    if (typeof document === "undefined") return;
    document.querySelectorAll("[data-i18n]").forEach((el) => {
        el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
        el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll("[data-i18n-title]").forEach((el) => {
        el.title = t(el.dataset.i18nTitle);
    });
}

export function applyTheme(theme = getPref("theme")) {
    document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
}

export function toggleTheme() {
    const next = getPref("theme") === "light" ? "dark" : "light";
    setPref("theme", next);
    applyTheme(next);
    return next;
}

// ── Bildirim sesi: masaüstündekiyle aynı iki tonlu kısa bip (dosya yok, WebAudio) ──
let audioCtx = null;

export function playNotification() {
    if (!getPref("sound_enabled")) return;
    try {
        audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
        if (audioCtx.state === "suspended") audioCtx.resume();
        const t0 = audioCtx.currentTime;
        [[880, 0], [1320, 0.12]].forEach(([freq, offset]) => {
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = "sine";
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.0001, t0 + offset);
            gain.gain.exponentialRampToValueAtTime(0.18, t0 + offset + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, t0 + offset + 0.11);
            osc.connect(gain).connect(audioCtx.destination);
            osc.start(t0 + offset);
            osc.stop(t0 + offset + 0.12);
        });
    } catch (err) {
        console.warn("Bildirim sesi çalınamadı:", err);
    }
}
