// static/js/app.js

import { state, API_URL } from './state.js';
import {
    connectWebSocket,
    fetchOfflineMessages,
    queryUserPresence,
    sendTyping,
    sendFrameWithFallback,
    sendGroupKeyTo
} from './ws.js';
import {
    renderInbox,
    selectChat,
    sendMessage,
    formatBytes,
    appendSystemMessage,
    openContactsDialog
} from './ui.js';
import {
    generateKeyPair,
    derivePublicKeyFromPrivateKey,
    signRegistration,
    getFingerprintJS,
    decryptPrivateKey,
    encryptPrivateKey,
    makeAuthHeadersJS,
    generateSymmetricKeyHex
} from './crypto.js';
import {
    dbGet,
    dbSet,
    dbDel,
    persistChats,
    syncChatSettingsFromServer,
    syncUserGroups,
    saveChatToLocalStorage,
    loadChatsFromLocalStorage
} from './db.js';
import { initVoipEvents } from './voip.js';
import { applyTheme, toggleTheme, getPref, setPref } from './prefs.js';
import { loadAvatarCache, setOwnAvatar, broadcastAvatar, avatarUrl } from './avatar.js';

// Initialize VoIP events
initVoipEvents();

// PWA service worker (kurulabilir uygulama + çevrimdışı arayüz kabuğu).
// Yalnızca güvenli bağlamda çalışır: https veya localhost.
if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js").catch((err) =>
            console.warn("Service worker kaydedilemedi:", err));
    });
}

// ── UI Interactions & Event Binding ──
const loginScreen = document.getElementById("login-screen");
const chatScreen = document.getElementById("chat-screen");
const loginBtn = document.getElementById("login-btn");
const loadingKeysSection = document.getElementById("loading-keys-section");
const loginForm = document.getElementById("login-form");
const usernameInput = document.getElementById("username-input");
const globalSearch = document.getElementById("global-search-input");

const emptyChatState = document.getElementById("empty-chat-state");
const activeChat = document.getElementById("active-chat");
const messageInput = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");

// File elements
const fileInput = document.getElementById("file-input");
const attachBtn = document.getElementById("attach-btn");
const stagedFileContainer = document.getElementById("staged-file-container");
const stagedFileName = document.getElementById("staged-file-name");
const stagedFileSize = document.getElementById("staged-file-size");
const cancelStagedBtn = document.getElementById("cancel-staged-btn");

const newChatModal = document.getElementById("new-chat-modal");
const addChatBtn = document.getElementById("add-chat-btn");
const modalCloseBtn = document.getElementById("modal-close-btn");
const newChatCancelBtn = document.getElementById("new-chat-cancel-btn");
const newChatConfirmBtn = document.getElementById("new-chat-confirm-btn");
const newUsernameInput = document.getElementById("new-username-input");

const groupRekeyBtn = document.getElementById("group-rekey-btn");
const leaveGroupBtn = document.getElementById("leave-group-btn");
const ephemeralBtn = document.getElementById("ephemeral-btn");
const viewOnceBtn = document.getElementById("view-once-btn");

// Open Modal
if (addChatBtn) {
    addChatBtn.addEventListener("click", () => {
        newChatModal.classList.add("active");
        newUsernameInput.focus();
    });
}

// Close Modal
const closeModal = () => {
    newChatModal.classList.remove("active");
    newUsernameInput.value = "";
    document.getElementById("group-name-input").value = "";
    document.getElementById("group-members-input").value = "";
    tabDm.click();
};

if (modalCloseBtn) modalCloseBtn.addEventListener("click", closeModal);
if (newChatCancelBtn) newChatCancelBtn.addEventListener("click", closeModal);

const tabDm = document.getElementById("tab-dm");
const tabGroup = document.getElementById("tab-group");
const sectionDm = document.getElementById("section-dm");
const sectionGroup = document.getElementById("section-group");
let activeTabName = "dm"; // "dm" or "group"

