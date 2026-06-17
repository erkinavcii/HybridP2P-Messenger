// static/js/voip.js

import { state, API_URL } from './state.js';

/** Fetch ICE server config from server (Google STUN + optional self-hosted TURN) */
export async function fetchIceServers() {
    if (state.voip.iceServers) return state.voip.iceServers;
    try {
        const res = await fetch(`${API_URL}/api/ice_servers`);
        const json = await res.json();
        state.voip.iceServers = json.ice_servers;
    } catch (e) {
        // Fallback to Google STUN if server unreachable
        state.voip.iceServers = [
            { urls: "stun:stun.l.google.com:19302" },
            { urls: "stun:stun1.l.google.com:19302" },
        ];
    }
    return state.voip.iceServers;
}

/** Build a fresh RTCPeerConnection wired to WebSocket signaling */
export async function createPeerConnection(partnerUsername, callId) {
    const iceServers = await fetchIceServers();
    const pc = new RTCPeerConnection({ iceServers });

    // Send each ICE candidate via WebSocket as it is discovered
    pc.onicecandidate = (event) => {
        if (event.candidate && state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({
                type: "ice_candidate",
                recipient: partnerUsername,
                call_id: callId,
                candidate: event.candidate.candidate,
                sdp_mid: event.candidate.sdpMid,
                sdp_mline_index: event.candidate.sdpMLineIndex,
            }));
        }
    };

    // Monitor P2P connection quality
    pc.oniceconnectionstatechange = () => {
        const el = document.getElementById("conn-quality");
        const statusEl = document.getElementById("ac-status");
        const states = {
            checking:     "🔄 Establishing P2P connection...",
            connected:    "🟢 P2P Connected — Encrypted",
            completed:    "🟢 P2P Connected — Encrypted",
            disconnected: "🟡 Connection unstable...",
            failed:       "🔴 Connection failed",
            closed:       "Connection closed",
        };
        if (el) el.textContent = states[pc.iceConnectionState] || pc.iceConnectionState;
        if (statusEl && (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed")) {
            statusEl.textContent = "🔐 DTLS-SRTP Encrypted";
            // Start call timer when P2P is actually connected
            if (!state.voip.timerInterval) startCallTimer();
        }
        if (pc.iceConnectionState === "failed") {
            cleanupCall();
        }
    };

    // When remote audio/video arrives, attach to elements
    pc.ontrack = (event) => {
        const [remoteStream] = event.streams;
        if (event.track.kind === "audio") {
            const audio = document.getElementById("remote-audio");
            if (audio) audio.srcObject = remoteStream;
        } else if (event.track.kind === "video") {
            const video = document.getElementById("remote-video");
            if (video) { video.srcObject = remoteStream; video.style.display = "block"; }
        }
    };

    // Drain buffered ICE candidates (Trickle ICE)
    const pending = state.voip.pendingIce.splice(0);
    for (const c of pending) {
        try { await pc.addIceCandidate(new RTCIceCandidate(c)); } catch (_) {}
    }

    return pc;
}

/** Initiate an outgoing call */
export async function startCall(callType) {
    if (!state.recipient || state.isGroup) {
        alert("Please select an individual chat first.");
        return;
    }
    if (state.voip.peerConnection) {
        alert("You are already in a call.");
        return;
    }

    const callId = crypto.randomUUID();
    state.voip.callId   = callId;
    state.voip.callType = callType;
    state.voip.partner  = state.recipient;
    state.voip.isCaller = true;

    // Show active call screen immediately ("Calling...")
    showActiveCallScreen(state.recipient, callType, false);

    try {
        // Request microphone (+ camera for video calls)
        const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: callType === "video" };
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        state.voip.localStream = stream;

        // Show local preview for video calls
        if (callType === "video") {
            const localVid = document.getElementById("local-video");
            if (localVid) { localVid.srcObject = stream; localVid.style.display = "block"; }
            document.getElementById("cam-btn").classList.remove("hidden");
        }

        const pc = await createPeerConnection(state.recipient, callId);
        state.voip.peerConnection = pc;

        // Add local tracks to PeerConnection
        stream.getTracks().forEach(track => pc.addTrack(track, stream));

        // Create SDP offer
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        // Send offer via WebSocket
        state.ws.send(JSON.stringify({
            type: "call_offer",
            recipient: state.recipient,
            call_id: callId,
            call_type: callType,
            sdp_offer: offer.sdp,
            timestamp: new Date().toISOString(),
        }));

        document.getElementById("ac-status").textContent = "🔔 Calling...";

    } catch (err) {
        console.error("[VoIP] startCall error:", err);
        cleanupCall();
        alert("Could not access microphone/camera: " + err.message);
    }
}

