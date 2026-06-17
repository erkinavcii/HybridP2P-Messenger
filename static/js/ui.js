// static/js/ui.js

import { state, API_URL } from './state.js';
import {
    arrayBufferToBase64,
    decryptMessageJS,
    decryptBytesJS,
    encryptBytesJS,
    encryptSymmetricJS,
    signDataJS,
    encryptMessageJS,
    makeAuthHeadersJS
} from './crypto.js';
import {
    persistChats,
    saveChatToLocalStorage,
    dbGet,
    getContactPubKey,
    fetchGroupName
} from './db.js';
import {
    sendReadReceipt,
    queryUserPresence
} from './ws.js';

// DOM Selectors
const inboxList = document.getElementById("inbox-list");
const chatBody = document.getElementById("chat-body");
const messageInput = document.getElementById("message-input");
const fileInput = document.getElementById("file-input");
const stagedFileContainer = document.getElementById("staged-file-container");
const stagedFileName = document.getElementById("staged-file-name");
const stagedFileSize = document.getElementById("staged-file-size");
const uploadProgressContainer = document.getElementById("upload-progress-container");
const uploadProgressBar = document.getElementById("upload-progress-bar");
const uploadStatusText = document.getElementById("upload-status-text");
const uploadPercentageText = document.getElementById("upload-percentage-text");
const chatPartnerName = document.getElementById("chat-partner-name");
const chatPartnerStatus = document.getElementById("chat-partner-status");
const statusLabel = document.getElementById("status-label");
const emptyChatState = document.getElementById("empty-chat-state");
const activeChat = document.getElementById("active-chat");

const audioCallBtn = document.getElementById("audio-call-btn");
const videoCallBtn = document.getElementById("video-call-btn");
const ephemeralBtn = document.getElementById("ephemeral-btn");
const viewOnceBtn = document.getElementById("view-once-btn");
const groupRekeyBtn = document.getElementById("group-rekey-btn");
const leaveGroupBtn = document.getElementById("leave-group-btn");

// File formatting helpers
export function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function guessFileType(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'];
    const videoExts = ['mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv'];
    const audioExts = ['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'];
    
    if (imageExts.includes(ext)) return 'image';
    if (videoExts.includes(ext)) return 'video';
    if (audioExts.includes(ext)) return 'audio';
    return 'document';
}

export function getFileIconEmoji(fileType) {
    if (fileType === "image") return "🖼️";
    if (fileType === "video") return "🎥";
    if (fileType === "audio") return "🎵";
    return "📄";
}

// Format ISO timestamp to HH:MM
export function formatTime(isoStr) {
    try {
        const date = new Date(isoStr);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return "";
    }
}

export function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Append system/status message
export function appendSystemMessage(partner, text) {
    if (state.recipient !== partner) return;
    const el = document.createElement("div");
    el.className = "msg-container system";
    el.innerHTML = `<div class="msg-system-bubble">${text}</div>`;
    chatBody.appendChild(el);
    chatBody.scrollTop = chatBody.scrollHeight;
}

// Set online presence UI
export function setPresenceUI(partner, isOnline) {
    if (state.recipient !== partner) return;
    const dot = chatPartnerStatus.querySelector(".status-dot");
    
    dot.className = "status-dot";
    if (isOnline) {
        dot.classList.add("online");
        statusLabel.innerText = "Online";
    } else {
        dot.classList.add("offline");
        statusLabel.innerText = "Offline";
    }
}