if (tabDm) {
    tabDm.addEventListener("click", () => {
        activeTabName = "dm";
        tabDm.classList.add("active");
        tabDm.style.color = "var(--accent-color)";
        tabDm.style.borderBottom = "2px solid var(--accent-color)";
        
        tabGroup.classList.remove("active");
        tabGroup.style.color = "var(--text-muted)";
        tabGroup.style.borderBottom = "none";
        
        sectionDm.style.display = "block";
        sectionGroup.style.display = "none";
        newChatConfirmBtn.innerText = "Start Chat";
        newUsernameInput.focus();
    });
}

if (tabGroup) {
    tabGroup.addEventListener("click", () => {
        activeTabName = "group";
        tabGroup.classList.add("active");
        tabGroup.style.color = "var(--accent-color)";
        tabGroup.style.borderBottom = "2px solid var(--accent-color)";
        
        tabDm.classList.remove("active");
        tabDm.style.color = "var(--text-muted)";
        tabDm.style.borderBottom = "none";
        
        sectionDm.style.display = "none";
        sectionGroup.style.display = "flex";
        newChatConfirmBtn.innerText = "Create Group";
        document.getElementById("group-name-input").focus();
    });
}

if (newChatConfirmBtn) {
    newChatConfirmBtn.addEventListener("click", async () => {
        if (activeTabName === "dm") {
            const partner = newUsernameInput.value.trim().toLowerCase();
            if (!partner) return;
            if (partner === state.username) {
                alert("You cannot add yourself!");
                return;
            }
            
            try {
                const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", `/api/public_key/${partner}`);
                const res = await fetch(`${API_URL}/api/public_key/${partner}`, { headers });
                
                if (res.status === 200) {
                    const data = await res.json();
                    await saveChatToLocalStorage(partner, null);
                    closeModal();
                    selectChat(partner, data.public_key, false);
                } else {
                    alert(`User '${partner}' not found or server error.`);
                }
            } catch (err) {
                alert("Connection error: " + err);
            }
        } else {
            // Group Chat Creation
            const groupName = document.getElementById("group-name-input").value.trim();
            const membersRaw = document.getElementById("group-members-input").value.trim().toLowerCase();
            if (!groupName) {
                alert("Please enter a group name.");
                return;
            }
            const members = membersRaw.split(",").map(m => m.trim()).filter(m => m.length > 0);
            if (members.length === 0) {
                alert("Please enter at least one member.");
                return;
            }
            
            newChatConfirmBtn.disabled = true;
            newChatConfirmBtn.innerText = "Creating...";
            
            try {
                const groupId = "group_" + crypto.randomUUID().replace(/-/g, "").substring(0, 12);
                const groupKeyHex = await generateSymmetricKeyHex();
                
                await dbSet("group_keys", groupId, groupKeyHex);
                
                const allMembers = [...members];
                if (!allMembers.includes(state.username)) {
                    allMembers.push(state.username);
                }
                
                const path = "/api/groups";
                const body = {
                    group_id: groupId,
                    group_name: groupName,
                    creator: state.username,
                    members: allMembers
                };
                const bodyText = JSON.stringify(body);
                const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", path, bodyText);
                headers["Content-Type"] = "application/json";
                
                const resp = await fetch(`${API_URL}${path}`, {
                    method: "POST",
                    headers: headers,
                    body: bodyText
                });
                
                if (resp.status === 200) {
                    state.chats[groupId] = {
                        partner: groupId,
                        groupName: groupName,
                        ephemeral: false,
                        isGroup: true,
                        messages: []
                    };
                    await persistChats();
                    
                    // Distribute group key to each member
                    for (let m of members) {
                        if (m === state.username) continue;
                        try {
                            const mPath = `/api/public_key/${m}`;
                            const mHeaders = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", mPath);
                            const mRes = await fetch(`${API_URL}${mPath}`, { headers: mHeaders });
                            if (mRes.status === 200) {
                                const mData = await mRes.json();
                                // İmzalı; grup sunucuda oluştuktan SONRA gönderilir
                                // (sunucu üye olmayana iletmez — K2)
                                await sendGroupKeyTo(m, groupId, groupKeyHex, mData.public_key);
                            }
                        } catch (distErr) {
                            console.error(`Failed to distribute key to ${m}:`, distErr);
                        }
                    }
                    
                    closeModal();
                    selectChat(groupId, null, true);
                } else {
                    const errTxt = await resp.text();
                    alert("Error creating group: " + errTxt);
                }
            } catch (err) {
                alert("Failed to create group: " + err.message);
            } finally {
                newChatConfirmBtn.disabled = false;
                newChatConfirmBtn.innerText = "Create Group";
            }
        }
    });
}