/** Handle an incoming call_offer from the server */
export async function handleIncomingCallOffer(data) {
    const { caller, call_id, call_type, sdp_offer } = data;
    // If already in a call, send busy automatically
    if (state.voip.peerConnection) {
        state.ws.send(JSON.stringify({ type: "call_reject", recipient: caller, call_id, reason: "busy" }));
        return;
    }
    state.voip.callId   = call_id;
    state.voip.callType = call_type;
    state.voip.partner  = caller;
    state.voip.isCaller = false;
    // Store offer SDP for when user accepts
    state.voip._pendingSdpOffer = sdp_offer;

    // Show incoming call UI
    const overlay = document.getElementById("incoming-call-overlay");
    document.getElementById("ic-avatar").textContent = caller.charAt(0).toUpperCase();
    document.getElementById("ic-name").textContent = caller;
    document.getElementById("ic-type").textContent = call_type === "video" ? "📹 Incoming Video Call" : "📞 Incoming Audio Call";
    overlay.classList.add("visible");

    // Play ringtone (simple beep via Web Audio API)
    try { playRingtone(); } catch (_) {}
}

/** Called when user presses Accept on the incoming call overlay */
export async function acceptCall() {
    const overlay = document.getElementById("incoming-call-overlay");
    overlay.classList.remove("visible");
    stopRingtone();

    const { callId, callType, partner, _pendingSdpOffer } = state.voip;
    showActiveCallScreen(partner, callType, false);

    try {
        const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: callType === "video" };
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        state.voip.localStream = stream;

        if (callType === "video") {
            const localVid = document.getElementById("local-video");
            if (localVid) { localVid.srcObject = stream; localVid.style.display = "block"; }
            document.getElementById("cam-btn").classList.remove("hidden");
        }

        const pc = await createPeerConnection(partner, callId);
        state.voip.peerConnection = pc;
        stream.getTracks().forEach(track => pc.addTrack(track, stream));

        // Set remote description (caller's SDP offer)
        await pc.setRemoteDescription(new RTCSessionDescription({ type: "offer", sdp: _pendingSdpOffer }));

        // Create and set local answer
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);

        // Send answer back via WebSocket
        state.ws.send(JSON.stringify({
            type: "call_answer",
            recipient: partner,
            call_id: callId,
            sdp_answer: answer.sdp,
            timestamp: new Date().toISOString(),
        }));

        document.getElementById("ac-status").textContent = "🔄 Connecting...";

    } catch (err) {
        console.error("[VoIP] acceptCall error:", err);
        cleanupCall();
        alert("Could not access microphone/camera: " + err.message);
    }
}

/** Called when user presses Reject on the incoming call overlay */
export function rejectCall() {
    const overlay = document.getElementById("incoming-call-overlay");
    overlay.classList.remove("visible");
    stopRingtone();

    const { callId, partner } = state.voip;
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
            type: "call_reject",
            recipient: partner,
            call_id: callId,
            reason: "rejected",
            timestamp: new Date().toISOString(),
        }));
    }
    resetVoipState();
}

