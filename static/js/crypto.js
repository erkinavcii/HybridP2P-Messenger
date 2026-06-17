// static/js/crypto.js

export function arrayBufferToBase64(buffer) {
    let binary = '';
    let bytes = new Uint8Array(buffer);
    let len = bytes.byteLength;
    for (let i = 0; i < len; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary);
}

export function base64ToArrayBuffer(base64) {
    let binary_string = window.atob(base64);
    let len = binary_string.length;
    let bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        bytes[i] = binary_string.charCodeAt(i);
    }
    return bytes.buffer;
}

export function formatPublicKeyPem(base64) {
    let pem = "-----BEGIN PUBLIC KEY-----\n";
    for (let i = 0; i < base64.length; i += 64) {
        pem += base64.substring(i, i + 64) + "\n";
    }
    pem += "-----END PUBLIC KEY-----";
    return pem;
}

export function formatPrivateKeyPem(base64) {
    let pem = "-----BEGIN PRIVATE KEY-----\n";
    for (let i = 0; i < base64.length; i += 64) {
        pem += base64.substring(i, i + 64) + "\n";
    }
    pem += "-----END PRIVATE KEY-----";
    return pem;
}

export async function sha256(text) {
    const encoder = new TextEncoder();
    const data = encoder.encode(text);
    const hashBuffer = await window.crypto.subtle.digest("SHA-256", data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function getFingerprintJS(publicKeyPem) {
    const normalizedPem = publicKeyPem.replace(/\r\n/g, "\n");
    const encoder = new TextEncoder();
    const pemBytes = encoder.encode(normalizedPem);
    const hashBuffer = await window.crypto.subtle.digest("SHA-256", pemBytes);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const sha256Hex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('').toUpperCase();
    const groups = [];
    for (let i = 0; i < sha256Hex.length; i += 4) {
        groups.push(sha256Hex.substring(i, i + 4));
    }
    return groups.join(" ");
}

export async function generateKeyPair() {
    const keyPair = await window.crypto.subtle.generateKey(
        {
            name: "RSA-OAEP",
            modulusLength: 4096,
            publicExponent: new Uint8Array([1, 0, 1]), // 65537
            hash: "SHA-256"
        },
        true,
        ["encrypt", "decrypt"]
    );
    
    // Export private key to pkcs8 PEM
    const privateKeyBuffer = await window.crypto.subtle.exportKey("pkcs8", keyPair.privateKey);
    const privateKeyBase64 = arrayBufferToBase64(privateKeyBuffer);
    const privateKeyPem = formatPrivateKeyPem(privateKeyBase64);
    
    // Export public key to spki PEM
    const publicKeyBuffer = await window.crypto.subtle.exportKey("spki", keyPair.publicKey);
    const publicKeyBase64 = arrayBufferToBase64(publicKeyBuffer);
    const publicKeyPem = formatPublicKeyPem(publicKeyBase64);
    
    return {
        privateKey: privateKeyPem,
        publicKey: publicKeyPem
    };
}

export async function derivePublicKeyFromPrivateKey(privateKeyPem) {
    try {
        const cleanPem = privateKeyPem
            .replace(/-----BEGIN PRIVATE KEY-----/, '')
            .replace(/-----END PRIVATE KEY-----/, '')
            .replace(/\s+/g, '');
        const derBuffer = base64ToArrayBuffer(cleanPem);
        
        // 1. Import private key as RSA-OAEP
        const privateKey = await window.crypto.subtle.importKey(
            "pkcs8",
            derBuffer,
            {
                name: "RSA-OAEP",
                hash: "SHA-256"
            },
            true, // must be extractable
            ["decrypt"]
        );
        
        // 2. Export to JWK
        const jwk = await window.crypto.subtle.exportKey("jwk", privateKey);
        
        // 3. Create public JWK
        const publicJwk = {
            kty: jwk.kty,
            n: jwk.n,
            e: jwk.e,
            ext: true
        };
        
        // 4. Import public JWK
        const publicKey = await window.crypto.subtle.importKey(
            "jwk",
            publicJwk,
            {
                name: "RSA-OAEP",
                hash: "SHA-256"
            },
            true,
            ["encrypt"]
        );
        
        // 5. Export to spki public key
        const publicKeyBuffer = await window.crypto.subtle.exportKey("spki", publicKey);
        const publicKeyBase64 = arrayBufferToBase64(publicKeyBuffer);
        return formatPublicKeyPem(publicKeyBase64);
    } catch (err) {
        console.error("Private key parsing or public key derivation failed:", err);
        throw new Error("Invalid private key format or import error.");
    }
}

export async function importPublicKeyPem(pem) {
    const cleanPem = pem
        .replace(/-----BEGIN PUBLIC KEY-----/, '')
        .replace(/-----END PUBLIC KEY-----/, '')
        .replace(/\s+/g, '');
    const derBuffer = base64ToArrayBuffer(cleanPem);
    return await window.crypto.subtle.importKey(
        "spki",
        derBuffer,
        {
            name: "RSA-OAEP",
            hash: "SHA-256"
        },
        true,
        ["encrypt"]
    );
}

export async function encryptMessageJS(plaintext, recipientPublicKeyPem) {
    // 1. Generate 256-bit AES-GCM Key
    const aesKey = await window.crypto.subtle.generateKey(
        {
            name: "AES-GCM",
            length: 256
        },
        true,
        ["encrypt", "decrypt"]
    );
    
    const aesKeyRaw = await window.crypto.subtle.exportKey("raw", aesKey);
    const iv = window.crypto.getRandomValues(new Uint8Array(12));
    
    // 2. Encrypt plaintext with AES
    const encoder = new TextEncoder();
    const plaintextBytes = encoder.encode(plaintext);
    const ciphertextBuffer = await window.crypto.subtle.encrypt(
        {
            name: "AES-GCM",
            iv: iv
        },
        aesKey,
        plaintextBytes
    );
    
    // 3. Encrypt AES Key with RSA Public Key
    const rsaPublicKey = await importPublicKeyPem(recipientPublicKeyPem);
    const encryptedAesKeyBuffer = await window.crypto.subtle.encrypt(
        {
            name: "RSA-OAEP"
        },
        rsaPublicKey,
        aesKeyRaw
    );
    
    // 4. Base64-encode JSON packet
    const packet = {
        "encrypted_aes_key": arrayBufferToBase64(encryptedAesKeyBuffer),
        "nonce": arrayBufferToBase64(iv),
        "ciphertext": arrayBufferToBase64(ciphertextBuffer)
    };
    
    const packetJson = JSON.stringify(packet);
    const packetJsonBytes = encoder.encode(packetJson);
    return arrayBufferToBase64(packetJsonBytes);
}

export async function decryptMessageJS(encryptedPacketBase64, privateKeyPem) {
    const encoder = new TextDecoder();
    
    // 1. Unpack JSON packet
    const packetJsonBytes = base64ToArrayBuffer(encryptedPacketBase64);
    const packetJson = encoder.decode(packetJsonBytes);
    const packet = JSON.parse(packetJson);
    
    const encryptedAesKey = base64ToArrayBuffer(packet.encrypted_aes_key);
    const nonce = base64ToArrayBuffer(packet.nonce);
    const ciphertext = base64ToArrayBuffer(packet.ciphertext);
    
    // 2. Import Private Key for RSA-OAEP
    const cleanPem = privateKeyPem
        .replace(/-----BEGIN PRIVATE KEY-----/, '')
        .replace(/-----END PRIVATE KEY-----/, '')
        .replace(/\s+/g, '');
    const derBuffer = base64ToArrayBuffer(cleanPem);
    
    const rsaPrivateKey = await window.crypto.subtle.importKey(
        "pkcs8",
        derBuffer,
        {
            name: "RSA-OAEP",
            hash: "SHA-256"
        },
        true,
        ["decrypt"]
    );
    
    // 3. Decrypt AES Key using RSA
    const aesKeyRaw = await window.crypto.subtle.decrypt(
        {
            name: "RSA-OAEP"
        },
        rsaPrivateKey,
        encryptedAesKey
    );
    
    // 4. Import AES Key
    const aesKey = await window.crypto.subtle.importKey(
        "raw",
        aesKeyRaw,
        {
            name: "AES-GCM"
        },
        false,
        ["decrypt"]
    );
    
    // 5. Decrypt message using AES-GCM
    const plaintextBuffer = await window.crypto.subtle.decrypt(
        {
            name: "AES-GCM",
            iv: nonce
        },
        aesKey,
        ciphertext
    );
    
    return encoder.decode(plaintextBuffer);
}

export async function encryptBytesJS(arrayBuffer, recipientPublicKeyPem) {
    // 1. Generate 256-bit AES-GCM Key
    const aesKey = await window.crypto.subtle.generateKey(
        {
            name: "AES-GCM",
            length: 256
        },
        true,
        ["encrypt", "decrypt"]
    );
    
    const aesKeyRaw = await window.crypto.subtle.exportKey("raw", aesKey);
    const iv = window.crypto.getRandomValues(new Uint8Array(12));
    
    // 2. Encrypt bytes with AES
    const ciphertextBuffer = await window.crypto.subtle.encrypt(
        {
            name: "AES-GCM",
            iv: iv
        },
        aesKey,
        arrayBuffer
    );
    
    // 3. Encrypt AES Key with RSA Public Key
    const rsaPublicKey = await importPublicKeyPem(recipientPublicKeyPem);
    const encryptedAesKeyBuffer = await window.crypto.subtle.encrypt(
        {
            name: "RSA-OAEP"
        },
        rsaPublicKey,
        aesKeyRaw
    );
    
    // 4. Base64-encode JSON packet
    const packet = {
        "encrypted_aes_key": arrayBufferToBase64(encryptedAesKeyBuffer),
        "nonce": arrayBufferToBase64(iv),
        "ciphertext": arrayBufferToBase64(ciphertextBuffer)
    };
    
    const packetJson = JSON.stringify(packet);
    const encoder = new TextEncoder();
    const packetJsonBytes = encoder.encode(packetJson);
    return arrayBufferToBase64(packetJsonBytes);
}

export async function decryptBytesJS(encryptedPacketBase64, privateKeyPem) {
    const encoder = new TextDecoder();
    
    // 1. Unpack JSON packet
    const packetJsonBytes = base64ToArrayBuffer(encryptedPacketBase64);
    const packetJson = encoder.decode(packetJsonBytes);
    const packet = JSON.parse(packetJson);
    
    const encryptedAesKey = base64ToArrayBuffer(packet.encrypted_aes_key);
    const nonce = base64ToArrayBuffer(packet.nonce);
    const ciphertext = base64ToArrayBuffer(packet.ciphertext);
    
    // 2. Import Private Key for RSA-OAEP
    const cleanPem = privateKeyPem
        .replace(/-----BEGIN PRIVATE KEY-----/, '')
        .replace(/-----END PRIVATE KEY-----/, '')
        .replace(/\s+/g, '');
    const derBuffer = base64ToArrayBuffer(cleanPem);
    
    const rsaPrivateKey = await window.crypto.subtle.importKey(
        "pkcs8",
        derBuffer,
        {
            name: "RSA-OAEP",
            hash: "SHA-256"
        },
        true,
        ["decrypt"]
    );
    
    // 3. Decrypt AES Key using RSA
    const aesKeyRaw = await window.crypto.subtle.decrypt(
        {
            name: "RSA-OAEP"
        },
        rsaPrivateKey,
        encryptedAesKey
    );
    
    // 4. Import AES Key
    const aesKey = await window.crypto.subtle.importKey(
        "raw",
        aesKeyRaw,
        {
            name: "AES-GCM"
        },
        false,
        ["decrypt"]
    );
    
    // 5. Decrypt message using AES-GCM
    const decryptedBuffer = await window.crypto.subtle.decrypt(
        {
            name: "AES-GCM",
            iv: nonce
        },
        aesKey,
        ciphertext
    );
    
    return decryptedBuffer;
}

export async function signDataJS(privateKeyPem, dataBytes) {
    const cleanPem = privateKeyPem
        .replace(/-----BEGIN PRIVATE KEY-----/, '')
        .replace(/-----END PRIVATE KEY-----/, '')
        .replace(/\s+/g, '');
    const derBuffer = base64ToArrayBuffer(cleanPem);
    
    const key = await window.crypto.subtle.importKey(
        "pkcs8",
        derBuffer,
        {
            name: "RSA-PSS",
            hash: "SHA-256"
        },
        false,
        ["sign"]
    );
    
    const signatureBuffer = await window.crypto.subtle.sign(
        {
            name: "RSA-PSS",
            saltLength: 478 // salt length matches python MAX_LENGTH for RSA-4096
        },
        key,
        dataBytes
    );
    
    return arrayBufferToBase64(signatureBuffer);
}

export async function makeAuthHeadersJS(username, privateKeyPem, method, path, bodyText = "") {
    const timestamp = new Date().toISOString();
    const bodyHash = await sha256(bodyText);
    const dataToSignText = [username, timestamp, method.toUpperCase(), path, bodyHash].join("\n");
    
    const encoder = new TextEncoder();
    const sigB64 = await signDataJS(privateKeyPem, encoder.encode(dataToSignText));
    
    return {
        "X-Username": username,
        "X-Timestamp": timestamp,
        "X-Signature": sigB64
    };
}

export async function signRegistration(username, timestamp, publicKeyPem, privateKeyPem) {
    const dataToSign = `${username}:${timestamp}:${publicKeyPem}`;
    const encoder = new TextEncoder();
    return await signDataJS(privateKeyPem, encoder.encode(dataToSign));
}

export function hexToUint8Array(hexString) {
    if (hexString.length % 2 !== 0) hexString = '0' + hexString;
    const numBytes = hexString.length / 2;
    const array = new Uint8Array(numBytes);
    for (let i = 0; i < numBytes; i++) {
        array[i] = parseInt(hexString.substr(i * 2, 2), 16);
    }
    return array;
}

export function arrayBufferToHex(buffer) {
    const byteArray = new Uint8Array(buffer);
    let hexString = "";
    for (let i = 0; i < byteArray.length; i++) {
        const hex = byteArray[i].toString(16).padStart(2, '0');
        hexString += hex;
    }
    return hexString;
}

export async function generateSymmetricKeyHex() {
    const key = await window.crypto.subtle.generateKey(
        { name: "AES-GCM", length: 256 },
        true,
        ["encrypt", "decrypt"]
    );
    const raw = await window.crypto.subtle.exportKey("raw", key);
    return arrayBufferToHex(raw);
}

export async function encryptSymmetricJS(plaintext, keyHex) {
    const keyBytes = hexToUint8Array(keyHex);
    const aesKey = await window.crypto.subtle.importKey(
        "raw",
        keyBytes,
        { name: "AES-GCM" },
        false,
        ["encrypt"]
    );
    const iv = window.crypto.getRandomValues(new Uint8Array(12));
    const encoder = new TextEncoder();
    const plaintextBytes = encoder.encode(plaintext);
    const ciphertextBuffer = await window.crypto.subtle.encrypt(
        { name: "AES-GCM", iv: iv },
        aesKey,
        plaintextBytes
    );
    const packet = {
        "nonce": arrayBufferToBase64(iv),
        "ciphertext": arrayBufferToBase64(ciphertextBuffer)
    };
    const packetJson = JSON.stringify(packet);
    const packetJsonBytes = encoder.encode(packetJson);
    return arrayBufferToBase64(packetJsonBytes);
}

export async function decryptSymmetricJS(encryptedPacketBase64, keyHex) {
    const keyBytes = hexToUint8Array(keyHex);
    const aesKey = await window.crypto.subtle.importKey(
        "raw",
        keyBytes,
        { name: "AES-GCM" },
        false,
        ["decrypt"]
    );
    const decoder = new TextDecoder();
    const packetJsonBytes = base64ToArrayBuffer(encryptedPacketBase64);
    const packetJson = decoder.decode(packetJsonBytes);
    const packet = JSON.parse(packetJson);
    
    const nonce = base64ToArrayBuffer(packet.nonce);
    const ciphertext = base64ToArrayBuffer(packet.ciphertext);
    
    const decryptedBuffer = await window.crypto.subtle.decrypt(
        { name: "AES-GCM", iv: nonce },
        aesKey,
        ciphertext
    );
    return decoder.decode(decryptedBuffer);
}

export async function verifySignatureJS(publicKeyPem, signatureBase64, dataBytes) {
    const cleanPem = publicKeyPem
        .replace(/-----BEGIN PUBLIC KEY-----/, '')
        .replace(/-----END PUBLIC KEY-----/, '')
        .replace(/\s+/g, '');
    const derBuffer = base64ToArrayBuffer(cleanPem);
    
    const key = await window.crypto.subtle.importKey(
        "spki",
        derBuffer,
        {
            name: "RSA-PSS",
            hash: "SHA-256"
        },
        false,
        ["verify"]
    );
    
    const signatureBytes = base64ToArrayBuffer(signatureBase64);
    
    return await window.crypto.subtle.verify(
        {
            name: "RSA-PSS",
            saltLength: 478
        },
        key,
        signatureBytes,
        dataBytes
    );
}

export async function deriveKeyFromPassword(password, salt) {
    const encoder = new TextEncoder();
    const passwordBytes = encoder.encode(password);
    const baseKey = await window.crypto.subtle.importKey(
        "raw",
        passwordBytes,
        { name: "PBKDF2" },
        false,
        ["deriveBits", "deriveKey"]
    );
    return await window.crypto.subtle.deriveKey(
        {
            name: "PBKDF2",
            salt: salt,
            iterations: 100000,
            hash: "SHA-256"
        },
        baseKey,
        { name: "AES-GCM", length: 256 },
        true,
        ["encrypt", "decrypt"]
    );
}

export async function encryptPrivateKey(privateKeyPem, password) {
    const encoder = new TextEncoder();
    const salt = window.crypto.getRandomValues(new Uint8Array(16));
    const iv = window.crypto.getRandomValues(new Uint8Array(12));
    const aesKey = await deriveKeyFromPassword(password, salt);
    const privateKeyBytes = encoder.encode(privateKeyPem);
    const ciphertext = await window.crypto.subtle.encrypt(
        { name: "AES-GCM", iv: iv },
        aesKey,
        privateKeyBytes
    );
    return arrayBufferToBase64(salt) + ":" + arrayBufferToBase64(iv) + ":" + arrayBufferToBase64(ciphertext);
}

export async function decryptPrivateKey(encryptedDataStr, password) {
    const parts = encryptedDataStr.split(":");
    if (parts.length !== 3) throw new Error("Invalid encrypted key format");
    const salt = base64ToArrayBuffer(parts[0]);
    const iv = base64ToArrayBuffer(parts[1]);
    const ciphertext = base64ToArrayBuffer(parts[2]);
    const aesKey = await deriveKeyFromPassword(password, salt);
    const decryptedBytes = await window.crypto.subtle.decrypt(
        { name: "AES-GCM", iv: iv },
        aesKey,
        ciphertext
    );
    const decoder = new TextDecoder();
    return decoder.decode(decryptedBytes);
}
