// static/js/app.js

import { state, API_URL } from './state.js';
import {
    connectWebSocket,
    fetchOfflineMessages,
    queryUserPresence
} from './ws.js';
import {
    renderInbox,
    selectChat,
    sendMessage,
    formatBytes
} from './ui.js';
import {
    generateKeyPair,
    derivePublicKeyFromPrivateKey,
    signRegistration,
    getFingerprintJS,
    decryptPrivateKey,
    encryptPrivateKey,
    makeAuthHeadersJS,
    generateSymmetricKeyHex,
    encryptMessageJS
} from './crypto.js';
import {
    dbGet,
    dbSet,
    dbDel,
    persistChats,
    syncChatSettingsFromServer,
    syncUserGroups,
    saveChatToLocalStorage
} from './db.js';
import { initVoipEvents } from './voip.js';

// Initialize VoIP events
initVoipEvents();

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
                    alert(`Kullanıcı '${partner}' bulunamadı veya sunucu hatası.`);
                }
            } catch (err) {
                alert("Bağlantı hatası: " + err);
            }
        } else {
            // Group Chat Creation
            const groupName = document.getElementById("group-name-input").value.trim();
            const membersRaw = document.getElementById("group-members-input").value.trim().toLowerCase();
            if (!groupName) {
                alert("Lütfen bir grup adı girin.");
                return;
            }
            const members = membersRaw.split(",").map(m => m.trim()).filter(m => m.length > 0);
            if (members.length === 0) {
                alert("Lütfen en az bir üye girin.");
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
                                const encKey = await encryptMessageJS(groupKeyHex, mData.public_key);
                                
                                const distPayload = {
                                    type: "group_key_dist",
                                    sender: state.username,
                                    recipient: m,
                                    group_id: groupId,
                                    encrypted_payload: encKey,
                                    timestamp: new Date().toISOString()
                                };
                                
                                if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                                    state.ws.send(JSON.stringify(distPayload));
                                } else {
                                    const fPath = "/api/send_ws_fallback";
                                    const fBody = JSON.stringify({ payload: JSON.stringify(distPayload) });
                                    const fHeaders = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", fPath, fBody);
                                    fHeaders["Content-Type"] = "application/json";
                                    await fetch(`${API_URL}${fPath}`, { method: "POST", headers: fHeaders, body: fBody });
                                }
                            }
                        } catch (distErr) {
                            console.error(`Failed to distribute key to ${m}:`, distErr);
                        }
                    }
                    
                    closeModal();
                    selectChat(groupId, null, true);
                } else {
                    const errTxt = await resp.text();
                    alert("Grup oluşturulurken hata oluştu: " + errTxt);
                }
            } catch (err) {
                alert("Grup oluşturulamadı: " + err.message);
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
        if (!confirm("Bu gruba ait şifreleme anahtarını yenilemek istiyor musunuz? Gruptaki tüm aktif üyeler yeni anahtarla güncellenecektir.")) return;
        
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
                        const encKey = await encryptMessageJS(groupKeyHex, m.public_key);
                        const distPayload = {
                            type: "group_key_dist",
                            sender: state.username,
                            recipient: m.username,
                            group_id: state.recipient,
                            encrypted_payload: encKey,
                            timestamp: new Date().toISOString()
                        };
                        
                        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                            state.ws.send(JSON.stringify(distPayload));
                        } else {
                            const fPath = "/api/send_ws_fallback";
                            const fBody = JSON.stringify({ payload: JSON.stringify(distPayload) });
                            const fHeaders = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", fPath, fBody);
                            fHeaders["Content-Type"] = "application/json";
                            await fetch(`${API_URL}${fPath}`, { method: "POST", headers: fHeaders, body: fBody });
                        }
                    } catch (err) {
                        console.error("Rekey distribution error for member:", m.username, err);
                    }
                }
                alert("Grup anahtarı başarıyla yenilendi ve dağıtıldı.");
            } else {
                alert("Grup üyeleri listesi alınamadı.");
            }
        } catch (err) {
            alert("Anahtar yenileme hatası: " + err.message);
        } finally {
            groupRekeyBtn.disabled = false;
        }
    });
}

if (leaveGroupBtn) {
    leaveGroupBtn.addEventListener("click", async () => {
        if (!state.recipient || !state.isGroup) return;
        if (!confirm("Bu gruptan çıkmak ve tüm grup mesaj geçmişini silmek istediğinize emin misiniz?")) return;
        
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
                alert("Gruptan başarıyla çıkıldı.");
            } else {
                const txt = await res.text();
                alert("Gruptan çıkılamadı: " + txt);
            }
        } catch (err) {
            alert("Gruptan çıkma hatası: " + err.message);
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
            await dbGet("chats", "history").then(val => {
                if (val) state.chats = val;
            });
            
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
            alert("Lütfen önce bir sohbet seçin!");
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
            alert("Dosya boyutu çok büyük! Maksimum limit 10 MB'tır.");
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
        
        loginBtn.disabled = true;
        loginBtn.innerHTML = `<div class="spinner"></div> Signing In...`;
        
        try {
            if (importKeyCheckbox.checked) {
                let importedPem = privateKeyInput.value.trim();
                if (!importedPem) {
                    alert("Lütfen özel anahtarınızı (Private Key PEM) yapıştırın veya dosya yükleyin.");
                    loginBtn.disabled = false;
                    loginBtn.innerText = "Sign In";
                    return;
                }
                
                if (decryptKeyCheckbox.checked) {
                    const password = decryptPasswordInput.value;
                    if (!password) {
                        alert("Şifreli anahtarı çözmek için lütfen parola girin.");
                        loginBtn.disabled = false;
                        loginBtn.innerText = "Sign In";
                        return;
                    }
                    try {
                        importedPem = await decryptPrivateKey(importedPem, password);
                    } catch (decryptErr) {
                        alert("Şifre çözme başarısız! Parola yanlış veya dosya bozuk olabilir.");
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
                    alert("İçeri aktarma hatası: Geçersiz Özel Anahtar (Private Key PEM) formatı.");
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
                await dbGet("chats", "history").then(val => {
                    if (val) state.chats = val;
                });
                
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
                alert("Giriş başarısız: " + errText);
            }
        } catch (err) {
            alert("Bağlantı hatası: " + err);
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
            alert("Lütfen yedek dosyasını şifrelemek için bir parola girin.");
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
            alert("Şifreli yedek dosyası başarıyla indirildi.");
        } catch (err) {
            alert("Şifreleme hatası: " + err.message);
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

// Send Message bindings
if (sendBtn) sendBtn.addEventListener("click", sendMessage);
if (messageInput) {
    messageInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
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