/** Handle server relaying the callee's SDP answer back to us (caller) */
export async function handleCallAnswer(data) {
    const { call_id, sdp_answer } = data;
    if (!state.voip.peerConnection || state.voip.callId !== call_id) return;
    try {
        await state.voip.peerConnection.setRemoteDescription(
            new RTCSessionDescription({ type: "answer", sdp: sdp_answer })
        );
        // Drain any buffered ICE candidates
        const pending = state.voip.pendingIce.splice(0);
        for (const c of pending) {
            try { await state.voip.peerConnection.addIceCandidate(new RTCIceCandidate(c)); } catch (_) {}
        }
    } catch (err) {
        console.error("[VoIP] handleCallAnswer error:", err);
    }
}

/** Handle a call_reject (busy, unavailable, timeout, rejected) */
export function handleCallRejected(data) {
    const reasons = {
        rejected:    "❌ Call was declined.",
        busy:        "📵 User is busy in another call.",
        timeout:     "⏰ No answer.",
        unavailable: "🔴 User is offline.",
        busy_caller: "⚠️ You are already in a call.",
    };
    const msg = reasons[data.reason] || "Call failed.";

    // If incoming overlay is still open, close it
    document.getElementById("incoming-call-overlay").classList.remove("visible");
    stopRingtone();
    cleanupCall();
    // Show brief notification in the chat status area
    const statusEl = document.getElementById("status-label");
    if (statusEl) { const prev = statusEl.textContent; statusEl.textContent = msg; setTimeout(() => statusEl.textContent = prev, 4000); }
}

/** Handle the remote party ending the call */
export function handleCallEnded(data) {
    cleanupCall();
}

/** Handle a relayed ICE candidate from the remote peer */
export async function handleIceCandidate(data) {
    const { candidate, sdp_mid, sdp_mline_index, call_id } = data;
    if (!candidate) return;
    const iceCandidate = new RTCIceCandidate({ candidate, sdpMid: sdp_mid, sdpMLineIndex: sdp_mline_index });
    const pc = state.voip.peerConnection;
    if (pc && pc.remoteDescription) {
        try { await pc.addIceCandidate(iceCandidate); } catch (_) {}
    } else {
        // Buffer until remote description is set
        state.voip.pendingIce.push({ candidate, sdpMid: sdp_mid, sdpMLineIndex: sdp_mline_index });
    }
}

/** End an active call (local action) */
export function endCall() {
    const { callId, partner } = state.voip;
    const duration = state.voip.startTime ? Math.floor((Date.now() - state.voip.startTime) / 1000) : 0;
    if (state.ws && state.ws.readyState === WebSocket.OPEN && partner && callId) {
        state.ws.send(JSON.stringify({
            type: "call_end",
            recipient: partner,
            call_id: callId,
            duration_seconds: duration,
            timestamp: new Date().toISOString(),
        }));
    }
    cleanupCall();
}

/** Show the active call screen UI */
export function showActiveCallScreen(partnerName, callType, connected) {
    const screen = document.getElementById("active-call-screen");
    document.getElementById("ac-avatar").textContent = partnerName.charAt(0).toUpperCase();
    document.getElementById("ac-name").textContent = partnerName;
    document.getElementById("ac-status").textContent = connected ? "🔐 DTLS-SRTP Encrypted" : "Connecting...";
    document.getElementById("call-timer").textContent = "00:00";
    document.getElementById("conn-quality").textContent = "🔄 Establishing P2P connection...";
    screen.classList.add("visible");
}

/** Start the call duration timer */
export function startCallTimer() {
    state.voip.startTime = Date.now();
    const timerEl = document.getElementById("call-timer");
    state.voip.timerInterval = setInterval(() => {
        const sec = Math.floor((Date.now() - state.voip.startTime) / 1000);
        const m = String(Math.floor(sec / 60)).padStart(2, "0");
        const s = String(sec % 60).padStart(2, "0");
        if (timerEl) timerEl.textContent = `${m}:${s}`;
    }, 1000);
}