// Group actions
if (groupRekeyBtn) {
    groupRekeyBtn.addEventListener("click", async () => {
        if (!state.recipient || !state.isGroup) return;
        if (!confirm("Rotate the encryption key for this group? All active members will receive the new key.")) return;
        
        groupRekeyBtn.disabled = true;
        try {
            const groupKeyHex = await generateSymmetricKeyHex();
            await dbSet("group_keys", state.recipient, groupKeyHex);
            
            const path = `/api/groups/${state.recipient}/members`;
            const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
            const res = await fetch(`${API_URL}${path}`, { headers });
            
            if (res.status === 200) {
                const data = await res.json();
                for (let m of data.members) {
                    if (m.username === state.username) continue;
                    try {
                        // İmzalı: alıcılar imzasız anahtar değişikliğini reddeder (K2)
                        await sendGroupKeyTo(m.username, state.recipient, groupKeyHex, m.public_key);
                    } catch (err) {
                        console.error("Rekey distribution error for member:", m.username, err);
                    }
                }
                alert("Group key rotated and distributed successfully.");
            } else {
                alert("Could not retrieve group members list.");
            }
        } catch (err) {
            alert("Key rotation error: " + err.message);
        } finally {
            groupRekeyBtn.disabled = false;
        }
    });
}

if (leaveGroupBtn) {
    leaveGroupBtn.addEventListener("click", async () => {
        if (!state.recipient || !state.isGroup) return;
        if (!confirm("Are you sure you want to leave this group and delete all local group message history?")) return;
        
        leaveGroupBtn.disabled = true;
        try {
            const path = `/api/groups/${state.recipient}/members/${state.username}`;
            const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "DELETE", path);
            const res = await fetch(`${API_URL}${path}`, { method: "DELETE", headers });
            
            if (res.status === 200) {
                const groupId = state.recipient;
                delete state.chats[groupId];
                await dbDel("group_keys", groupId);
                await persistChats();
                
                state.recipient = null;
                state.isGroup = false;
                emptyChatState.classList.remove("hidden");
                activeChat.classList.add("hidden");
                renderInbox();
                alert("Left the group successfully.");
            } else {
                const txt = await res.text();
                alert("Failed to leave group: " + txt);
            }
        } catch (err) {
            alert("Error leaving group: " + err.message);
        } finally {
            leaveGroupBtn.disabled = false;
        }
    });
}

// Initialize Keys on Page Load
window.addEventListener("load", async () => {
    const savedPriv = await dbGet("keys", "private_key");
    const savedPub = await dbGet("keys", "public_key");
    
    if (savedPriv && savedPub) {
        state.privateKeyPem = savedPriv;
        state.publicKeyPem = savedPub;
        
        const sessionUser = sessionStorage.getItem("session_username");
        if (sessionUser) {
            state.username = sessionUser;
            document.getElementById("my-username").innerText = sessionUser;
            // Oturum geri yüklenince de sohbet ekranına geç (normal girişle aynı)
            loginScreen.classList.remove("active");
            chatScreen.classList.add("active");
            
            const fp = await getFingerprintJS(state.publicKeyPem);
            document.getElementById("my-fingerprint").innerText = fp;
            document.getElementById("my-fingerprint").title = fp;
            
            const privKeyDisplay = document.getElementById("my-private-key-display");
            if (privKeyDisplay) {
                privKeyDisplay.addEventListener("click", () => {
                    navigator.clipboard.writeText(state.privateKeyPem).then(() => {
                        privKeyDisplay.innerText = "COPIED!";
                        privKeyDisplay.style.color = "var(--online-color)";
                        setTimeout(() => {
                            privKeyDisplay.innerText = "••••••••••••••••••••";
                            privKeyDisplay.style.color = "var(--accent-hover)";
                        }, 2000);
                    }).catch(err => {
                        console.error("Copy failed:", err);
                    });
                });
            }
            
            // Sync with IndexedDB chats
            // Sohbetler "chats_<kullanıcı>" anahtarında (persistChats ile aynı). Eskiden
            // "history" okunuyordu: boş durum geri yazılıp her yenilemede geçmiş siliniyordu.
            await loadChatsFromLocalStorage();
            await loadAvatarCache();
            refreshOwnAvatarButton();
            
            await fetchOfflineMessages();
            await syncChatSettingsFromServer();
            await syncUserGroups();
            renderInbox();
            connectWebSocket();
        }
    } else {
        loginForm.classList.add("hidden");
        loadingKeysSection.classList.remove("hidden");
        
        // Generate 4096-bit RSA Keys in background
        const keys = await generateKeyPair();
        
        await dbSet("keys", "private_key", keys.privateKey);
        await dbSet("keys", "public_key", keys.publicKey);
        
        state.privateKeyPem = keys.privateKey;
        state.publicKeyPem = keys.publicKey;
        
        loadingKeysSection.classList.add("hidden");
        loginForm.classList.remove("hidden");
    }
});

