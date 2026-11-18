/* Skyfold – Safari Bookmarks (Firefox, Chrome, Edge).
 *
 * Keeps one folder, "Safari Bookmarks", the same as your Safari bookmarks in iCloud. The bookmarks come
 * from the Skyfold app on this PC (native messaging host "com.skyfold.bookmarks"), which reads them from
 * iCloud. One-way: the folder is rebuilt from Safari whenever Safari's bookmarks change, so edits made
 * inside it are replaced. Nothing outside that folder is ever touched, and nothing goes back to iCloud.
 */
"use strict";

const HOST = "com.skyfold.bookmarks";
const FOLDER = "Safari Bookmarks";
const EVERY_MINUTES = 30;

function api() {
  return globalThis.browser ?? globalThis.chrome;
}

function hashOf(tree) {
  // FNV-1a over the JSON: enough to tell "changed" from "the same"
  const s = JSON.stringify(tree);
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16) + ":" + s.length;
}

function count(tree) {
  let n = 0;
  for (const node of tree) n += node.c ? count(node.c) : 1;
  return n;
}

function safeUrl(u) {
  return typeof u === "string" && /^(https?|ftp|file):|^mailto:/i.test(u);
}

async function otherBookmarksId(b) {
  const [root] = await b.bookmarks.getTree();
  const kids = root.children || [];
  const other = kids.find((k) => k.id === "unfiled_____" || k.id === "2");  // Firefox / Chrome+Edge
  return (other || kids[kids.length - 1] || root).id;
}

async function ourFolder(b, savedId) {
  if (savedId) {
    try {
      const [node] = await b.bookmarks.get(savedId);
      if (node && !node.url) return node;
    } catch (e) { /* removed by the user: made again below */ }
  }
  return b.bookmarks.create({ parentId: await otherBookmarksId(b), title: FOLDER });
}

async function addAll(b, parentId, nodes) {
  for (const n of nodes) {
    if (Array.isArray(n.c)) {
      const f = await b.bookmarks.create({ parentId, title: String(n.t || "Untitled") });
      await addAll(b, f.id, n.c);
    } else if (safeUrl(n.u)) {
      await b.bookmarks.create({ parentId, title: String(n.t || n.u), url: n.u });
    }
  }
}

const MESSAGES = {
  "no-bookmarks": "Skyfold hasn't read your Safari bookmarks yet. In Skyfold: Settings → Notes → Safari bookmarks, turn it on, then Sync now.",
  "no-host": "Skyfold isn't set up for this browser yet. Install Skyfold on this PC, turn on Safari bookmarks (Settings → Notes) and let it sync once.",
};

function explain(err) {
  const text = String((err && err.message) || err || "");
  if (/no-bookmarks/.test(text)) return MESSAGES["no-bookmarks"];
  if (/native|host|not found|disconnected|No such/i.test(text)) return MESSAGES["no-host"];
  return "Couldn't update: " + text.slice(0, 120);
}

let running = null;

async function syncOnce(b = api()) {
  if (running) return running;  // one at a time
  running = (async () => {
    const res = await b.runtime.sendNativeMessage(HOST, { cmd: "bookmarks" });
    if (!res || !res.ok) throw new Error((res && res.error) || "no answer from Skyfold");
    const tree = Array.isArray(res.tree) ? res.tree : [];
    const hash = hashOf(tree);
    const st = await b.storage.local.get(["folderId", "hash"]);
    const folder = await ourFolder(b, st.folderId);
    let changed = false;
    if (folder.id !== st.folderId || st.hash !== hash) {
      for (const k of await b.bookmarks.getChildren(folder.id)) {  // only inside our folder
        if (k.url) await b.bookmarks.remove(k.id);
        else await b.bookmarks.removeTree(k.id);
      }
      await addAll(b, folder.id, tree);
      changed = true;
    }
    const status = { folderId: folder.id, hash, updated: res.updated || null, count: count(tree),
                     checked: new Date().toISOString(), error: null };
    await b.storage.local.set(status);
    return { changed, ...status };
  })();
  try {
    return await running;
  } catch (e) {
    await b.storage.local.set({ error: explain(e), checked: new Date().toISOString() });
    throw e;
  } finally {
    running = null;
  }
}

function start() {
  const b = api();
  b.alarms.create("skyfold-sync", { periodInMinutes: EVERY_MINUTES });
  syncOnce(b).catch(() => {});
}

if (typeof module !== "undefined") {
  module.exports = { syncOnce, hashOf, count, explain, FOLDER };  // for the tests (Node)
} else {
  const b = api();
  b.runtime.onInstalled.addListener(start);
  b.runtime.onStartup.addListener(start);
  b.alarms.onAlarm.addListener((a) => { if (a.name === "skyfold-sync") syncOnce(b).catch(() => {}); });
  b.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg && msg.cmd === "sync-now") {
      syncOnce(b).then((r) => reply({ ok: true, ...r }), (e) => reply({ ok: false, error: explain(e) }));
      return true;  // the answer comes later
    }
    return false;
  });
}