/** Stop all media, close PeerConnection, hide UI */
export function cleanupCall() {
    if (state.voip.timerInterval) { clearInterval(state.voip.timerInterval); }
    if (state.voip.localStream) {
        state.voip.localStream.getTracks().forEach(t => t.stop());
    }
    if (state.voip.peerConnection) {
        state.voip.peerConnection.close();
    }
    const localVid  = document.getElementById("local-video");
    const remoteVid = document.getElementById("remote-video");
    const remoteAud = document.getElementById("remote-audio");
    if (localVid)  { localVid.srcObject  = null; localVid.style.display  = "none"; }
    if (remoteVid) { remoteVid.srcObject = null; remoteVid.style.display = "none"; }
    if (remoteAud) { remoteAud.srcObject = null; }

    document.getElementById("active-call-screen").classList.remove("visible");
    document.getElementById("incoming-call-overlay").classList.remove("visible");
    document.getElementById("cam-btn").classList.add("hidden");
    document.getElementById("mute-btn").classList.remove("active");

    resetVoipState();
}

/** Reset voip fields in state */
export function resetVoipState() {
    state.voip.peerConnection   = null;
    state.voip.localStream      = null;
    state.voip.callId           = null;
    state.voip.callType         = null;
    state.voip.partner          = null;
    state.voip.isCaller         = false;
    state.voip.timerInterval    = null;
    state.voip.startTime        = null;
    state.voip.muted            = false;
    state.voip.camOff           = false;
    state.voip.pendingIce       = [];
    state.voip._pendingSdpOffer = null;
}

// ── Simple Web Audio ringtone (no file dependency) ──
let _ringtoneInterval = null;
export function playRingtone() {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    function beep() {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = "sine"; osc.frequency.value = 520;
        gain.gain.setValueAtTime(0.25, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
        osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.45);
    }
    beep();
    _ringtoneInterval = setInterval(beep, 1500);
}
export function stopRingtone() {
    if (_ringtoneInterval) { clearInterval(_ringtoneInterval); _ringtoneInterval = null; }
}

// ── Pure P2P (Serverless) SDP Compression & Call negotiation ──
export async function compressStringJS(str) {
    const stream = new Response(str).body.pipeThrough(new CompressionStream("deflate"));
    const buffer = await new Response(stream).arrayBuffer();
    return btoa(String.fromCharCode(...new Uint8Array(buffer)));
}

export async function decompressStringJS(base64Str) {
    const binary = atob(base64Str);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    const stream = new Response(bytes).body.pipeThrough(new DecompressionStream("deflate"));
    return await new Response(stream).text();
}

export async function packSDPJS(sdpStr, sdpType, callType = "audio", compress = true) {
    const data = {
        sdp: sdpStr,
        type: sdpType,
        call_type: callType
    };
    const jsonStr = JSON.stringify(data);
    if (compress && typeof CompressionStream !== 'undefined') {
        try {
            const b64 = await compressStringJS(jsonStr);
            return `z1:${b64}`;
        } catch (e) {
            console.warn("Compression failed, falling back to uncompressed", e);
        }
    }
    const b64 = btoa(unescape(encodeURIComponent(jsonStr)));
    return `v1:${b64}`;
}

export async function unpackSDPJS(packedStr) {
    packedStr = packedStr.trim();
    if (packedStr.startsWith("z1:")) {
        const b64 = packedStr.substring(3);
        const jsonStr = await decompressStringJS(b64);
        return JSON.parse(jsonStr);
    } else if (packedStr.startsWith("v1:")) {
        const b64 = packedStr.substring(3);
        const jsonStr = decodeURIComponent(escape(atob(b64)));
        return JSON.parse(jsonStr);
    } else {
        try {
            const decoded = atob(packedStr);
            if (decoded.charCodeAt(0) === 0x78) {
                const jsonStr = await decompressStringJS(packedStr);
                return JSON.parse(jsonStr);
            }
            return JSON.parse(decoded);
        } catch (e) {
            throw new Error("Invalid packed SDP format: " + e.message);
        }
    }
}

