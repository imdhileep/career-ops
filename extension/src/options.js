import { DEFAULT_SERVER, getServer, normalizeServer } from "./settings.js";

const $ = (id) => document.getElementById(id);
const origin = `chrome-extension://${chrome.runtime.id}`;
$("cmd").textContent = `cd career-ops/web\nCAREER_OPS_ALLOWED_ORIGINS=${origin} npm start`;

getServer().then((s) => ($("server").value = s));

$("save").addEventListener("click", async () => {
  const value = normalizeServer($("server").value || DEFAULT_SERVER);
  if (!value) {
    $("status").textContent = "Use a local address such as http://127.0.0.1:3000.";
    $("status").className = "status err";
    return;
  }
  await chrome.storage.sync.set({ server: value });
  $("server").value = value;
  try {
    const res = await fetch(`${value}/api/companion`);
    $("status").textContent = res.ok ? "Saved. Connected to your app." : res.status === 403 ? "Saved, but the app refused this extension. Use the command below." : `Saved, but the app answered ${res.status}.`;
    $("status").className = res.ok ? "status" : "status err";
  } catch {
    $("status").textContent = "Saved, but the app isn't running at that address.";
    $("status").className = "status err";
  }
});
