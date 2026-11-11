import { getServer } from "./settings.js";

const $ = (id) => document.getElementById(id);
let server = "";
let job = null;
let contact = null;

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").className = isError ? "status err" : "status";
}

async function api(path, init) {
  const res = await fetch(server + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init && init.headers) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return body;
}

function showOffline(err) {
  $("main").hidden = true;
  $("offline").hidden = false;
  const origin = `chrome-extension://${chrome.runtime.id}`;
  $("offline-detail").textContent =
    err && err.status === 403
      ? `Your app is running but refused this extension. Start it with CAREER_OPS_ALLOWED_ORIGINS=${origin}`
      : `Start it with "npm start" in the web folder, then reopen this popup. Looking at ${server}.`;
}

async function activeTab() {
  // ?tab=<id> lets the end-to-end test open the popup as a page aimed at a job tab.
  const forced = Number(new URLSearchParams(location.search).get("tab"));
  if (forced) return chrome.tabs.get(forced);
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function inTab(tabId, func, args = []) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["src/core.js"] });
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return res && res.result;
}

function band(el, value, cls) {
  el.textContent = value;
  el.className = `value ${cls || ""}`;
}

function renderMatch(m) {
  const c = m.coverage;
  if (c && c.pct != null) {
    band($("coverage"), `${c.pct}%`, c.pct >= 75 ? "good" : c.pct >= 50 ? "warn" : "bad");
    if (c.missing.length) {
      $("missing").hidden = false;
      $("missing").innerHTML = "";
      const b = document.createElement("b");
      b.textContent = "Not on your CV: ";
      $("missing").append(b, document.createTextNode(c.missing.slice(0, 8).join(", ")));
    }
  } else {
    band($("coverage"), "–");
    $("hint").hidden = false;
    $("hint").textContent = m.cvFound
      ? "No skill list found on this page, so there's no skills match."
      : "Add your cv.md to career-ops to get a skills match.";
  }
  const fit = m.fit;
  band($("fit"), fit ? fit.band[0].toUpperCase() + fit.band.slice(1) : "–", fit ? { strong: "good", related: "warn", weak: "bad" }[fit.band] : "");
}

async function init() {
  server = await getServer();
  $("dashboard").addEventListener("click", (e) => {
    e.preventDefault();
    chrome.tabs.create({ url: `${server}/pipeline?tab=ALL&view=board` });
  });
  $("open-options").addEventListener("click", (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  });

  try {
    const me = await api("/api/companion");
    contact = me.contact;
  } catch (err) {
    showOffline(err);
    return;
  }
  $("main").hidden = false;

  const tab = await activeTab();
  if (!tab || !/^https?:/.test(tab.url || "")) {
    $("title").textContent = "Open a job posting to use career-ops here.";
    $("save").disabled = $("fill").disabled = true;
    return;
  }

  try {
    job = await inTab(tab.id, () => window.CareerOpsCore.extractJob());
  } catch {
    job = null;
  }
  if (!job || !job.title) {
    $("title").textContent = "Couldn't read a job on this page.";
    $("save").disabled = true;
  } else {
    $("title").textContent = job.title;
    $("meta").textContent = [job.company, job.location].filter(Boolean).join(" · ");
    try {
      renderMatch(await api("/api/companion", { method: "POST", body: JSON.stringify({ job }) }));
    } catch (err) {
      setStatus(`Match failed: ${err.message}`, true);
    }
  }

  $("save").addEventListener("click", async () => {
    $("save").disabled = true;
    setStatus("Saving…");
    try {
      const r = await api("/api/explore/add", {
        method: "POST",
        body: JSON.stringify({
          offers: [{ url: job.url, company: job.company, title: job.title, location: job.location, postedAt: "", ats: job.ats, source: "extension" }],
        }),
      });
      if (r.error) throw new Error(r.error);
      setStatus("Saved to your pipeline inbox. Evaluate it from the dashboard.");
    } catch (err) {
      $("save").disabled = false;
      setStatus(`Not saved: ${err.message}`, true);
    }
  });

  $("fill").addEventListener("click", async () => {
    if (!contact || !contact.email) {
      setStatus("Your profile has no contact details yet. Fill in config/profile.yml first.", true);
      return;
    }
    try {
      const r = await inTab(tab.id, (c) => window.CareerOpsCore.autofill(c), [contact]);
      setStatus(r && r.count ? `Filled ${r.count} field${r.count === 1 ? "" : "s"} (outlined in orange). Check them, then submit yourself.` : "No matching empty fields found on this page.");
    } catch (err) {
      setStatus(`Autofill failed: ${err.message}`, true);
    }
  });
}

init();