// Render Inbox / Chat Tile List
export function renderInbox(query = "") {
    if (!inboxList) return;
    inboxList.innerHTML = "";
    const queryClean = query.trim().toLowerCase();
    
    const chatEntries = Object.values(state.chats);
    
    if (chatEntries.length === 0) {
        inboxList.innerHTML = `
            <div class="empty-inbox">
                <div class="empty-inbox-icon">💬</div>
                <span>No chats yet</span>
                <span style="font-size:10px;">Click '+' button to start a new chat.</span>
            </div>
        `;
        return;
    }

    // Filter chats if search query is active
    let filteredChats = chatEntries;
    let filteredMsgs = [];
    
    if (queryClean) {
        filteredChats = chatEntries.filter(c => c.partner.includes(queryClean));
        
        // Global Message text search
        for (let c of chatEntries) {
            const matches = c.messages.filter(m => m.content && m.content.toLowerCase().includes(queryClean) && !m.view_once);
            for (let m of matches) {
                filteredMsgs.push({ partner: c.partner, msg: m });
            }
        }
    }

    // Render matching Chats section
    if (queryClean && filteredChats.length > 0) {
        const header = document.createElement("div");
        header.className = "list-section-header";
        header.innerText = "CHATS";
        inboxList.appendChild(header);
    }

    filteredChats.forEach(c => {
        const lastMsgObj = c.messages[c.messages.length - 1];
        let snippet = lastMsgObj ? lastMsgObj.content : "No messages yet";
        if (lastMsgObj && lastMsgObj.view_once) snippet = "👁 View-once message";
        
        if (snippet && snippet.length > 30) snippet = snippet.substring(0, 27) + "...";
        
        const timeStr = lastMsgObj ? formatTime(lastMsgObj.timestamp) : "";
        
        // Calculate unread count
        const unreadCount = c.messages.filter(m => m.sender !== state.username && !m.read).length;
        
        const activeClass = state.recipient === c.partner ? "active" : "";
        const tile = document.createElement("div");
        tile.className = `chat-tile ${activeClass}`;
        
        const displayName = c.isGroup ? c.groupName : c.partner;
        const avatarText = displayName.substring(0, 2).toUpperCase();
        
        tile.innerHTML = `
            <div class="avatar ${c.isGroup ? 'group' : ''}">${avatarText}</div>
            <div class="chat-tile-content">
                <div class="chat-tile-header">
                    <span class="chat-tile-name">${displayName}</span>
                    <span class="chat-tile-time ${unreadCount > 0 ? 'unread' : ''}">${timeStr}</span>
                </div>
                <div class="chat-tile-footer">
                    <span class="chat-tile-lastmsg">${snippet || ""}</span>
                    ${unreadCount > 0 ? `<span class="badge">${unreadCount}</span>` : ""}
                </div>
            </div>
        `;
        
        tile.addEventListener("click", async () => {
            if (c.isGroup) {
                await selectChat(c.partner, null, true);
            } else {
                // Fetch partner public key first
                try {
                    const path = `/api/public_key/${c.partner}`;
                    const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
                    const res = await fetch(`${API_URL}${path}`, { headers });
                    if (res.status === 200) {
                        const data = await res.json();
                        selectChat(c.partner, data.public_key, false);
                    }
                } catch (err) {
                    console.error(err);
                }
            }
        });
        
        inboxList.appendChild(tile);
    });

    // Render matching Messages section
    if (queryClean && filteredMsgs.length > 0) {
        const header = document.createElement("div");
        header.className = "list-section-header";
        header.innerText = "MESSAGES";
        inboxList.appendChild(header);

        filteredMsgs.forEach(item => {
            let snippet = item.msg.content;
            if (snippet.length > 35) snippet = snippet.substring(0, 32) + "...";
            const timeStr = formatTime(item.msg.timestamp);

            const tile = document.createElement("div");
            tile.className = "chat-tile";
            tile.innerHTML = `
                <div class="avatar" style="width:32px; height:32px; font-size:12px;">${item.partner.substring(0, 2).toUpperCase()}</div>
                <div class="chat-tile-content">
                    <div class="chat-tile-header">
                        <span class="chat-tile-name" style="font-size:13px;">${item.partner}</span>
                        <span class="chat-tile-time">${timeStr}</span>
                    </div>
                    <span class="chat-tile-lastmsg" style="font-size:11px;">${item.msg.sender === state.username ? 'Me' : item.msg.sender}: ${snippet}</span>
                </div>
            `;

            tile.addEventListener("click", async () => {
                try {
                    const path = `/api/public_key/${item.partner}`;
                    const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
                    const res = await fetch(`${API_URL}${path}`, { headers });
                    if (res.status === 200) {
                        const data = await res.json();
                        const globalSearch = document.getElementById("global-search-input");
                        if (globalSearch) globalSearch.value = "";
                        selectChat(item.partner, data.public_key);
                    }
                } catch (err) {
                    console.error(err);
                }
            });

            inboxList.appendChild(tile);
        });
    }
}

