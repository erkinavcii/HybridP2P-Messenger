// static/js/safe.js saf yardımcılarını Node'da çalıştırır: girdi stdin'den JSON [[fn, arg], ...]
import { escapeHtml, imageMime, jsArg } from "../../static/js/safe.js";
const fns = { escapeHtml, imageMime, jsArg };
const cmds = JSON.parse(await new Promise((r) => { let d = ""; process.stdin.on("data", (c) => d += c).on("end", () => r(d)); }));
process.stdout.write(JSON.stringify(cmds.map(([fn, arg]) => fns[fn](arg))));
