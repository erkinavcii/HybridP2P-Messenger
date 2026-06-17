// static/js/ws.js

import { state, API_URL, WS_URL } from './state.js';
import {
    signDataJS,
    decryptMessageJS,
    verifySignatureJS,
    decryptSymmetricJS,
    makeAuthHeadersJS
} from './crypto.js';
import {
    dbSet,
    dbGet,
    persistChats,
    syncUserGroups,
    getContactPubKey,
    fetchGroupName,
    saveChatToLocalStorage
} from './db.js';
import {
    handleIncomingCallOffer,
    handleCallAnswer,
    handleCallRejected,
    handleCallEnded,
    handleIceCandidate
} from './voip.js';

// Send read receipt
export async function sendReadReceipt(recipient, timestamp) {
    if (!recipient || !timestamp) return;
    const payload = {
        type: "read_receipt",
        recipient: recipient,
        timestamp: timestamp
    };
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify(payload));
    } else {
        // REST fallback
        const path = "/api/send_ws_fallback";
        const bodyText = JSON.stringify({ payload: JSON.stringify(payload) });
        try {
            const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", path, bodyText);
            headers["Content-Type"] = "application/json";
            await fetch(`${API_URL}${path}`, {
                method: "POST",
                headers: headers,
                body: bodyText
            });
        } catch (err) {
            console.error("Failed to send read receipt fallback:", err);
        }
    }
}

// Query user presence
export async function queryUserPresence(partner) {
    try {
        const path = `/api/status/${partner}`;
        const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
        const res = await fetch(`${API_URL}${path}`, { headers });
        if (res.status === 200) {
            const data = await res.json();
            window.dispatchEvent(new CustomEvent('presence-updated', { detail: { partner, online: data.online } }));
        }
    } catch (err) {
        console.error("Durum sorgulanamadı:", err);
    }
}

// Fetch Offline Messages
export async function fetchOfflineMessages() {
    try {
        const path = `/api/fetch_messages/${state.username}`;
        const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
        const res = await fetch(`${API_URL}${path}`, { headers });
        
        if (res.status === 200) {
            const data = await res.json();
            for (let msg of data.messages) {
                try {
                    const plaintext = await decryptMessageJS(msg.encrypted_payload, state.privateKeyPem);
                    const msgObj = {
                        sender: msg.sender,
                        content: plaintext,
                        timestamp: msg.timestamp,
                        view_once: msg.view_once,
                        encrypted_payload: msg.encrypted_payload,
                        read: false
                    };
                    await saveChatToLocalStorage(msg.sender, msgObj);
                } catch (decryptErr) {
                    console.error("Çevrimdışı mesaj çözülemedi:", decryptErr);
                }
            }
        }
    } catch (err) {
        console.error("Çevrimdışı mesajlar çekilemedi:", err);
    }
}

