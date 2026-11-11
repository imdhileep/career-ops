// career-ops companion — page logic, injected into the active tab on demand.
// Classic script (no imports): chrome.scripting.executeScript({files}) runs it,
// then calls CareerOpsCore.extractJob() / CareerOpsCore.autofill(contact).
// Also loaded by tests/core.test.mjs through node:vm.
(function (root) {
  "use strict";

  const ATS = [
    [/(^|\.)greenhouse\.io$|grnh\.se$/, "greenhouse"],
    [/(^|\.)lever\.co$/, "lever"],
    [/(^|\.)ashbyhq\.com$/, "ashby"],
    [/myworkdayjobs\.com$|myworkdaysite\.com$/, "workday"],
    [/(^|\.)linkedin\.com$/, "linkedin"],
    [/(^|\.)smartrecruiters\.com$/, "smartrecruiters"],
    [/(^|\.)workable\.com$/, "workable"],
    [/(^|\.)icims\.com$/, "icims"],
    [/(^|\.)bamboohr\.com$/, "bamboohr"],
  ];

  function atsFor(hostname) {
    for (const [re, name] of ATS) if (re.test(hostname)) return name;
    return "web";
  }

  function clean(s, max) {
    return String(s == null ? "" : s).replace(/\s+/g, " ").trim().slice(0, max || 300);
  }

  // Like clean(), but keeps line breaks: the skill check reads requirement
  // lists line by line.
  function cleanBlock(s, max) {
    return String(s == null ? "" : s)
      .replace(/[ \t\f\v\u00a0]+/g, " ")
      .replace(/ *\n */g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim()
      .slice(0, max || 60000);
  }

  // JSON-LD descriptions are HTML: turn block tags into line breaks and list
  // items into "- " bullets before dropping the remaining tags.
  function htmlToText(html) {
    return String(html || "")
      .replace(/<li[^>]*>/gi, "\n- ")
      .replace(/<(br|\/p|\/div|\/h[1-6]|\/ul|\/ol|\/li)[^>]*>/gi, "\n")
      .replace(/<h[1-6][^>]*>/gi, "\n## ")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/g, " ")
      .replace(/&amp;/g, "&")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&#39;|&apos;/g, "'")
      .replace(/&quot;/g, '"');
  }

  function textOf(doc, selectors) {
    for (const sel of selectors) {
      const el = doc.querySelector(sel);
      const t = el && clean(el.textContent);
      if (t) return t;
    }
    return "";
  }

  // schema.org JobPosting: most ATS boards and LinkedIn embed it, and it is the
  // most reliable source for title/company/location.
  function jsonLdJob(doc) {
    for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try {
        data = JSON.parse(s.textContent || "");
      } catch {
        continue;
      }
      const items = [].concat(data && data["@graph"] ? data["@graph"] : data);
      for (const it of items) {
        if (!it || typeof it !== "object") continue;
        const type = [].concat(it["@type"] || []);
        if (!type.includes("JobPosting")) continue;
        const org = it.hiringOrganization;
        const place = [].concat(it.jobLocation || [])[0];
        const addr = place && place.address;
        const loc = addr
          ? [addr.addressLocality, addr.addressRegion, addr.addressCountry && (addr.addressCountry.name || addr.addressCountry)]
              .filter((x) => typeof x === "string" && x)
              .join(", ")
          : "";
        const remote = it.jobLocationType === "TELECOMMUTE" ? "Remote" : "";
        const desc = typeof it.description === "string" ? htmlToText(it.description) : "";
        return {
          title: clean(it.title),
          company: clean(org && (org.name || org)),
          location: clean(loc || remote),
          jdText: cleanBlock(desc),
        };
      }
    }
    return null;
  }

  const SITE = {
    greenhouse: { title: ["h1.app-title", ".job__title h1", "h1"], company: [".company-name", ".job__header .company"], location: [".location", ".job__location"], body: ["#content", ".job__description", "main"] },
    lever: { title: [".posting-headline h2", "h2"], company: [], location: [".posting-categories .location", ".location"], body: [".posting-page .content", ".section-wrapper", "main"] },
    ashby: { title: ["h1"], company: [], location: ['[class*="location"]'], body: ['[class*="description"]', "main"] },
    workday: { title: ['[data-automation-id="jobPostingHeader"]', "h2", "h1"], company: [], location: ['[data-automation-id="locations"]'], body: ['[data-automation-id="jobPostingDescription"]', "main"] },
    linkedin: { title: [".job-details-jobs-unified-top-card__job-title", ".top-card-layout__title", "h1"], company: [".job-details-jobs-unified-top-card__company-name", ".topcard__org-name-link"], location: [".job-details-jobs-unified-top-card__primary-description-container", ".topcard__flavor--bullet"], body: ["#job-details", ".jobs-description__content", ".show-more-less-html__markup"] },
    web: { title: ["h1"], company: ['meta[property="og:site_name"]'], location: [], body: ["main", "article", "body"] },
  };

  function extractJob(doc, loc) {
    doc = doc || root.document;
    loc = loc || root.location;
    const ats = atsFor(loc.hostname);
    const sel = SITE[ats] || SITE.web;
    const ld = jsonLdJob(doc) || {};
    const ogSite = doc.querySelector('meta[property="og:site_name"]');
    const bodyEl = sel.body.map((s) => doc.querySelector(s)).find(Boolean);
    let company = ld.company || textOf(doc, sel.company) || (ogSite && clean(ogSite.getAttribute("content"))) || "";
    // Board URLs carry the company slug: boards.greenhouse.io/<co>/..., jobs.lever.co/<co>/..., jobs.ashbyhq.com/<co>/...
    if (!company && /greenhouse|lever|ashby/.test(ats)) {
      const slug = loc.pathname.split("/").filter(Boolean)[0] || "";
      company = slug.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    }
    return {
      url: loc.href,
      ats,
      title: ld.title || textOf(doc, sel.title) || clean(doc.title),
      company,
      location: ld.location || textOf(doc, sel.location),
      jdText: ld.jdText || cleanBlock(bodyEl ? bodyEl.innerText || bodyEl.textContent : ""),
    };
  }

  // ── Autofill ────────────────────────────────────────────────────────
  // Field rules, checked in order against autocomplete, name, id, placeholder,
  // aria-label and the visible label. First match wins.
  const RULES = [
    ["email", /e-?mail/i, ["email"]],
    ["phone", /phone|mobile|tel\b/i, ["tel", "tel-national"]],
    ["firstName", /first.?name|given.?name|fname|first$/i, ["given-name"]],
    ["lastName", /last.?name|family.?name|surname|lname/i, ["family-name"]],
    ["fullName", /full.?name|^name$|your.?name|candidate.?name|legal.?name/i, ["name"]],
    ["linkedin", /linked.?in/i, []],
    ["github", /git.?hub/i, []],
    ["portfolio", /portfolio|personal.?(site|website)|website|blog/i, ["url"]],
    ["location", /location|city|current.?(address|location)/i, ["address-level2"]],
  ];

  function labelText(el, doc) {
    const parts = [];
    if (el.id) {
      const l = doc.querySelector('label[for="' + (root.CSS && root.CSS.escape ? root.CSS.escape(el.id) : el.id) + '"]');
      if (l) parts.push(l.textContent);
    }
    const wrap = el.closest && el.closest("label");
    if (wrap) parts.push(wrap.textContent);
    return clean(parts.join(" "));
  }

  function fieldKey(el, doc) {
    const ac = (el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/).pop();
    for (const [key, , acs] of RULES) if (ac && acs.includes(ac)) return key;
    const hay = [el.name, el.id, el.getAttribute("placeholder"), el.getAttribute("aria-label"), labelText(el, doc)]
      .filter(Boolean)
      .join(" | ");
    for (const [key, re] of RULES) if (re.test(hay)) return key;
    return null;
  }

  function fillable(el) {
    if (el.disabled || el.readOnly) return false;
    if (el.tagName === "TEXTAREA") return true;
    const t = (el.type || "text").toLowerCase();
    return ["text", "email", "tel", "url", "search", ""].includes(t);
  }

  // React/Vue keep their own value tracker; set through the native setter and
  // fire input/change so the framework sees the change.
  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? root.HTMLTextAreaElement.prototype : root.HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new root.Event("input", { bubbles: true }));
    el.dispatchEvent(new root.Event("change", { bubbles: true }));
  }

  /**
   * Fill contact fields on the page. Never submits, never clicks, never
   * overwrites something already typed, never touches file/password fields.
   */
  function autofill(contact, doc) {
    doc = doc || root.document;
    const filled = [];
    const used = new Set();
    for (const el of doc.querySelectorAll("input, textarea")) {
      if (!fillable(el) || (el.value && el.value.trim())) continue;
      const key = fieldKey(el, doc);
      if (!key) continue;
      let value = contact && contact[key];
      if (key === "fullName" && !value && contact) value = [contact.firstName, contact.lastName].filter(Boolean).join(" ");
      if (!value) continue;
      // One value per key per form, except names (some forms repeat them).
      if (used.has(key) && key !== "fullName") continue;
      setValue(el, value);
      el.style.outline = "2px solid #e8590c";
      used.add(key);
      filled.push(key);
    }
    return { filled, count: filled.length };
  }

  root.CareerOpsCore = { atsFor, extractJob, jsonLdJob, fieldKey, autofill };
})(typeof window !== "undefined" ? window : globalThis);