// Sign In Key Import Checkboxes
const importKeyCheckbox = document.getElementById("import-key-checkbox");
const importKeySection = document.getElementById("import-key-section");
const privateKeyInput = document.getElementById("private-key-input");
const decryptKeyCheckbox = document.getElementById("decrypt-key-checkbox");
const decryptPasswordSection = document.getElementById("decrypt-password-section");
const decryptPasswordInput = document.getElementById("decrypt-password-input");
const importKeyFile = document.getElementById("import-key-file");

if (importKeyCheckbox) {
    importKeyCheckbox.addEventListener("change", () => {
        if (importKeyCheckbox.checked) {
            importKeySection.classList.remove("hidden");
            privateKeyInput.focus();
        } else {
            importKeySection.classList.add("hidden");
        }
    });
}

if (decryptKeyCheckbox) {
    decryptKeyCheckbox.addEventListener("change", () => {
        if (decryptKeyCheckbox.checked) {
            decryptPasswordSection.classList.remove("hidden");
            decryptPasswordInput.focus();
        } else {
            decryptPasswordSection.classList.add("hidden");
        }
    });
}

if (importKeyFile) {
    importKeyFile.addEventListener("change", (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function(evt) {
            privateKeyInput.value = evt.target.result.trim();
            if (evt.target.result.split(":").length === 3) {
                decryptKeyCheckbox.checked = true;
                decryptPasswordSection.classList.remove("hidden");
                decryptPasswordInput.focus();
            } else {
                decryptKeyCheckbox.checked = false;
                decryptPasswordSection.classList.add("hidden");
            }
        };
        reader.readAsText(file);
    });
}

// File Attachment handlers
if (attachBtn) {
    attachBtn.addEventListener("click", () => {
        if (!state.recipient) {
            alert("Please select a chat first!");
            return;
        }
        fileInput.click();
    });
}

if (fileInput) {
    fileInput.addEventListener("change", (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (file.size > 10 * 1024 * 1024) {
            alert("File size is too large! Maximum limit is 10 MB.");
            fileInput.value = "";
            return;
        }

        state.staged_file = file;
        stagedFileName.innerText = file.name;
        stagedFileSize.innerText = `(${formatBytes(file.size)})`;
        stagedFileContainer.classList.remove("hidden");
        messageInput.focus();
    });
}

if (cancelStagedBtn) {
    cancelStagedBtn.addEventListener("click", () => {
        state.staged_file = null;
        fileInput.value = "";
        stagedFileContainer.classList.add("hidden");
    });
}