// ── WebSocket Loop ──
export function connectWebSocket() {
    const wsUrl = `${WS_URL}/ws/${state.username}`;
    console.log("WebSocket bağlantısı kuruluyor...", wsUrl);
    
    const dot = document.getElementById("server-status-dot");
    const txt = document.getElementById("server-status-text");
    if (dot && txt) {
        dot.className = "server-status-dot offline";
        txt.innerText = "Connecting...";
    }
    
    const ws = new WebSocket(wsUrl);
    state.ws = ws;
    
    ws.onmessage = async (event) => {
        try {
            const data = JSON.parse(event.data);
            
            // 1. Challenge Response Authentication
            if (data.type === "challenge") {
                const challenge = data.challenge;
                const encoder = new TextEncoder();
                const sigB64 = await signDataJS(state.privateKeyPem, encoder.encode(challenge));
                ws.send(JSON.stringify({
                    "type": "auth",
                    "signature": sigB64
                }));
            }
            
            // 2. Auth Success
            else if (data.type === "auth_result" && data.status === "success") {
                console.log("WebSocket başarıyla doğrulandı ve bağlandı!");
                const dot = document.getElementById("server-status-dot");
                const txt = document.getElementById("server-status-text");
                if (dot && txt) {
                    dot.className = "server-status-dot online";
                    txt.innerText = "Connected";
                }
            }
            
            // 3. E2E Chat Message received
            else if (data.type === "message") {
                const sender = data.sender;
                try {
                    const plaintext = await decryptMessageJS(data.encrypted_payload, state.privateKeyPem);
                    const isActive = (state.recipient === sender);

                    const msgObj = {
                        sender: sender,
                        content: plaintext,
                        timestamp: data.timestamp || new Date().toISOString(),
                        view_once: !!data.view_once,
                        encrypted_payload: data.encrypted_payload,
                        read: isActive
                    };

                    if (!state.chats[sender]) {
                        state.chats[sender] = { partner: sender, ephemeral: false, messages: [] };
                    }
                    const exists = state.chats[sender].messages.some(
                        m => m.timestamp === msgObj.timestamp && m.content === msgObj.content
                    );
                    if (!exists) state.chats[sender].messages.push(msgObj);
                    await persistChats();

                    if (isActive) {
                        window.dispatchEvent(new CustomEvent('messages-updated'));
                        sendReadReceipt(sender, msgObj.timestamp);
                    }
                    window.dispatchEvent(new CustomEvent('chats-updated'));
                } catch (decryptErr) {
                    console.error("Gelen mesaj çözülemedi:", decryptErr);
                }
            }
            
            // E2E Chat File Message received
            else if (data.type === "file_message") {
                const sender = data.sender;
                const isActive = (state.recipient === sender);
                
                const msgObj = {
                    sender: sender,
                    is_file: true,
                    file_uuid: data.file_uuid,
                    original_name: data.original_name,
                    file_type: data.file_type,
                    view_once: !!data.view_once,
                    timestamp: data.timestamp || new Date().toISOString(),
                    content: "[File: " + data.original_name + "]",
                    read: isActive
                };
                
                if (!state.chats[sender]) {
                    state.chats[sender] = { partner: sender, ephemeral: false, messages: [] };
                }
                
                const exists = state.chats[sender].messages.some(
                    m => m.is_file && m.file_uuid === data.file_uuid
                );
                if (!exists) state.chats[sender].messages.push(msgObj);
                await persistChats();
                
                if (isActive) {
                    window.dispatchEvent(new CustomEvent('messages-updated'));
                    sendReadReceipt(sender, msgObj.timestamp);
                }
                window.dispatchEvent(new CustomEvent('chats-updated'));
            }

            // 4. Ephemeral toggle received from partner
            else if (data.type === "ephemeral_toggle") {
                const sender = data.sender;
                const newEphemeral = !!data.ephemeral;
                if (!state.chats[sender]) {
                    state.chats[sender] = { partner: sender, ephemeral: false, messages: [] };
                }
                state.chats[sender].ephemeral = newEphemeral;
                await persistChats();
                // If this is the active chat, sync button + show system msg
                if (state.recipient === sender) {
                    state.ephemeral = newEphemeral;
                    window.dispatchEvent(new CustomEvent('ephemeral-status-synced', { detail: { partner: sender, ephemeral: newEphemeral } }));
                    window.dispatchEvent(new CustomEvent('system-message', {
                        detail: {
                            partner: sender,
                            text: newEphemeral
                                ? `🔒 ${sender} turned ON ephemeral mode — messages will not be saved`
                                : `💾 ${sender} turned OFF ephemeral mode — message history is now saved`
                        }
                    }));
                }
            }

            // 5. Delivery Status Ack
            else if (data.type === "delivery_ack") {
                console.log("Delivery ack:", data);
            }
            
            // 6. Read Receipt received
            else if (data.type === "read_receipt") {
                const sender = data.sender;
                const timestamp = data.timestamp;
                console.log(`Received read receipt from ${sender} for timestamp ${timestamp}`);
                
                if (state.chats[sender]) {
                    state.chats[sender].messages.forEach(m => {
                        if (m.sender === state.username && m.timestamp === timestamp) {
                            m.read = true;
                        }
                    });
                    await persistChats();
                    if (state.recipient === sender) {
                        window.dispatchEvent(new CustomEvent('messages-updated'));
                    }
                }
            }
            
            // Group Key Distribution
            else if (data.type === "group_key_dist") {
                const sender = data.sender;
                const groupId = data.group_id;
                const encPayload = data.encrypted_payload;
                try {
                    const groupKeyHex = await decryptMessageJS(encPayload, state.privateKeyPem);
                    await dbSet("group_keys", groupId, groupKeyHex);
                    console.log(`Grup anahtarı alındı: ${groupId} (Gönderen: ${sender})`);
                    await syncUserGroups();
                } catch (ex) {
                    console.error("Grup anahtarı çözme hatası:", ex);
                }
            }
            
            // Group Message received
            else if (data.type === "group_message") {
                const sender = data.sender;
                const groupId = data.group_id;
                const encPayload = data.encrypted_payload;
                const sig = data.signature;
                const timestamp = data.timestamp || new Date().toISOString();
                const isActive = (state.recipient === groupId);
                
                // Verify sender's signature
                let verified = false;
                const senderPubKey = await getContactPubKey(sender);
                if (senderPubKey && sig) {
                    try {
                        const dataToVerify = new TextEncoder().encode(sender + ":" + groupId + ":" + encPayload);
                        verified = await verifySignatureJS(senderPubKey, sig, dataToVerify);
                    } catch (sigErr) {
                        console.error("Grup mesajı imza doğrulama hatası:", sigErr);
                    }
                }
                
                if (!verified) {
                    console.error(`HATA: '${sender}' kullanıcısının grup imza doğrulaması başarısız!`);
                    if (isActive) {
                        window.dispatchEvent(new CustomEvent('system-message', {
                            detail: {
                                partner: groupId,
                                text: `⚠️ UYARI: '${sender}' adlı kullanıcının kimliği doğrulanamadı (Taklit Teşebbüsü)!`
                            }
                        }));
                    }
                    return;
                }
                
                // Decrypt symmetric group message
                const groupKeyHex = await dbGet("group_keys", groupId);
                if (groupKeyHex) {
                    try {
                        const plaintext = await decryptSymmetricJS(encPayload, groupKeyHex);
                        
                        const msgObj = {
                            sender: sender,
                            content: plaintext,
                            timestamp: timestamp,
                            read: isActive
                        };
                        
                        if (!state.chats[groupId]) {
                            const groupName = await fetchGroupName(groupId);
                            state.chats[groupId] = {
                                partner: groupId,
                                groupName: groupName,
                                ephemeral: false,
                                isGroup: true,
                                messages: []
                            };
                        }
                        
                        const exists = state.chats[groupId].messages.some(
                            m => m.timestamp === msgObj.timestamp && m.content === msgObj.content
                        );
                        if (!exists) state.chats[groupId].messages.push(msgObj);
                        await persistChats();
                        
                        if (isActive) {
                            window.dispatchEvent(new CustomEvent('messages-updated'));
                        }
                        window.dispatchEvent(new CustomEvent('chats-updated'));
                    } catch (decEx) {
                        console.error("Grup mesajı deşifre edilemedi:", decEx);
                    }
                } else {
                    console.warn(`Grup anahtarı bulunamadı: ${groupId}. Lütfen kurucudan anahtarı yenilemesini isteyin.`);
                }
            }

            // 7. VoIP: Incoming call offer
            else if (data.type === "call_offer") {
                handleIncomingCallOffer(data);
            }

            // 8. VoIP: Call answer (we are the caller, received callee's answer)
            else if (data.type === "call_answer") {
                handleCallAnswer(data);
            }

            // 9. VoIP: Call rejected / busy / timeout / unavailable
            else if (data.type === "call_reject") {
                handleCallRejected(data);
            }

            // 10. VoIP: Remote party ended the call
            else if (data.type === "call_end") {
                handleCallEnded(data);
            }

            // 11. VoIP: ICE candidate from remote peer
            else if (data.type === "ice_candidate") {
                handleIceCandidate(data);
            }
        } catch (e) {
            console.error("WS mesaj işleme hatası:", e);
        }
    };

    ws.onclose = (event) => {
        console.log("WebSocket bağlantısı kesildi. Reconnecting in 3s...");
        const dot = document.getElementById("server-status-dot");
        const txt = document.getElementById("server-status-text");
        if (dot && txt) {
            dot.className = "server-status-dot offline";
            txt.innerText = "Disconnected";
        }
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (err) => {
        console.error("WebSocket Hatası:", err);
        const dot = document.getElementById("server-status-dot");
        const txt = document.getElementById("server-status-text");
        if (dot && txt) {
            dot.className = "server-status-dot offline";
            txt.innerText = "Error";
        }
    };
}