// Select active chat session
export async function selectChat(partner, publicKeyPem, isGroup = false) {
    state.recipient = partner;
    state.recipientPubKey = publicKeyPem;
    state.isGroup = isGroup;
    
    // Mark all messages in this chat as read
    if (state.chats[partner]) {
        state.chats[partner].messages.forEach(m => m.read = true);
        persistChats();
        
        // Trigger read receipt for the latest received message
        if (!isGroup) {
            const receivedMsgs = state.chats[partner].messages.filter(m => m.sender !== state.username);
            if (receivedMsgs.length > 0) {
                const latestMsg = receivedMsgs[receivedMsgs.length - 1];
                sendReadReceipt(partner, latestMsg.timestamp);
            }
        }
    }
    
    // Sync ephemeral button visual state with this chat's setting
    const chatEphemeral = state.chats[partner] ? state.chats[partner].ephemeral : false;
    state.ephemeral = chatEphemeral;
    
    if (isGroup) {
        audioCallBtn.classList.add("hidden");
        videoCallBtn.classList.add("hidden");
        ephemeralBtn.classList.add("hidden");
        viewOnceBtn.classList.add("hidden");
        attachBtn.classList.add("hidden");
        groupRekeyBtn.classList.remove("hidden");
        leaveGroupBtn.classList.remove("hidden");
    } else {
        audioCallBtn.classList.remove("hidden");
        videoCallBtn.classList.remove("hidden");
        ephemeralBtn.classList.remove("hidden");
        viewOnceBtn.classList.remove("hidden");
        attachBtn.classList.remove("hidden");
        groupRekeyBtn.classList.add("hidden");
        leaveGroupBtn.classList.add("hidden");
        
        if (chatEphemeral) {
            ephemeralBtn.classList.add("ephemeral-active");
            ephemeralBtn.title = "Ephemeral Mode ON (chat history not saved)";
        } else {
            ephemeralBtn.classList.remove("ephemeral-active");
            ephemeralBtn.title = "Ephemeral Mode (Don't save chat history)";
        }
    }

    emptyChatState.classList.add("hidden");
    activeChat.classList.remove("hidden");
    
    const chatObj = state.chats[partner];
    const displayName = (isGroup && chatObj) ? chatObj.groupName : partner;
    chatPartnerName.innerText = displayName;
    document.getElementById("chat-avatar").innerText = displayName.substring(0, 2).toUpperCase();
    
    if (isGroup) {
        document.getElementById("chat-avatar").classList.add("group");
    } else {
        document.getElementById("chat-avatar").classList.remove("group");
    }
    
    renderMessages();
    renderInbox();
    
    // Set online indicator dynamically
    if (isGroup) {
        chatPartnerStatus.style.visibility = "hidden";
    } else {
        chatPartnerStatus.style.visibility = "visible";
        queryUserPresence(partner);
    }
}