// Sign In Button
if (loginBtn) {
    loginBtn.addEventListener("click", async () => {
        const username = usernameInput.value.trim().toLowerCase();
        if (!username) return;
        // Sunucudaki kuralın aynısı (server/routes/users.py USERNAME_RE)
        if (!/^[a-z0-9_]{2,32}$/.test(username)) {
            alert("Invalid username: 2-32 characters; only a-z, 0-9, and _ are allowed.");
            return;
        }
        
        loginBtn.disabled = true;
        loginBtn.innerHTML = `<div class="spinner"></div> Signing In...`;
        
        try {
            if (importKeyCheckbox.checked) {
                let importedPem = privateKeyInput.value.trim();
                if (!importedPem) {
                    alert("Please paste your Private Key PEM or upload a key file.");
                    loginBtn.disabled = false;
                    loginBtn.innerText = "Sign In";
                    return;
                }
                
                if (decryptKeyCheckbox.checked) {
                    const password = decryptPasswordInput.value;
                    if (!password) {
                        alert("Please enter a password to decrypt the key.");
                        loginBtn.disabled = false;
                        loginBtn.innerText = "Sign In";
                        return;
                    }
                    try {
                        importedPem = await decryptPrivateKey(importedPem, password);
                    } catch (decryptErr) {
                        alert("Decryption failed! Wrong password or corrupted file.");
                        loginBtn.disabled = false;
                        loginBtn.innerText = "Sign In";
                        return;
                    }
                }

                try {
                    const pubKeyPem = await derivePublicKeyFromPrivateKey(importedPem);
                    state.privateKeyPem = importedPem;
                    state.publicKeyPem = pubKeyPem;
                    await dbSet("keys", "private_key", importedPem);
                    await dbSet("keys", "public_key", pubKeyPem);
                } catch (e) {
                    alert("Import error: Invalid Private Key PEM format.");
                    loginBtn.disabled = false;
                    loginBtn.innerText = "Sign In";
                    return;
                }
            }
            const timestamp = new Date().toISOString();
            const sigB64 = await signRegistration(username, timestamp, state.publicKeyPem, state.privateKeyPem);
            
            const registerRes = await fetch(`${API_URL}/api/register`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    username: username,
                    public_key: state.publicKeyPem,
                    timestamp: timestamp,
                    signature: sigB64
                })
            });
            
            if (registerRes.status === 200) {
                state.username = username;
                sessionStorage.setItem("session_username", username);
                document.getElementById("my-username").innerText = username;
                
                const fp = await getFingerprintJS(state.publicKeyPem);
                document.getElementById("my-fingerprint").innerText = fp;
                document.getElementById("my-fingerprint").title = fp;
                
                // Sync with IndexedDB chats
                // Sohbetler "chats_<kullanıcı>" anahtarında (persistChats ile aynı). Eskiden
                // "history" okunuyordu: boş durum geri yazılıp her yenilemede geçmiş siliniyordu.
                await loadChatsFromLocalStorage();
                await loadAvatarCache();
                refreshOwnAvatarButton();
                
                loginScreen.classList.remove("active");
                chatScreen.classList.add("active");
                
                const privKeyDisplay = document.getElementById("my-private-key-display");
                if (privKeyDisplay) {
                    privKeyDisplay.addEventListener("click", () => {
                        navigator.clipboard.writeText(state.privateKeyPem).then(() => {
                            privKeyDisplay.innerText = "COPIED!";
                            privKeyDisplay.style.color = "var(--online-color)";
                            setTimeout(() => {
                                privKeyDisplay.innerText = "••••••••••••••••••••";
                                privKeyDisplay.style.color = "var(--accent-hover)";
                            }, 2000);
                        }).catch(err => {
                            console.error("Copy failed:", err);
                        });
                    });
                }

                await fetchOfflineMessages();
                await syncChatSettingsFromServer();
                await syncUserGroups();
                renderInbox();
                connectWebSocket();
            } else {
                const errText = await registerRes.text();
                alert("Sign in failed: " + errText);
            }
        } catch (err) {
            alert("Connection error: " + err);
        } finally {
            loginBtn.disabled = false;
            loginBtn.innerText = "Sign In";
        }
    });
}

// Logout
const logoutBtn = document.getElementById("logout-btn");
if (logoutBtn) {
    logoutBtn.addEventListener("click", () => {
        if (state.ws) state.ws.close();
        sessionStorage.removeItem("session_username");
        window.location.reload();
    });
}

// Backup Key Modal
const backupKeyModal = document.getElementById("backup-key-modal");
const showKeyBtn = document.getElementById("show-key-btn");
const backupKeyCloseBtn = document.getElementById("backup-key-close-btn");
const backupKeyTextarea = document.getElementById("backup-key-textarea");
const backupKeyCopyBtn = document.getElementById("backup-key-copy-btn");
const backupKeyEncryptDownloadBtn = document.getElementById("backup-key-encrypt-download-btn");
const backupPasswordInput = document.getElementById("backup-password-input");