// Wire up VoIP listeners when the module is loaded
export function initVoipEvents() {
    const audioCallBtn = document.getElementById("audio-call-btn");
    const videoCallBtn = document.getElementById("video-call-btn");
    const icAcceptBtn = document.getElementById("ic-accept-btn");
    const icRejectBtn = document.getElementById("ic-reject-btn");
    const endCallBtn = document.getElementById("end-call-btn");
    const muteBtn = document.getElementById("mute-btn");
    const camBtn = document.getElementById("cam-btn");
    
    if (audioCallBtn) audioCallBtn.addEventListener("click", () => startCall("audio"));
    if (videoCallBtn) videoCallBtn.addEventListener("click", () => startCall("video"));
    if (icAcceptBtn) icAcceptBtn.addEventListener("click", acceptCall);
    if (icRejectBtn) icRejectBtn.addEventListener("click", rejectCall);
    if (endCallBtn) endCallBtn.addEventListener("click", endCall);

    if (muteBtn) {
        muteBtn.addEventListener("click", function() {
            if (!state.voip.localStream) return;
            state.voip.muted = !state.voip.muted;
            state.voip.localStream.getAudioTracks().forEach(t => t.enabled = !state.voip.muted);
            this.classList.toggle("active", state.voip.muted);
            this.title = state.voip.muted ? "Unmute" : "Mute";
        });
    }

    if (camBtn) {
        camBtn.addEventListener("click", function() {
            if (!state.voip.localStream) return;
            state.voip.camOff = !state.voip.camOff;
            state.voip.localStream.getVideoTracks().forEach(t => t.enabled = !state.voip.camOff);
            this.classList.toggle("active", state.voip.camOff);
            this.title = state.voip.camOff ? "Turn Camera On" : "Turn Camera Off";
        });
    }

    // P2P UI Management
    const p2pModal = document.getElementById("p2p-modal");
    const pureP2pBtn = document.getElementById("pure-p2p-btn");
    const p2pCloseBtn = document.getElementById("p2p-close-btn");
    const p2pTabCaller = document.getElementById("p2p-tab-caller");
    const p2pTabCallee = document.getElementById("p2p-tab-callee");
    const p2pContentCaller = document.getElementById("p2p-content-caller");
    const p2pContentCallee = document.getElementById("p2p-content-callee");

    if (pureP2pBtn) {
        pureP2pBtn.addEventListener("click", () => {
            p2pModal.classList.add("active");
        });
    }

    if (p2pCloseBtn) {
        p2pCloseBtn.addEventListener("click", () => {
            p2pModal.classList.remove("active");
            if (state.voip.peerConnection && state.voip.peerConnection.iceConnectionState !== "connected" && state.voip.peerConnection.iceConnectionState !== "completed") {
                cleanupCall();
            }
        });
    }

    if (p2pTabCaller) {
        p2pTabCaller.addEventListener("click", () => {
            p2pTabCaller.classList.add("active");
            p2pTabCaller.style.color = "#fff";
            p2pTabCaller.style.borderBottomColor = "var(--accent-color)";
            p2pTabCallee.classList.remove("active");
            p2pTabCallee.style.color = "var(--text-muted)";
            p2pTabCallee.style.borderBottomColor = "transparent";
            p2pContentCaller.classList.remove("hidden");
            p2pContentCallee.classList.add("hidden");
        });
    }

    if (p2pTabCallee) {
        p2pTabCallee.addEventListener("click", () => {
            p2pTabCallee.classList.add("active");
            p2pTabCallee.style.color = "#fff";
            p2pTabCallee.style.borderBottomColor = "var(--accent-color)";
            p2pTabCaller.classList.remove("active");
            p2pTabCaller.style.color = "var(--text-muted)";
            p2pTabCaller.style.borderBottomColor = "transparent";
            p2pContentCallee.classList.remove("hidden");
            p2pContentCaller.classList.add("hidden");
        });
    }

    // Caller: Generate Offer
    const p2pGenOfferBtn = document.getElementById("p2p-gen-offer-btn");
    if (p2pGenOfferBtn) {
        p2pGenOfferBtn.addEventListener("click", async () => {
            const btn = document.getElementById("p2p-gen-offer-btn");
            const loading = document.getElementById("p2p-caller-loading");
            const callType = document.getElementById("p2p-call-type").value;

            btn.disabled = true;
            loading.classList.remove("hidden");

            try {
                const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: callType === "video" };
                const stream = await navigator.mediaDevices.getUserMedia(constraints);
                state.voip.localStream = stream;

                if (callType === "video") {
                    const localVid = document.getElementById("local-video");
                    if (localVid) { localVid.srcObject = stream; localVid.style.display = "block"; }
                    document.getElementById("cam-btn").classList.remove("hidden");
                }

                // Create Peer Connection with STUN
                const iceServers = [
                    { urls: "stun:stun.l.google.com:19302" },
                    { urls: "stun:stun1.l.google.com:19302" },
                    { urls: "stun:stun.cloudflare.com:3478" }
                ];
                const pc = new RTCPeerConnection({ iceServers });
                state.voip.peerConnection = pc;
                state.voip.callType = callType;
                state.voip.partner = "Pure P2P Peer";
                state.voip.isCaller = true;

                stream.getTracks().forEach(track => pc.addTrack(track, stream));

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed") {
                        p2pModal.classList.remove("active");
                        showActiveCallScreen("Pure P2P Peer", callType, true);
                        if (!state.voip.timerInterval) startCallTimer();
                    }
                    if (pc.iceConnectionState === "failed") {
                        cleanupCall();
                    }
                };

                pc.ontrack = (event) => {
                    const [remoteStream] = event.streams;
                    if (event.track.kind === "audio") {
                        const audio = document.getElementById("remote-audio");
                        if (audio) audio.srcObject = remoteStream;
                    } else if (event.track.kind === "video") {
                        const video = document.getElementById("remote-video");
                        if (video) { video.srcObject = remoteStream; video.style.display = "block"; }
                    }
                };

                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                // Wait for ICE gathering complete
                await new Promise((resolve) => {
                    if (pc.iceGatheringState === "complete") {
                        resolve();
                        return;
                    }
                    function checkGathering() {
                        if (pc.iceGatheringState === "complete") {
                            pc.removeEventListener("icegatheringstatechange", checkGathering);
                            resolve();
                        }
                    }
                    pc.addEventListener("icegatheringstatechange", checkGathering);
                    pc.onicecandidate = (e) => {
                        if (!e.candidate) {
                            resolve();
                        }
                    };
                });

                const packed = await packSDPJS(pc.localDescription.sdp, "offer", callType);
                document.getElementById("p2p-offer-code").value = packed;
                document.getElementById("p2p-copy-offer-btn").disabled = false;
                document.getElementById("p2p-connect-btn").disabled = false;
                loading.classList.add("hidden");

            } catch (err) {
                console.error("[P2P] Offer Error:", err);
                alert("Offer generation error: " + err.message);
                btn.disabled = false;
                loading.classList.add("hidden");
                cleanupCall();
            }
        });
    }

    const p2pCopyOfferBtn = document.getElementById("p2p-copy-offer-btn");
    if (p2pCopyOfferBtn) {
        p2pCopyOfferBtn.addEventListener("click", () => {
            const el = document.getElementById("p2p-offer-code");
            el.select();
            document.execCommand("copy");
            const btn = document.getElementById("p2p-copy-offer-btn");
            btn.innerText = "Copied!";
            setTimeout(() => { btn.innerText = "Teklifi Kopyala"; }, 2000);
        });
    }

    // Caller: Paste Answer and Connect
    const p2pConnectBtn = document.getElementById("p2p-connect-btn");
    if (p2pConnectBtn) {
        p2pConnectBtn.addEventListener("click", async () => {
            const answerCode = document.getElementById("p2p-caller-answer-code").value;
            if (!answerCode) {
                alert("Please paste the Callee's Answer code first.");
                return;
            }
            try {
                const unpacked = await unpackSDPJS(answerCode);
                const pc = state.voip.peerConnection;
                if (pc) {
                    await pc.setRemoteDescription(new RTCSessionDescription({ type: "answer", sdp: unpacked.sdp }));
                } else {
                    alert("Active Peer Connection not found.");
                }
            } catch (err) {
                alert("Error connecting call: " + err.message);
                cleanupCall();
            }
        });
    }

    // Callee: Generate Answer
    const p2pGenAnswerBtn = document.getElementById("p2p-gen-answer-btn");
    if (p2pGenAnswerBtn) {
        p2pGenAnswerBtn.addEventListener("click", async () => {
            const offerCode = document.getElementById("p2p-callee-offer-code").value;
            if (!offerCode) {
                alert("Please paste the Caller's Offer code first.");
                return;
            }
            const btn = document.getElementById("p2p-gen-answer-btn");
            const loading = document.getElementById("p2p-callee-loading");

            btn.disabled = true;
            loading.classList.remove("hidden");

            try {
                const unpacked = await unpackSDPJS(offerCode);
                const callType = unpacked.call_type || "audio";

                const constraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: callType === "video" };
                const stream = await navigator.mediaDevices.getUserMedia(constraints);
                state.voip.localStream = stream;

                if (callType === "video") {
                    const localVid = document.getElementById("local-video");
                    if (localVid) { localVid.srcObject = stream; localVid.style.display = "block"; }
                    document.getElementById("cam-btn").classList.remove("hidden");
                }

                // Create Peer Connection with STUN
                const iceServers = [
                    { urls: "stun:stun.l.google.com:19302" },
                    { urls: "stun:stun1.l.google.com:19302" },
                    { urls: "stun:stun.cloudflare.com:3478" }
                ];
                const pc = new RTCPeerConnection({ iceServers });
                state.voip.peerConnection = pc;
                state.voip.callType = callType;
                state.voip.partner = "Pure P2P Peer";
                state.voip.isCaller = false;

                stream.getTracks().forEach(track => pc.addTrack(track, stream));

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed") {
                        p2pModal.classList.remove("active");
                        showActiveCallScreen("Pure P2P Peer", callType, true);
                        if (!state.voip.timerInterval) startCallTimer();
                    }
                    if (pc.iceConnectionState === "failed") {
                        cleanupCall();
                    }
                };

                pc.ontrack = (event) => {
                    const [remoteStream] = event.streams;
                    if (event.track.kind === "audio") {
                        const audio = document.getElementById("remote-audio");
                        if (audio) audio.srcObject = remoteStream;
                    } else if (event.track.kind === "video") {
                        const video = document.getElementById("remote-video");
                        if (video) { video.srcObject = remoteStream; video.style.display = "block"; }
                    }
                };

                await pc.setRemoteDescription(new RTCSessionDescription({ type: "offer", sdp: unpacked.sdp }));

                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);

                // Wait for ICE gathering complete
                await new Promise((resolve) => {
                    if (pc.iceGatheringState === "complete") {
                        resolve();
                        return;
                    }
                    function checkGathering() {
                        if (pc.iceGatheringState === "complete") {
                            pc.removeEventListener("icegatheringstatechange", checkGathering);
                            resolve();
                        }
                    }
                    pc.addEventListener("icegatheringstatechange", checkGathering);
                    pc.onicecandidate = (e) => {
                        if (!e.candidate) {
                            resolve();
                        }
                    };
                });

                const packed = await packSDPJS(pc.localDescription.sdp, "answer", callType);
                document.getElementById("p2p-answer-code").value = packed;
                document.getElementById("p2p-copy-answer-btn").disabled = false;
                loading.classList.add("hidden");

            } catch (err) {
                console.error("[P2P] Answer Error:", err);
                alert("Answer generation error: " + err.message);
                btn.disabled = false;
                loading.classList.add("hidden");
                cleanupCall();
            }
        });
    }

    const p2pCopyAnswerBtn = document.getElementById("p2p-copy-answer-btn");
    if (p2pCopyAnswerBtn) {
        p2pCopyAnswerBtn.addEventListener("click", () => {
            const el = document.getElementById("p2p-answer-code");
            el.select();
            document.execCommand("copy");
            const btn = document.getElementById("p2p-copy-answer-btn");
            btn.innerText = "Copied!";
            setTimeout(() => { btn.innerText = "Cevabı Kopyala"; }, 2000);
        });
    }
}
