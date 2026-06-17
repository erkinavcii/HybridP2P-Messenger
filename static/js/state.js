// static/js/state.js

// Set dynamic API URL based on document origin
export const BASE_HOST = window.location.host; // e.g. "127.0.0.1:8000" or ngrok URL
export const API_URL = `${window.location.protocol}//${BASE_HOST}`;
export const WS_URL = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${BASE_HOST}`;

// Application State
export const state = {
    username: "",
    privateKeyPem: "",
    publicKeyPem: "",
    recipient: null,
    recipientPubKey: null,
    isGroup: false,
    ephemeral: false,
    viewOnceNext: false,
    staged_file: null,
    ws: null,
    chats: {}, // chat_id -> { partner, ephemeral, messages: [] }
    onlineStatus: {}, // username -> online/offline
    // VoIP state
    voip: {
        peerConnection: null,  // RTCPeerConnection
        localStream: null,     // MediaStream (mic + optional cam)
        callId: null,          // UUID of active call
        callType: null,        // "audio" | "video"
        partner: null,         // who we're calling/receiving from
        isCaller: false,       // true if we initiated
        timerInterval: null,   // setInterval handle for call clock
        startTime: null,       // Date.now() when call connected
        muted: false,
        camOff: false,
        pendingIce: [],        // ICE candidates buffered before remote desc set
        iceServers: null,      // cached ICE server config
    }
};