// Render message balloons in chat window
export function renderMessages() {
    if (!chatBody) return;
    chatBody.innerHTML = "";
    const chatObj = state.chats[state.recipient];
    if (!chatObj) return;
    
    chatObj.messages.forEach((m, idx) => {
        const container = document.createElement("div");
        const isMe = m.sender === state.username;
        container.className = `msg-container ${isMe ? 'me' : 'other'}`;
        
        let textNodeHtml = "";
        
        if (m.is_file) {
            const isViewOnce = m.view_once;
            const icon = getFileIconEmoji(m.file_type);
            
            let actionBtnHtml = "";
            if (isViewOnce) {
                if (m.opened) {
                    actionBtnHtml = `<span style="font-size: 11px; color: var(--text-muted);">Opened</span>`;
                } else {
                    actionBtnHtml = `
                        <button class="action-icon-btn" onclick="downloadAndDecryptFile('${state.recipient}', ${idx})" style="color: #ef4444;" title="View Once file">
                            👁️
                        </button>`;
                }
            } else {
                actionBtnHtml = `
                    <button class="action-icon-btn" onclick="downloadAndDecryptFile('${state.recipient}', ${idx})" style="color: var(--accent-hover);" title="Download & Decrypt">
                        📥
                    </button>`;
            }
            
            let imgDisplayHtml = "";
            if (m.decrypted_data_url) {
                imgDisplayHtml = `
                    <div style="margin-top: 8px; max-width: 100%; border-radius: 8px; overflow: hidden; border: 1px solid var(--border-color);">
                        <img src="${m.decrypted_data_url}" style="max-width: 100%; max-height: 200px; display: block; margin: 0 auto;" />
                    </div>
                `;
            }
            
            let statusLabelHtml = m.status_label || (isViewOnce ? "View-Once File" : "Encrypted File");
            
            textNodeHtml = `
                <div style="display: flex; flex-direction: column; gap: 4px;">
                    ${isViewOnce ? `<div style="display: flex; align-items: center; gap: 4px; font-size: 10px; color: #ef4444; font-weight: bold;">🛡️ View-once file</div>` : ""}
                    <div style="display: flex; align-items: center; gap: 10px; background-color: rgba(0,0,0,0.2); padding: 8px 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); min-width: 220px;">
                        <span style="font-size: 24px;">${icon}</span>
                        <div style="display: flex; flex-direction: column; flex: 1; min-width: 0;">
                            <span style="font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: #ffffff;" title="${escapeHtml(m.original_name)}">${escapeHtml(m.original_name)}</span>
                            <span id="file-status-${idx}" style="font-size: 10px; color: var(--text-muted);">${statusLabelHtml}</span>
                        </div>
                        <div id="file-action-${idx}">
                            ${actionBtnHtml}
                        </div>
                    </div>
                    ${imgDisplayHtml}
                </div>
            `;
        } else if (m.view_once) {
            if (m.opened) {
                textNodeHtml = `<span class="view-once-placeholder" style="color:var(--text-muted); cursor:default;">👁 Opened view-once message</span>`;
            } else {
                textNodeHtml = `
                    <div class="view-once-placeholder" onclick="openViewOnceMessage(this, '${state.recipient}', ${idx})">
                        🛡️ <span>View Once Message (Tap to decrypt)</span>
                    </div>
                `;
            }
        } else {
            textNodeHtml = `<span class="msg-text">${escapeHtml(m.content || "")}</span>`;
        }

        container.innerHTML = `
            <div class="msg-bubble ${m.opened ? 'view-once-decrypted' : ''}">
                ${state.isGroup && !isMe ? `<span class="msg-sender">${m.sender}</span>` : ""}
                ${textNodeHtml}
                <div class="msg-meta">
                    <span class="msg-time">${formatTime(m.timestamp)}</span>
                    ${isMe ? `<span class="msg-status-tick ${m.read ? 'read' : ''}">${m.read ? '✓✓' : '✓'}</span>` : ""}
                </div>
            </div>
        `;
        chatBody.appendChild(container);
    });
    
    // Scroll to bottom
    chatBody.scrollTop = chatBody.scrollHeight;
}

// View-once message handler
export async function openViewOnceMessage(element, partner, msgIndex) {
    const chatObj = state.chats[partner];
    if (!chatObj) return;
    const msg = chatObj.messages[msgIndex];
    if (!msg || msg.opened) return;

    try {
        const plaintext = await decryptMessageJS(msg.encrypted_payload, state.privateKeyPem);
        
        const modal = document.getElementById("view-once-modal");
        const contentSpan = document.getElementById("view-once-modal-content");
        const closeBtn = document.getElementById("view-once-modal-close-btn");
        
        contentSpan.innerText = plaintext;
        modal.classList.add("active");
        
        msg.opened = true;
        msg.content = "[Opened view-once message]";
        delete msg.encrypted_payload;
        
        persistChats();
        renderMessages();
        
        const handleClose = () => {
            modal.classList.remove("active");
            contentSpan.innerText = "";
            closeBtn.removeEventListener("click", handleClose);
        };
        
        closeBtn.addEventListener("click", handleClose);
        
    } catch (err) {
        alert("Mesaj çözülemedi: " + err);
    }
}

