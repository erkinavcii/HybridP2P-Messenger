// tests/node/p2p_harness.mjs — static/serverless.html'in çekirdek betiğini (script#p2p-core)
// Node.js'te çalıştırır; tests/test_serverless_html.py masaüstü (Python) ile karşılıklı
// uyumu bunun üzerinden test eder. Test edilen kod tarayıcıdakiyle aynı metindir.
//
// Kullanım: node p2p_harness.mjs <serverless.html> <crypto.js>  (komut stdin'den JSON)
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [htmlPath, cryptoJsPath] = process.argv.slice(2);
const html = readFileSync(htmlPath, "utf8");
const m = /<script id="p2p-core">([\s\S]*?)<\/script>/.exec(html);
if (!m) throw new Error("p2p-core bloğu bulunamadı");
new Function(m[1])();                       // globalThis.P2PCore tanımlanır
const C = globalThis.P2PCore;

const input = JSON.parse(readFileSync(0, "utf8"));
const b64 = (u8) => Buffer.from(u8).toString("base64");
const out = [];

for (const cmd of input) {
  switch (cmd.op) {
    case "identify": {
      const env = await C.parseCode(cmd.code);
      const id = await C.identifyPeer(env, (u) => (cmd.contacts || {})[u] || null, cmd.expected,
                                      cmd.own_offer_sdp || "", cmd.now ?? Date.now() / 1000);
      out.push({ status: id.status, username: id.username || "", fingerprint: id.fingerprint || "",
                 detail: id.detail, mode: env.mode, sdp: env.sdp });
      break;
    }
    case "make": {
      const ident = await C.importPrivatePkcs8(C.pemToDer(cmd.pkcs8_pem, "PRIVATE KEY"));
      const code = await C.makeEnvelope({ type: cmd.type, mode: cmd.mode, sdp: cmd.sdp, username: cmd.username,
                                          priv: ident.priv, pubDer: ident.pubDer, offerSdp: cmd.offer_sdp || "" });
      out.push({ code, fingerprint: await C.fingerprint(ident.pubDer) });
      break;
    }
    case "fingerprint":
      out.push({ fp: await C.fingerprint(C.pemToDer(cmd.pem, "PUBLIC KEY")) });
      break;
    case "webfp": {                         // web istemcisinin static/js/crypto.js'i
      globalThis.window = globalThis;
      const mod = await import(pathToFileURL(cryptoJsPath).href);
      out.push({ fp: await mod.getFingerprintJS(cmd.pem) });
      break;
    }
    case "parse_frame":
      out.push(cmd.frames.map((f) => C.parseFrame(f)));
      break;
    case "safe_filename":
      out.push(cmd.names.map((n) => C.safeFilename(n)));
      break;
    case "chunk": {
      const frame = C.chunkFrame(cmd.fid, Buffer.from(cmd.data_b64, "base64"));
      const back = C.splitChunk(frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength));
      out.push({ frame_b64: b64(frame), fid: back.fid, data_b64: b64(back.data) });
      break;
    }
    case "split": {
      const raw = Buffer.from(cmd.frame_b64, "base64");
      const r = C.splitChunk(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength));
      out.push({ fid: r.fid, data_b64: b64(r.data) });
      break;
    }
    case "quality": {                       // örnek dizileri → basamaklar (sayfa + quality.js)
      const qmod = await import(pathToFileURL(cmd.quality_js).href);
      const run = (Policy) => cmd.sequences.map((seq) => {
        const p = new Policy();
        return seq.map(([loss, rtt]) => { p.step(loss, rtt); return p.level; });
      });
      const stats = (fn) => cmd.reports.map((r) => fn(new Map(r.map((x, i) => [String(i), x]))));
      out.push({ page: run(C.QualityPolicy), module: run(qmod.QualityPolicy),
                 page_stats: stats(C.statsSample), module_stats: stats(qmod.statsSample),
                 labels: [0, 1, 2, 3].map((l) => [C.qualityLabel(l, true), qmod.qualityLabel(l, true), C.qualityLabel(l, false)]) });
      break;
    }
    case "ice":                             // { preset, custom } → sunucu listesi ya da hata
      try { out.push({ servers: C.iceServersFor(cmd.preset, cmd.custom || "") }); }
      catch (e) { out.push({ error: e instanceof C.IceConfigError ? e.message : "BEKLENMEYEN: " + e }); }
      break;
    default:
      throw new Error("bilinmeyen komut: " + cmd.op);
  }
}
process.stdout.write(JSON.stringify(out));
