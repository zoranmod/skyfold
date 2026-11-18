// Node test of the extension's sync logic against a fake browser bookmarks API (run by
// tests/test_extension.py). Plain asserts, no test framework needed.
"use strict";
const assert = require("assert");
const ext = require("../extension/background.js");

function fakeBrowser(answers) {
  let next = 100;
  const nodes = new Map();
  const add = (n) => { nodes.set(n.id, n); return n; };
  const root = add({ id: "root________", children: [] });
  for (const id of ["menu________", "toolbar_____", "unfiled_____"]) {
    const n = add({ id, parentId: root.id, title: id, children: [] });
    root.children.push(n);
  }
  const mine = add({ id: "own1", parentId: "unfiled_____", title: "My own", url: "https://mine.example/" });
  nodes.get("unfiled_____").children.push(mine);
  const store = {};
  const removeFrom = (n) => { const p = nodes.get(n.parentId); p.children = p.children.filter((c) => c.id !== n.id); };
  const drop = (n) => { (n.children || []).forEach(drop); nodes.delete(n.id); };
  return {
    nodes,
    runtime: { sendNativeMessage: async (host, msg) => {
      assert.strictEqual(host, "com.skyfold.bookmarks");
      assert.deepStrictEqual(msg, { cmd: "bookmarks" });
      return answers.shift();
    } },
    storage: { local: { get: async (keys) => Object.fromEntries(keys.map((k) => [k, store[k]])),
                        set: async (o) => Object.assign(store, o) } },
    bookmarks: {
      getTree: async () => [root],
      get: async (id) => { if (!nodes.has(id)) throw new Error("not found"); return [nodes.get(id)]; },
      getChildren: async (id) => [...(nodes.get(id).children || [])],
      create: async ({ parentId, title, url }) => {
        const n = add({ id: "n" + next++, parentId, title, ...(url ? { url } : { children: [] }) });
        nodes.get(parentId).children.push(n);
        return n;
      },
      remove: async (id) => { const n = nodes.get(id); assert.ok(n.url); removeFrom(n); nodes.delete(id); },
      removeTree: async (id) => { const n = nodes.get(id); removeFrom(n); drop(n); },
    },
  };
}

const tree = [
  { t: "Favorites", c: [{ t: "One", u: "https://one.example/" }, { t: "Work", c: [{ t: "Two", u: "https://two.example/" }] }] },
  { t: "Bad", u: "javascript:alert(1)" },
];

(async () => {
  const b = fakeBrowser([
    { ok: true, updated: "2026-11-18T16:00:00+00:00", tree },
    { ok: true, updated: "2026-11-18T17:00:00+00:00", tree },
    { ok: true, updated: "2026-11-18T18:00:00+00:00", tree: [{ t: "Only", u: "https://only.example/" }] },
    { ok: false, error: "no-bookmarks" },
  ]);
  const first = await ext.syncOnce(b);
  assert.ok(first.changed);
  assert.strictEqual(first.count, 3);  // counted from the tree
  const other = b.nodes.get("unfiled_____");
  const folder = other.children.find((c) => c.title === ext.FOLDER);
  assert.ok(folder, "the Safari Bookmarks folder is under Other Bookmarks");
  assert.deepStrictEqual(folder.children.map((c) => c.title), ["Favorites"]);  // no javascript: link
  assert.deepStrictEqual(folder.children[0].children.map((c) => c.title), ["One", "Work"]);

  const second = await ext.syncOnce(b);  // nothing changed in Safari: nothing touched
  assert.strictEqual(second.changed, false);
  assert.strictEqual(other.children.find((c) => c.title === ext.FOLDER).id, folder.id);

  const third = await ext.syncOnce(b);  // changed: the folder's content is replaced, nothing else
  assert.ok(third.changed);
  assert.deepStrictEqual(folder.children.map((c) => c.title), ["Only"]);
  assert.ok(b.nodes.has("own1"), "the user's own bookmark next to the folder is untouched");

  await assert.rejects(ext.syncOnce(b));
  const st = await b.storage.local.get(["error"]);
  assert.match(st.error, /Settings → Notes/);
  assert.ok(b.nodes.has(folder.id), "a failed update leaves the folder as it was");
  console.log("extension sync: ok");
})().catch((e) => { console.error(e); process.exit(1); });