// Download, Decrypt, and save/render E2EE file
export async function downloadAndDecryptFile(partner, msgIndex) {
    const chatObj = state.chats[partner];
    if (!chatObj) return;
    const m = chatObj.messages[msgIndex];
    if (!m || m.opened) return;

    const fileStatus = document.getElementById(`file-status-${msgIndex}`);
    const fileAction = document.getElementById(`file-action-${msgIndex}`);

    if (fileStatus) fileStatus.innerText = "Downloading...";
    if (fileAction) fileAction.innerHTML = `<div class="spinner" style="width: 14px; height: 14px; border-width: 1.5px;"></div>`;

    try {
        const path = `/api/download_file/${m.file_uuid}`;
        const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
        const res = await fetch(`${API_URL}${path}`, { headers });
        
        if (res.status !== 200) {
            const errText = await res.text();
            throw new Error(errText || "Download failed.");
        }

        const data = await res.json();
        
        if (fileStatus) fileStatus.innerText = "Decrypting...";
        const decryptedBuffer = await decryptBytesJS(data.encrypted_data, state.privateKeyPem);
        
        if (m.view_once) {
            m.opened = true;
            m.content = "[Opened view-once file]";
            
            if (m.file_type === "image") {
                const b64 = arrayBufferToBase64(decryptedBuffer);
                const ext = m.original_name.split('.').pop() || "png";
                const dataUrl = `data:image/${ext};base64,${b64}`;
                
                const modal = document.getElementById("view-once-modal");
                const contentSpan = document.getElementById("view-once-modal-content");
                const closeBtn = document.getElementById("view-once-modal-close-btn");
                
                contentSpan.innerHTML = `
                    <div style="display: flex; flex-direction: column; gap: 8px; align-items: center;">
                        <span style="font-weight: 500; font-size: 13px; color: var(--text-muted);">${escapeHtml(m.original_name)}</span>
                        <img src="${dataUrl}" style="max-width: 100%; max-height: 350px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);" />
                    </div>
                `;
                modal.classList.add("active");
                
                const handleClose = () => {
                    modal.classList.remove("active");
                    contentSpan.innerHTML = "";
                    closeBtn.removeEventListener("click", handleClose);
                    
                    chatObj.messages.splice(msgIndex, 1);
                    persistChats();
                    renderMessages();
                };
                closeBtn.addEventListener("click", handleClose);
            } else {
                const blob = new Blob([decryptedBuffer], { type: "application/octet-stream" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = m.original_name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                
                const modal = document.getElementById("view-once-modal");
                const contentSpan = document.getElementById("view-once-modal-content");
                const closeBtn = document.getElementById("view-once-modal-close-btn");
                
                contentSpan.innerHTML = `
                    <div style="text-align: center; color: #ffffff;">
                        <span style="font-size: 24px; display: block; margin-bottom: 8px;">📥</span>
                        <span style="font-weight: 600; font-size: 14px; display: block; margin-bottom: 4px;">File Downloaded</span>
                        <span style="font-size: 12px; color: var(--text-muted);">${escapeHtml(m.original_name)}</span>
                    </div>
                `;
                modal.classList.add("active");
                
                const handleClose = () => {
                    modal.classList.remove("active");
                    contentSpan.innerHTML = "";
                    closeBtn.removeEventListener("click", handleClose);
                    
                    chatObj.messages.splice(msgIndex, 1);
                    persistChats();
                    renderMessages();
                };
                closeBtn.addEventListener("click", handleClose);
            }
        } else {
            if (m.file_type === "image") {
                const b64 = arrayBufferToBase64(decryptedBuffer);
                const ext = m.original_name.split('.').pop() || "png";
                const dataUrl = `data:image/${ext};base64,${b64}`;
                
                m.decrypted_data_url = dataUrl;
                m.status_label = "Decrypted";
            } else {
                const blob = new Blob([decryptedBuffer], { type: "application/octet-stream" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = m.original_name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                
                m.status_label = "Downloaded";
            }
            m.opened = true;
            persistChats();
            renderMessages();
        }
    } catch (err) {
        console.error(err);
        if (fileStatus) fileStatus.innerText = "Error: " + err.message;
        if (fileAction) {
            if (m.view_once) {
                fileAction.innerHTML = `
                    <button class="action-icon-btn" onclick="downloadAndDecryptFile('${partner}', ${msgIndex})" style="color: #ef4444;" title="Retry">
                        👁️
                    </button>`;
            } else {
                fileAction.innerHTML = `
                    <button class="action-icon-btn" onclick="downloadAndDecryptFile('${partner}', ${msgIndex})" style="color: var(--accent-hover);" title="Retry">
                        📥
                    </button>`;
            }
        }
    }
}

// Send message (Handles staged file upload + text sending)
export async function sendMessage() {
    const text = messageInput.value.trim();
    const staged = state.staged_file;
    if (!text && !staged) return;
    if (!state.recipient) {
        alert("Lütfen önce bir sohbet seçin!");
        return;
    }
    
    messageInput.value = "";
    const isViewOnce = state.viewOnceNext;
    
    // Turn off view-once indicator if active
    if (isViewOnce) {
        state.viewOnceNext = false;
        viewOnceBtn.classList.remove("view-once-active");
        viewOnceBtn.style.color = "";
        const viewOnceBadge = document.getElementById("view-once-active-dot");
        if (viewOnceBadge) viewOnceBadge.classList.add("hidden");
    }

    // If a file is staged, upload & send it
    if (staged) {
        state.staged_file = null;
        stagedFileContainer.classList.add("hidden");
        fileInput.value = "";
        
        uploadProgressContainer.classList.remove("hidden");
        uploadStatusText.innerText = "Dosya okunuyor...";
        uploadProgressBar.style.width = "10%";
        uploadPercentageText.innerText = "10%";

        const fileReader = new FileReader();
        fileReader.onload = async function() {
            try {
                const arrayBuffer = fileReader.result;
                
                uploadStatusText.innerText = "Dosya şifreleniyor (E2EE)...";
                uploadProgressBar.style.width = "30%";
                uploadPercentageText.innerText = "30%";
                
                const encryptedB64 = await encryptBytesJS(arrayBuffer, state.recipientPubKey);
                
                uploadStatusText.innerText = "Sunucuya yükleniyor...";
                uploadProgressBar.style.width = "60%";
                uploadPercentageText.innerText = "60%";
                
                const path = "/api/upload_file";
                const fileType = guessFileType(staged.name);
                
                const uploadPayload = {
                    sender: state.username,
                    recipient: state.recipient,
                    encrypted_data: encryptedB64,
                    original_name: staged.name,
                    file_type: fileType
                };
                
                const bodyText = JSON.stringify(uploadPayload);
                const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", path, bodyText);
                headers["Content-Type"] = "application/json";
                
                const resp = await fetch(`${API_URL}${path}`, {
                    method: "POST",
                    headers: headers,
                    body: bodyText
                });
                
                if (resp.status !== 200) {
                    const errText = await resp.text();
                    throw new Error("Yükleme başarısız: " + errText);
                }
                
                const uploadData = await resp.json();
                const fileUuid = uploadData.uuid;
                
                uploadProgressBar.style.width = "90%";
                uploadPercentageText.innerText = "90%";
                
                const timestamp = new Date().toISOString();
                
                const msgObj = {
                    sender: state.username,
                    is_file: true,
                    file_uuid: fileUuid,
                    original_name: staged.name,
                    file_type: fileType,
                    view_once: isViewOnce,
                    timestamp: timestamp,
                    read: true
                };
                
                await saveChatToLocalStorage(state.recipient, msgObj);
                renderMessages();
                renderInbox();
                
                const fileMsgPayload = {
                    type: "file_message",
                    sender: state.username,
                    recipient: state.recipient,
                    file_uuid: fileUuid,
                    original_name: staged.name,
                    file_type: fileType,
                    view_once: isViewOnce,
                    timestamp: timestamp
                };
                
                if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                    state.ws.send(JSON.stringify(fileMsgPayload));
                } else {
                    const fallbackPath = "/api/send_ws_fallback";
                    const fallbackBody = JSON.stringify({ payload: JSON.stringify(fileMsgPayload) });
                    const fallbackHeaders = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", fallbackPath, fallbackBody);
                    fallbackHeaders["Content-Type"] = "application/json";
                    await fetch(`${API_URL}${fallbackPath}`, {
                        method: "POST",
                        headers: fallbackHeaders,
                        body: fallbackBody
                    });
                }
                
                uploadProgressBar.style.width = "100%";
                uploadPercentageText.innerText = "100%";
                uploadStatusText.innerText = "Tamamlandı!";
                setTimeout(() => {
                    uploadProgressContainer.classList.add("hidden");
                    uploadProgressBar.style.width = "0%";
                }, 1000);
                
            } catch (err) {
                alert("Dosya gönderilemedi: " + err.message);
                uploadProgressContainer.classList.add("hidden");
            }
        };
        fileReader.onerror = function() {
            alert("Dosya okuma hatası.");
            uploadProgressContainer.classList.add("hidden");
        };
        fileReader.readAsArrayBuffer(staged);
    }

    // If there is text message, send it
    if (text) {
        try {
            let encryptedPayload, textMsgPayload;
            const timestamp = new Date().toISOString();
            
            if (state.isGroup) {
                const groupKeyHex = await dbGet("group_keys", state.recipient);
                if (!groupKeyHex) {
                    alert("Bu grubun şifreleme anahtarı yerel depoda bulunamadı!");
                    return;
                }
                encryptedPayload = await encryptSymmetricJS(text, groupKeyHex);
                
                // Sign the message
                const dataToSign = new TextEncoder().encode(state.username + ":" + state.recipient + ":" + encryptedPayload);
                const signature = await signDataJS(state.privateKeyPem, dataToSign);
                
                textMsgPayload = {
                    "type": "group_message",
                    "sender": state.username,
                    "group_id": state.recipient,
                    "encrypted_payload": encryptedPayload,
                    "signature": signature,
                    "timestamp": timestamp
                };
            } else {
                encryptedPayload = await encryptMessageJS(text, state.recipientPubKey);
                textMsgPayload = {
                    "type": "message",
                    "sender": state.username,
                    "recipient": state.recipient,
                    "encrypted_payload": encryptedPayload,
                    "view_once": isViewOnce,
                    "timestamp": timestamp
                };
            }
            
            const msgObj = {
                sender: state.username,
                content: text,
                timestamp: timestamp,
                view_once: isViewOnce,
                read: true
            };
            
            await saveChatToLocalStorage(state.recipient, msgObj);
            renderMessages();
            renderInbox();
            
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify(textMsgPayload));
            } else {
                const path = "/api/send_ws_fallback";
                const bodyText = JSON.stringify({ payload: JSON.stringify(textMsgPayload) });
                const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "POST", path, bodyText);
                headers["Content-Type"] = "application/json";
                await fetch(`${API_URL}${path}`, {
                    method: "POST",
                    headers: headers,
                    body: bodyText
                });
            }
        } catch (err) {
            console.error("Mesaj gönderilemedi:", err);
            alert("Mesaj gönderilemedi: " + err);
        }
    }
}

// Global exposure for onclick handlers in HTML template
window.downloadAndDecryptFile = downloadAndDecryptFile;
window.openViewOnceMessage = openViewOnceMessage;

// Listeners to custom events from ws.js or db.js
window.addEventListener('chats-updated', () => renderInbox());
window.addEventListener('messages-updated', () => renderMessages());
window.addEventListener('presence-updated', (e) => setPresenceUI(e.detail.partner, e.detail.online));
window.addEventListener('system-message', (e) => appendSystemMessage(e.detail.partner, e.detail.text));
window.addEventListener('ephemeral-status-synced', (e) => {
    if (state.recipient === e.detail.partner) {
        state.ephemeral = e.detail.ephemeral;
        if (ephemeralBtn) {
            ephemeralBtn.classList.toggle("ephemeral-active", state.ephemeral);
            ephemeralBtn.title = state.ephemeral ? "Ephemeral Mode ON (chat history not saved)" : "Ephemeral Mode (Don't save chat history)";
        }
    }
});