if (showKeyBtn) {
    showKeyBtn.addEventListener("click", () => {
        backupKeyTextarea.value = state.privateKeyPem;
        backupKeyModal.classList.add("active");
    });
}

if (backupKeyCloseBtn) {
    backupKeyCloseBtn.addEventListener("click", () => {
        backupKeyModal.classList.remove("active");
    });
}

if (backupKeyCopyBtn) {
    backupKeyCopyBtn.addEventListener("click", () => {
        backupKeyTextarea.select();
        document.execCommand("copy");
        backupKeyCopyBtn.innerText = "Copied!";
        setTimeout(() => {
            backupKeyCopyBtn.innerText = "Copy PEM";
        }, 2000);
    });
}

if (backupKeyEncryptDownloadBtn) {
    backupKeyEncryptDownloadBtn.addEventListener("click", async () => {
        const password = backupPasswordInput.value;
        if (!password) {
            alert("Please enter a password to encrypt the backup file.");
            return;
        }
        try {
            backupKeyEncryptDownloadBtn.disabled = true;
            backupKeyEncryptDownloadBtn.innerText = "Encrypting...";
            
            const encryptedKey = await encryptPrivateKey(state.privateKeyPem, password);
            
            const blob = new Blob([encryptedKey], {type: 'text/plain'});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `hybridp2p_backup_${state.username || 'user'}.txt`;
            a.click();
            URL.revokeObjectURL(url);
            
            backupPasswordInput.value = "";
            backupKeyEncryptDownloadBtn.disabled = false;
            backupKeyEncryptDownloadBtn.innerText = "Encrypt & Download";
            alert("Encrypted backup file downloaded successfully.");
        } catch (err) {
            alert("Encryption error: " + err.message);
            backupKeyEncryptDownloadBtn.disabled = false;
            backupKeyEncryptDownloadBtn.innerText = "Encrypt & Download";
        }
    });
}

// Reconnect Button
const reconnectServerBtn = document.getElementById("reconnect-server-btn");
if (reconnectServerBtn) {
    reconnectServerBtn.addEventListener("click", () => {
        if (state.username) {
            if (state.ws) {
                try {
                    state.ws.close();
                } catch (e) {}
            }
            connectWebSocket();
        }
    });
}

// ── "yazıyor…" sinyali (masaüstüyle aynı zamanlama) ──
// Yazarken en fazla 3 sn'de bir "yazıyor" gönderilir; 4 sn tuşa basılmazsa,
// alan boşalırsa ya da mesaj gönderilirse "bıraktı" gönderilir.
const TYPING_RESEND_MS = 3000;
const TYPING_IDLE_MS = 4000;
let typingTarget = null;
let typingLastSent = 0;
let typingIdleTimer = null;

function stopTypingSignal() {
    clearTimeout(typingIdleTimer);
    if (typingTarget) sendTyping(typingTarget, false);
    typingTarget = null;
    typingLastSent = 0;
}

function onMessageInput() {
    const text = messageInput.value.trim();
    if (!text || !state.recipient || state.isGroup) {
        stopTypingSignal();
        return;
    }
    if (typingTarget && typingTarget !== state.recipient) stopTypingSignal();
    const now = Date.now();
    if (now - typingLastSent > TYPING_RESEND_MS) {
        sendTyping(state.recipient, true);
        typingTarget = state.recipient;
        typingLastSent = now;
    }
    clearTimeout(typingIdleTimer);
    typingIdleTimer = setTimeout(stopTypingSignal, TYPING_IDLE_MS);
}

// Send Message bindings
if (sendBtn) sendBtn.addEventListener("click", () => { stopTypingSignal(); sendMessage(); });
if (messageInput) {
    messageInput.addEventListener("input", onMessageInput);
    messageInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            stopTypingSignal();
            sendMessage();
        }
    });
}

// ── Kişi rehberi ve profil fotoğrafı ──
const contactsBtn = document.getElementById("contacts-btn");
const ownAvatarBtn = document.getElementById("own-avatar-btn");
const ownAvatarInput = document.getElementById("own-avatar-input");

function refreshOwnAvatarButton() {
    const url = avatarUrl(state.username);
    if (ownAvatarBtn && url) ownAvatarBtn.innerHTML = `<img class="avatar-img" src="${url}" alt="">`;
}

