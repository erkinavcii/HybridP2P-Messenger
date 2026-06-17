// static/js/db.js

import { state, API_URL } from './state.js';
import { makeAuthHeadersJS } from './crypto.js';

const dbName = "hybridp2p_db";
const dbVersion = 2;

export function getDB() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(dbName, dbVersion);
        request.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains("keys")) {
                db.createObjectStore("keys");
            }
            if (!db.objectStoreNames.contains("chats")) {
                db.createObjectStore("chats");
            }
            if (!db.objectStoreNames.contains("group_keys")) {
                db.createObjectStore("group_keys");
            }
        };
        request.onsuccess = (e) => resolve(e.target.result);
        request.onerror = (e) => reject(e.target.error);
    });
}

export async function dbGet(storeName, key) {
    const db = await getDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction(storeName, "readonly");
        const store = transaction.objectStore(storeName);
        const request = store.get(key);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

export async function dbSet(storeName, key, value) {
    const db = await getDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction(storeName, "readwrite");
        const store = transaction.objectStore(storeName);
        const request = store.put(value, key);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
    });
}

export async function dbDel(storeName, key) {
    const db = await getDB();
    return new Promise((resolve, reject) => {
        const transaction = db.transaction(storeName, "readwrite");
        const store = transaction.objectStore(storeName);
        const request = store.delete(key);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
    });
}

export async function persistChats() {
    if (!state.username) return;
    const chatsClone = {};
    for (const [k, v] of Object.entries(state.chats)) {
        chatsClone[k] = {
            partner: v.partner,
            groupName: v.groupName || null,
            isGroup: !!v.isGroup,
            ephemeral: v.ephemeral,
            messages: v.ephemeral ? [] : (v.messages || []).filter(m => !m.view_once)
        };
    }
    await dbSet("chats", `chats_${state.username}`, JSON.stringify(chatsClone));
    window.dispatchEvent(new CustomEvent('chats-updated'));
}

export async function saveChatToLocalStorage(partner, msgObj) {
    if (!state.chats[partner]) {
        state.chats[partner] = {
            partner: partner,
            ephemeral: false,
            messages: []
        };
    }
    if (msgObj) {
        const exists = state.chats[partner].messages.some(m => m.timestamp === msgObj.timestamp && m.content === msgObj.content);
        if (!exists) {
            state.chats[partner].messages.push(msgObj);
        }
    }
    await persistChats();
}

export async function syncChatSettingsFromServer() {
    try {
        const path = `/api/chat_settings/${state.username}`;
        const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
        const res = await fetch(`${API_URL}${path}`, { headers });
        
        if (res.status === 200) {
            const data = await res.json();
            for (let setting of data.settings) {
                const parts = setting.chat_id.split("_");
                const partner = parts[0] === state.username ? parts[1] : parts[0];
                
                if (!state.chats[partner]) {
                    state.chats[partner] = {
                        partner: partner,
                        ephemeral: false,
                        messages: []
                    };
                }
                state.chats[partner].ephemeral = setting.ephemeral;
            }
            await persistChats();
        }
    } catch (err) {
        console.error("Chat settings sync failed:", err);
    }
}

export async function loadChatsFromLocalStorage() {
    const raw = await dbGet("chats", `chats_${state.username}`);
    if (raw) {
        state.chats = JSON.parse(raw);
    } else {
        state.chats = {};
    }
    window.dispatchEvent(new CustomEvent('chats-updated'));
}

export async function getContactPubKey(username) {
    let pubKey = await dbGet("keys", `pubkey_${username}`);
    if (!pubKey) {
        try {
            const path = `/api/public_key/${username}`;
            const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
            const res = await fetch(`${API_URL}${path}`, { headers });
            if (res.status === 200) {
                const data = await res.json();
                pubKey = data.public_key;
                await dbSet("keys", `pubkey_${username}`, pubKey);
            }
        } catch (err) {
            console.error("Error fetching contact pub key:", err);
        }
    }
    return pubKey;
}

export async function fetchGroupName(groupId) {
    try {
        const path = `/api/groups/${state.username}`;
        const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
        const res = await fetch(`${API_URL}${path}`, { headers });
        if (res.status === 200) {
            const data = await res.json();
            const grp = data.groups.find(g => g.group_id === groupId);
            if (grp) return grp.group_name;
        }
    } catch (e) {
        console.error("Error fetching group name:", e);
    }
    return groupId;
}

export async function syncUserGroups() {
    if (!state.username) return;
    try {
        const path = `/api/groups/${state.username}`;
        const headers = await makeAuthHeadersJS(state.username, state.privateKeyPem, "GET", path);
        const res = await fetch(`${API_URL}${path}`, { headers });
        if (res.status === 200) {
            const data = await res.json();
            let changed = false;
            for (let g of data.groups) {
                if (!state.chats[g.group_id]) {
                    state.chats[g.group_id] = {
                        partner: g.group_id,
                        groupName: g.group_name,
                        ephemeral: false,
                        isGroup: true,
                        messages: []
                    };
                    changed = true;
                } else {
                    if (state.chats[g.group_id].groupName !== g.group_name) {
                        state.chats[g.group_id].groupName = g.group_name;
                        changed = true;
                    }
                }
            }
            if (changed) {
                await persistChats();
            }
        }
    } catch (e) {
        console.error("Error syncing groups:", e);
    }
}
