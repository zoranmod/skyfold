"use strict";
const b = globalThis.browser ?? globalThis.chrome;
const status = document.getElementById("status");
const btn = document.getElementById("sync");

function when(iso) {
  if (!iso) return "never";
  try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
}

function show(st) {
  status.textContent = "";
  if (st.error) {
    const p = document.createElement("div");
    p.className = "err";
    p.textContent = st.error;
    status.append(p);
    return;
  }
  const line = (text) => { const d = document.createElement("div"); d.textContent = text; status.append(d); };
  line(st.count != null ? `${st.count} bookmarks` : "Not updated yet");
  line(`Read from iCloud: ${when(st.updated)}`);
  line(`Checked: ${when(st.checked)}`);
}

b.storage.local.get(["count", "updated", "checked", "error"]).then(show);

btn.addEventListener("click", async () => {
  btn.disabled = true;
  btn.textContent = "Updating…";
  const res = await b.runtime.sendMessage({ cmd: "sync-now" });
  show(res && res.ok ? res : { error: (res && res.error) || "Couldn't update." });
  btn.disabled = false;
  btn.textContent = "Update now";
});