if (contactsBtn) contactsBtn.addEventListener("click", openContactsDialog);
if (ownAvatarBtn && ownAvatarInput) {
    ownAvatarBtn.addEventListener("click", () => ownAvatarInput.click());
    ownAvatarInput.addEventListener("change", async () => {
        const file = ownAvatarInput.files[0];
        ownAvatarInput.value = "";
        if (!file) return;
        try {
            await setOwnAvatar(await file.arrayBuffer());
        } catch (err) {
            alert("Could not use photo: " + err.message);
            return;
        }
        refreshOwnAvatarButton();
        const sent = await broadcastAvatar(sendFrameWithFallback);
        console.log(`Encrypted profile photo sent to ${sent} contacts.`);
    });
}

// ── Tema ve bildirim sesi düğmeleri ──
applyTheme();
const themeToggleBtn = document.getElementById("theme-toggle-btn");
const soundToggleBtn = document.getElementById("sound-toggle-btn");

function refreshPrefButtons() {
    if (themeToggleBtn) {
        themeToggleBtn.title = getPref("theme") === "light" ? "Switch to dark theme" : "Switch to light theme";
    }
    if (soundToggleBtn) {
        const on = !!getPref("sound_enabled");
        soundToggleBtn.classList.toggle("pref-off", !on);
        soundToggleBtn.title = on ? "Notification sound on (click to mute)" : "Notification sound muted (click to unmute)";
    }
}
if (themeToggleBtn) themeToggleBtn.addEventListener("click", () => { toggleTheme(); refreshPrefButtons(); });
if (soundToggleBtn) soundToggleBtn.addEventListener("click", () => {
    setPref("sound_enabled", !getPref("sound_enabled"));
    refreshPrefButtons();
});
refreshPrefButtons();

// Toggle Ephemeral Mode button
if (ephemeralBtn) {
    ephemeralBtn.addEventListener("click", async () => {
        state.ephemeral = !state.ephemeral;
        const newEphemeral = state.ephemeral;

        if (newEphemeral) {
            ephemeralBtn.classList.add("ephemeral-active");
            ephemeralBtn.title = "Ephemeral Mode ON (chat history not saved)";
        } else {
            ephemeralBtn.classList.remove("ephemeral-active");
            ephemeralBtn.title = "Ephemeral Mode (Don't save chat history)";
        }

        // Update chat state
        if (state.recipient) {
            if (!state.chats[state.recipient]) {
                state.chats[state.recipient] = { partner: state.recipient, ephemeral: false, messages: [] };
            }
            state.chats[state.recipient].ephemeral = newEphemeral;
            await persistChats();

            // Send ephemeral_toggle notification to partner via WS
            const toggleMsg = {
                type: "ephemeral_toggle",
                sender: state.username,
                recipient: state.recipient,
                ephemeral: newEphemeral,
                timestamp: new Date().toISOString()
            };
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify(toggleMsg));
            } else {
                // REST fallback
                const path = "/api/send_ws_fallback";
                const bodyText = JSON.stringify({ payload: JSON.stringify(toggleMsg) });
                const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", path, bodyText);
                headers["Content-Type"] = "application/json";
                await fetch(`${API_URL}${path}`, { method: "POST", headers, body: bodyText });
            }

            // Show system message in own chat
            appendSystemMessage(state.recipient, newEphemeral
                ? "🔒 Ephemeral mode ON — messages will not be saved"
                : "🔓 Ephemeral mode OFF — message history is now saved"
            );
        }
    });
}

// Toggle View-Once button
if (viewOnceBtn) {
    viewOnceBtn.addEventListener("click", () => {
        state.viewOnceNext = !state.viewOnceNext;
        if (state.viewOnceNext) {
            viewOnceBtn.classList.add("view-once-active");
        } else {
            viewOnceBtn.classList.remove("view-once-active");
        }
    });
}

// Search
if (globalSearch) {
    globalSearch.addEventListener("input", (e) => {
        renderInbox(e.target.value);
    });
}

// Periodic user presence query
setInterval(() => {
    if (state.username && state.recipient) {
        queryUserPresence(state.recipient);
    }
}, 5000);
