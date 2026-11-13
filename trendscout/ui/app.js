/* TrendScout app. Plain JavaScript, no build step, no third-party code.
 * Text from data sources always goes through textContent, never innerHTML.
 * Layout follows the TrendRadar look: a report card, and a settings editor with
 * numbered modules on the left and the live config file on the right. */
"use strict";

// ------------------------------------------------------------------ basics

const TOKEN_KEY = "trendscout-token";
(function takeToken() {
  const t = new URLSearchParams(location.search).get("token");
  if (t) {
    localStorage.setItem(TOKEN_KEY, t);
    history.replaceState(null, "", location.pathname + (location.hash || "#/report"));
  }
})();
const TOKEN = localStorage.getItem(TOKEN_KEY) || "";

const REPO_URL = "https://github.com/scavio-ai/trendscout";
const SCAVIO_URL = "https://scavio.dev/?utm_source=github&utm_medium=ui&utm_campaign=trendscout";
const PLATFORMS = { tiktok: "TikTok", youtube: "YouTube", reddit: "Reddit", google: "Google", x: "X", hackernews: "Hacker News", news: "News" };
const TRENDING_SOURCES = new Set(["tiktok_searches", "tiktok_videos", "tiktok_sounds", "google_trending", "google_trends_rss", "x_trends", "reddit_trending", "reddit_popular", "hackernews"]);
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_NAMES = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
const STYLE_NAMES = { new: "Only what is new", summary: "Summary of today", every: "Every scan" };

const NICHES = [
  { name: "AI and tech", topics: [
    { name: "AI agents", match: ["ai agents", "ai agent", "agentic"] },
    { name: "OpenAI", match: ["openai", "chatgpt", "sam altman"] },
    { name: "Nvidia", match: ["nvidia", "jensen huang"] },
    { name: "Apple", match: ["iphone", "macbook", "ios 27", "apple intelligence"] },
  ] },
  { name: "E-commerce", topics: [
    { name: "TikTok Shop", match: ["tiktok shop", "tiktokshop"] },
    { name: "Viral products", match: ["viral product", "viral products", "tiktok made me buy"] },
    { name: "Amazon finds", match: ["amazon finds", "amazon must haves"] },
    { name: "Dropshipping", match: ["dropshipping", "dropship"] },
  ] },
  { name: "Beauty", topics: [
    { name: "Skincare", match: ["skincare", "skin care", "retinol", "sunscreen", "serum"] },
    { name: "Makeup", match: ["makeup", "lipstick", "mascara", "foundation"] },
    { name: "Haircare", match: ["haircare", "hair care", "shampoo"] },
  ] },
  { name: "Fitness", topics: [
    { name: "Creatine", match: ["creatine"] },
    { name: "Protein", match: ["protein powder", "high protein"] },
    { name: "Running", match: ["marathon", "running shoes"] },
  ] },
  { name: "Gaming", topics: [
    { name: "GTA 6", match: ["gta 6", "gta vi"] },
    { name: "Nintendo", match: ["nintendo", "switch 2"] },
    { name: "Fortnite", match: ["fortnite"] },
  ] },
];

const SCHEDULES = [
  { id: "hourly", icon: "bolt", tone: "blue", label: "Every hour", detail: "Watch all day. New things reach you within the hour.", schedule: { every: "1h" } },
  { id: "twice", icon: "sun", tone: "amber", label: "Morning and evening", detail: "08:00 and 18:00 every day. Suits most people.", schedule: { at: ["08:00", "18:00"] }, recommended: true },
  { id: "office", icon: "briefcase", tone: "green", label: "Office hours", detail: "09:00, 13:00 and 17:00 on weekdays.", schedule: { at: ["09:00", "13:00", "17:00"], days: ["mon", "tue", "wed", "thu", "fri"] } },
  { id: "3h", icon: "clock", tone: "indigo", label: "Every 3 hours", detail: "A steady pulse through the day and night.", schedule: { every: "3h" } },
  { id: "custom", icon: "sliders", tone: "purple", label: "Custom", detail: "Your own times and days." },
  { id: "off", icon: "pause", tone: "gray", label: "Only when I click Scan now", detail: "No automatic scans." },
];

const ALERT_STYLES = [
  { id: "new", icon: "bolt", tone: "blue", label: "Only what is new", detail: "Only what appeared since the last scan. No alert when nothing did.", recommended: true },
  { id: "summary", icon: "sun", tone: "amber", label: "Summary of today", detail: "Everything that matched since midnight. Pairs with morning and evening scans." },
  { id: "every", icon: "list", tone: "green", label: "Every scan", detail: "The current matches after every scan, new or not." },
];

const CHANNELS = {
  telegram: { label: "Telegram", icon: "send", steps: [
    ["Open Telegram, message ", ["@BotFather", "https://t.me/BotFather"], ", send /newbot and follow the prompts."],
    ["Paste the bot token it gives you below and save."],
    ["Send any message to your new bot (or add it to a group), then press Find my chat."],
  ] },
  discord: { label: "Discord", icon: "chat", steps: [
    ["In Discord, open the channel's settings (the gear next to its name)."],
    ["Go to Integrations, then Webhooks, then New Webhook."],
    ["Copy the webhook URL, paste it below and save."],
  ] },
  slack: { label: "Slack", icon: "hash", steps: [
    ["Go to ", ["api.slack.com/apps", "https://api.slack.com/apps"], " and create an app from scratch."],
    ["Open Incoming Webhooks, turn them on, and add a webhook to the channel you want."],
    ["Copy the webhook URL, paste it below and save."],
  ] },
  ntfy: { label: "Phone", icon: "phone", steps: [
    ["Install the free ntfy app on your phone (App Store or Google Play)."],
    ["Tap Subscribe and enter the topic name below. Anyone who knows the name can read it, so keep the random one."],
    ["Save, then send a test."],
  ] },
  email: { label: "Email", icon: "mail", steps: [
    ["Use any mail account that allows SMTP. For Gmail, turn on 2-step verification and create an ", ["app password", "https://myaccount.google.com/apppasswords"], "."],
    ["Gmail settings: host smtp.gmail.com, port 587, your address as username and From."],
    ["Save, then send a test."],
  ] },
  webhook: { label: "Webhook", icon: "link", steps: [
    ["Paste a URL that accepts a JSON POST: Zapier, Make, n8n or your own server."],
    ["Each alert sends the alert and the full report. Save, then send a test."],
  ] },
};

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
// replaceChildren() prints null as text; skip empty slots the way h() does.
const nativeReplace = Element.prototype.replaceChildren;
Element.prototype.replaceChildren = function (...kids) {
  return nativeReplace.apply(this, kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
};
const clone = (o) => JSON.parse(JSON.stringify(o));

// Small line icons, drawn here so nothing loads from a CDN.
const ICONS = {
  bolt: "M13 2 4 14h7l-1 8 9-12h-7z",
  sun: "M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 1v3M12 20v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M1 12h3M20 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1",
  briefcase: "M3 7h18v13H3zM9 7V4h6v3M3 12h18",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 3",
  sliders: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
  pause: "M8 5v14M16 5v14",
  list: "M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1",
  send: "M22 2 11 13M22 2l-7 20-4-9-9-4z",
  chat: "M21 12a8 8 0 0 1-12 7l-5 2 2-5a8 8 0 1 1 15-4z",
  hash: "M4 9h16M4 15h16M10 3 8 21M16 3l-2 18",
  phone: "M7 2h10v20H7zM11 18h2",
  mail: "M3 5h18v14H3zM3 6l9 7 9-7",
  link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  shield: "M12 2 4 5v6c0 5 3.5 9.5 8 11 4.5-1.5 8-6 8-11V5z",
  star: "M12 2l3 7 7 .6-5.3 4.7L18.2 22 12 18.3 5.8 22l1.5-7.7L2 9.6 9 9z",
  gift: "M3 9h18v4H3zM5 13h14v8H5zM12 9v12M12 9c-2-4-6-4-6-1s6 1 6 1c2-4 6-4 6-1s-6 1-6 1",
  key: "M15 7a4 4 0 1 1-3.9 5H3v3h3v3h3v-3h2.1A4 4 0 0 1 15 7z",
  chevron: "M6 9l6 6 6-6",
  plus: "M12 5v14M5 12h14",
  check: "M5 12l5 5 9-11",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  code: "M8 8l-5 4 5 4M16 8l5 4-5 4",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 21h4",
  file: "M6 2h9l5 5v15H6zM14 2v6h6",
  radar: "M12 3a9 9 0 1 0 9 9M12 7a5 5 0 1 0 5 5M12 12l7-7",
  x: "M6 6l12 12M18 6 6 18",
};
function icon(name, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", ICONS[name] || "");
  svg.appendChild(p);
  return svg;
}

function safeUrl(url) {
  try { const u = new URL(url); return u.protocol === "https:" || u.protocol === "http:" ? u.href : null; } catch { return null; }
}
function extLink(text, url, cls) {
  const href = safeUrl(url);
  return href ? h("a", { href, target: "_blank", rel: "noopener noreferrer", class: cls }, text) : h("span", { class: cls }, text);
}
function stepText(parts) { return parts.map((p) => (Array.isArray(p) ? extLink(p[0], p[1]) : p)); }

function human(n) {
  if (n === null || n === undefined || isNaN(n)) return "";
  for (const [div, s] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) if (Math.abs(n) >= div) return (n / div).toFixed(1).replace(/\.0$/, "") + s;
  return String(Math.round(n));
}
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" });
const shortFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
function sameDay(a, b) { return a.toDateString() === b.toDateString(); }
function clock(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const now = new Date();
  if (sameDay(d, now)) return timeFmt.format(d);
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
  if (sameDay(d, tomorrow)) return "tomorrow " + timeFmt.format(d);
  return shortFmt.format(d);
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "1 day ago" : `${d} days ago`;
}
function idToIso(id) {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(id || "");
  return m ? `${m[1]}T${m[2]}:${m[3]}:${m[4]}Z` : null;
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function randomTopicName() {
  const a = new Uint8Array(6); crypto.getRandomValues(a);
  return "trendscout-" + Array.from(a, (b) => b.toString(36).padStart(2, "0")).join("").slice(0, 10);
}
function toast(msg, bad) {
  const t = h("div", { class: "toast" + (bad ? " bad" : "") }, msg);
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), bad ? 7000 : 3500);
}

// ------------------------------------------------------------------ API

class ApiError extends Error { constructor(m, status, field) { super(m); this.status = status; this.field = field; } }
async function api(method, path, body) {
  const opts = { method, headers: { "X-TrendScout-Token": TOKEN } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let resp;
  try { resp = await fetch(path, opts); } catch { throw new ApiError("TrendScout is not responding. Is it still running?", 0); }
  let data = null;
  try { data = await resp.json(); } catch { /* empty */ }
  if (resp.status === 401) { showLocked(); throw new ApiError("locked", 401); }
  if (!resp.ok) throw new ApiError((data && data.error) || `HTTP ${resp.status}`, resp.status, data && data.field);
  return data;
}

// ------------------------------------------------------------------ state

const S = {
  state: null, cfg: null, form: null, formErrors: {}, estimate: null, saveState: "saved",
  secrets: null, drafts: {}, replacing: {}, results: {},
  report: null, reportId: null, history: null,
  job: null, jobSince: 0, jobTimer: null,
  openChannel: null, openTopic: null, previews: {}, dialog: null, expanded: {}, everythingTab: null, everythingOpen: false,
  open: new Set(["topics", "channels", "schedule"]), codeTab: "yaml", yaml: null, alertPreview: null,
  background: null, tgChats: null, wizard: null,
};

// ------------------------------------------------------------------ shell

function showLocked() {
  document.body.replaceChildren(h("div", { class: "locked" },
    h("h1", {}, "Open TrendScout from its link"),
    h("p", {}, "This page needs the link TrendScout printed when it started, the one that ends in ?token=. It keeps other websites from using your keys."),
    h("p", { class: "muted" }, "Run trendscout ui again to open it."),
  ));
}
function isRunning() { return S.job && S.job.status === "running"; }

function paintTopbar() {
  const st = S.state;
  const ready = st && st.has_config;
  for (const a of document.querySelectorAll(".views a")) {
    if (a.dataset.route === route()) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  $(".views").hidden = !ready;
  const btn = $("#scan-now");
  btn.hidden = !ready;
  const status = $("#scan-status");
  if (!ready) { status.replaceChildren(); return; }
  if (isRunning()) {
    const last = S.job.log.length ? S.job.log[S.job.log.length - 1].trim() : "Starting";
    status.replaceChildren(h("span", { class: "pulse", "aria-hidden": "true" }), h("span", { class: "line" }, S.job.kind === "run" ? `Scanning: ${last}` : `${S.job.label}: ${last}`));
  } else {
    const next = S.cfg && S.cfg.plan && S.cfg.plan.next_scan_at;
    status.replaceChildren(
      icon("clock"),
      st.latest ? h("span", {}, "Last scan ", h("b", {}, ago(idToIso(st.latest)))) : h("span", {}, "No scans yet"),
      next ? h("span", { class: "faint" }, `next ${clock(next)}`) : null,
    );
  }
  btn.disabled = isRunning();
  btn.replaceChildren(icon("refresh"), isRunning() && S.job.kind === "run" ? "Scanning" : "Scan now");
}

async function refreshState() {
  S.state = await api("GET", "/api/state");
  if (S.state.job && S.state.job.status === "running" && (!S.job || S.job.id !== S.state.job.id)) followJob(S.state.job, true);
  paintTopbar();
}

// ------------------------------------------------------------------ jobs

function followJob(job, fresh) {
  S.job = { ...job, log: fresh ? [] : job.log.slice() };
  S.jobSince = fresh ? 0 : job.log_size;
  clearTimeout(S.jobTimer);
  pollJob();
}
async function pollJob() {
  let job;
  try { job = await api("GET", `/api/job?since=${S.jobSince}`); } catch { S.jobTimer = setTimeout(pollJob, 2000); return; }
  if (!job) return;
  if (!S.job || job.id !== S.job.id) S.job = { ...job, log: [] };
  S.job.log.push(...job.log);
  S.jobSince = job.log_size;
  Object.assign(S.job, { status: job.status, result: job.result, error: job.error });
  paintTopbar();
  const box = $("#scan-log");
  if (box) fillLog(box);
  if (job.status === "running") S.jobTimer = setTimeout(pollJob, 700);
  else jobFinished(S.job);
}
async function jobFinished(job) {
  if (job.kind === "run") {
    if (job.status === "done" && job.result && job.result.ok) {
      const r = job.result;
      toast(r.alert ? `Scan done. Alert: ${r.alert}.` : `Scan done: ${r.items} items, nothing new for your topics.`);
      S.reportId = null; S.report = null; S.history = null; S.alertPreview = null;
    } else toast(job.error || "The scan collected nothing. Check your internet connection.", true);
    await refreshState();
    S.cfg = await api("GET", "/api/config");
  } else if (job.kind === "preview") {
    if (job.result) S.previews[job.result.name] = job.result; else S.previews[S.openTopic] = { error: job.error };
    if (S.dialog && S.dialog.previewing) { S.dialog.preview = job.status === "done" ? job.result : { error: job.error }; S.dialog.previewing = false; renderDialog(); }
  } else if (job.kind === "try") {
    const name = job.result ? job.result.source : job.label.replace(/^Try /, "");
    S.previews["source:" + name] = job.status === "done" ? job.result : { error: job.error };
  } else if (job.kind === "send") {
    const ok = job.result && job.result.status === "ok";
    const ch = job.label.replace(/^Test /, "");
    S.results[ch] = { ok, text: ok ? `Sent. Check ${CHANNELS[ch] ? CHANNELS[ch].label : ch}.` : `Not sent: ${job.result ? job.result.status : job.error}` };
  } else if (job.kind === "briefing") {
    S.results.llm = job.status === "done" ? { ok: true, text: job.result.text, pre: true } : { ok: false, text: job.error };
  }
  render();
}
async function startJob(method, path, body) {
  try { const job = await api(method, path, body); followJob(job, false); render(); return job; }
  catch (e) { if (e.status !== 401) toast(e.message, true); return null; }
}
function startScan() { return startJob("POST", "/api/run", { notify: true }); }
function fillLog(box) {
  const job = S.job;
  if (!job || job.kind !== "run") { box.replaceChildren(); return; }
  const lines = job.log.filter((l) => !/credit/i.test(l));
  box.replaceChildren(...lines.slice(-200).map((l) => h("div", { class: /failed|Failed/.test(l) ? "fail" : null }, l || " ")));
  box.scrollTop = box.scrollHeight;
}

// ------------------------------------------------------------------ router

const ROUTES = { report: viewReport, settings: viewSettings };
const ALIASES = { today: "report", topics: "settings", alerts: "settings" };
function route() {
  const n = location.hash.replace(/^#\/?/, "").split(/[?/]/)[0];
  return ROUTES[n] ? n : (ALIASES[n] || "report");
}
let renderSeq = 0;
async function render() {
  const seq = ++renderSeq;
  paintTopbar();
  const view = $("#view");
  if (S.state && !S.state.has_config) {
    document.body.dataset.view = "wizard";
    view.replaceChildren(viewWizard());
    return;
  }
  document.body.dataset.view = route();
  try {
    const keep = rememberScroll();
    const node = await ROUTES[route()]();
    if (seq !== renderSeq) return;
    const focusId = document.activeElement && document.activeElement.id;
    view.replaceChildren(node);
    keep();
    if (focusId) { const el = document.getElementById(focusId); if (el) el.focus({ preventScroll: true }); }
  } catch (e) {
    if (e.status === 401 || seq !== renderSeq) return;
    view.replaceChildren(h("div", { class: "empty-card" }, h("h1", {}, "Something went wrong"), h("p", {}, e.message), h("button", { class: "btn", onclick: () => render() }, "Try again")));
  }
}
/** Keep each scrolling column where it was across a re-render. */
function rememberScroll() {
  const spots = ["#modules", "#code-body"].map((sel) => [sel, $(sel) ? $(sel).scrollTop : 0]);
  const y = window.scrollY;
  return () => { for (const [sel, top] of spots) { const el = $(sel); if (el) el.scrollTop = top; } window.scrollTo(0, y); };
}
window.addEventListener("hashchange", () => {
  const mod = location.hash.split("/")[2];
  if (mod) S.open.add(mod);
  render().then(() => { if (mod) { const el = document.getElementById("mod-" + mod); if (el) el.scrollIntoView({ block: "start" }); } else window.scrollTo(0, 0); });
});

// ------------------------------------------------------------------ shared config form

async function ensureConfig() {
  if (!S.cfg) S.cfg = await api("GET", "/api/config");
  if (!S.form) { S.form = clone(S.cfg.config || S.cfg.defaults); S.estimate = S.cfg.plan; S.formErrors = {}; }
}
// Settings save themselves: every change is written to config.yaml shortly after
// it is made, so nothing is lost by navigating away. Keys keep an explicit Save.
let saveTimer = null;
function isDirty() { return S.saveState === "pending" || S.saveState === "saving"; }
function changed(rerender) {
  S.alertPreview = null; S.yaml = null; S.saveState = "pending";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(autosave, 600);
  paintModulesHead();
  if (rerender) render();
}
async function autosave() {
  clearTimeout(saveTimer);
  S.saveState = "saving"; paintModulesHead();
  try {
    S.cfg = await api("PUT", "/api/config", { config: S.form });
    S.estimate = S.cfg.plan; S.formErrors = {}; S.saveState = "saved";
    S.report = null; S.yaml = null;
    refreshState().catch(() => {});
  } catch (e) {
    S.saveState = "error";
    S.formErrors = { [e.field || "_"]: e.message };
  }
  paintModulesHead();
  refreshCode();
  return S.saveState === "saved";
}
function friendly(msg) {
  return String(msg).replace(/^topics: /, "Topics: ").replace(/^schedule: /, "Schedule: ").replace(/^alerts: /, "Alerts: ")
    .replace(/^max_credits_per_run/, "The scan limit").replace(/^top_n/, "Items per topic").replace(/^keep_days/, "Keep scans for")
    .replace(/^tiktok_video_calls/, "TikTok video calls").replace(/^hackernews_limit/, "Hacker News stories")
    .replace(/, got .*$/, ".");
}
/** Save now (the topic popup, starter sets) and redraw; returns whether it saved. */
async function saveConfig() {
  const ok = await autosave();
  if (ok) S.form = clone(S.cfg.config);
  else toast(friendly(Object.values(S.formErrors)[0] || "Could not save."), true);
  render();
  return ok;
}
window.addEventListener("beforeunload", (ev) => { if (isDirty()) { ev.preventDefault(); ev.returnValue = ""; } });

// ------------------------------------------------------------------ report (the TrendRadar report card)

async function viewReport() {
  await ensureConfig();
  if (!S.report) {
    const q = new URLSearchParams({ items: "1" });
    if (S.reportId) q.set("id", S.reportId);
    try { S.report = await api("GET", "/api/report?" + q); }
    catch (e) { if (e.status === 404) S.report = { empty: true }; else throw e; }
  }
  if (!S.history) S.history = await api("GET", "/api/snapshots");
  const r = S.report;

  if (r.empty) {
    return h("div", { class: "report-page" }, h("div", { class: "report-card" },
      reportHead("Report", "No scans yet.", null),
      isRunning() ? scanningPanel() : h("div", { class: "report-empty" },
        h("p", {}, "Run your first scan to see what is happening with your topics. It takes about a minute."),
        h("button", { class: "btn btn-blue", onclick: startScan }, icon("refresh"), "Scan now")),
      reportFooter()));
  }

  const topics = r.topics || [];
  const withHits = topics.filter((t) => t.hits.length);
  const without = topics.filter((t) => !t.hits.length);
  const matches = topics.reduce((n, t) => n + t.total, 0);
  const newCount = topics.reduce((n, t) => n + t.new, 0);
  const when = new Date(r.taken_at);
  const picker = S.history.length > 1 ? h("select", { class: "header-select", "aria-label": "Show an earlier scan", onchange: (e) => { S.reportId = e.target.value || null; S.report = null; render(); } },
    S.history.map((s, i) => h("option", { value: i === 0 ? "" : s.id, selected: s.id === r.id }, i === 0 ? "Latest scan" : shortFmt.format(new Date(s.taken_at))))) : null;

  const scanned = sameDay(when, new Date()) ? `today at ${timeFmt.format(when)}` : shortFmt.format(when);
  const summary = topics.length
    ? `${matches} match${matches === 1 ? "" : "es"} across ${topics.length} topic${topics.length === 1 ? "" : "s"}${r.previous_at ? `, ${newCount} new since the scan before` : ""}. Scanned ${scanned}, ${(r.items || []).length} items checked.`
    : `Scanned ${scanned}, ${(r.items || []).length} items checked.`;
  let groupNo = 0;
  const groups = withHits.length + (r.cross_platform.length ? 1 : 0);

  return h("div", { class: "report-page" }, h("div", { class: "report-card", id: "report-card" },
    reportHead(S.reportId ? dayFmt.format(when) : "Report", summary,
      h("div", { class: "row" }, picker, withHits.length ? h("button", { class: "btn btn-light small", onclick: () => shareImage(r) }, "Save as image") : null)),
    isRunning() && S.job.kind === "run" ? scanningPanel() : null,
    !r.previous_at ? h("p", { class: "report-note" }, "First scan: nothing is marked new yet. The next scan flags what changed.") : null,
    r.summary ? h("div", { class: "report-briefing" }, h("b", {}, "AI briefing"), h("p", {}, r.summary)) : null,
    !topics.length ? h("p", { class: "report-note" }, "No topics yet. ", h("a", { href: "#/settings/topics" }, "Add one"), " to see what matters to you first.") : null,
    h("div", { class: "report-body" },
      withHits.map((t) => wordGroup(t.name, t.new ? `${t.new} new` : `${t.total}`, t.new > 0, `${++groupNo}/${groups}`, t.hits, t.name)),
      r.cross_platform.length ? crossGroup(r.cross_platform, `${++groupNo}/${groups}`) : null,
      without.length ? h("p", { class: "quiet-topics" }, `Nothing this scan for ${without.map((t) => t.name).join(", ")}. `, h("a", { href: "#/settings/topics" }, "Adjust topics")) : null,
      everythingBlock(r),
      problemsBlock(r),
    ),
    reportFooter(),
  ));
}

function reportHead(title, summary, actions) {
  return h("div", { class: "report-head" },
    h("div", {}, h("h1", { class: "report-title" }, title), h("p", { class: "report-summary" }, summary)),
    actions,
  );
}
function reportFooter() {
  return h("div", { class: "report-footer" }, "TrendScout is open source. ", extLink("View it on GitHub", REPO_URL));
}
function scanningPanel() {
  const box = h("div", { class: "scan-log", id: "scan-log", role: "log", "aria-live": "polite" });
  fillLog(box);
  return h("div", { class: "report-scanning" }, h("div", { class: "row" }, h("span", { class: "pulse" }), h("b", {}, "Scanning"), h("span", { class: "hint" }, "about a minute")), box);
}

function rankClass(best) { return best <= 5 ? "rank-num top" : best <= 10 ? "rank-num high" : "rank-num"; }

function newsItem(n, hit) {
  const seen = hit.seen;
  const meta = [h("span", { class: "source-name" }, PLATFORMS[hit.platform] || hit.platform)];
  if (TRENDING_SOURCES.has(hit.source)) {
    const best = seen ? seen.best : hit.rank;
    const worst = seen ? seen.worst : hit.rank;
    meta.push(h("span", { class: rankClass(best) }, best === worst ? String(best) : `${best}-${worst}`));
    if (seen) {
      const a = clock(seen.first), b = clock(seen.last);
      meta.push(h("span", { class: "time-info" }, a === b ? a : `${a}~${b}`));
      if (seen.count > 1) meta.push(h("span", { class: "count-info" }, `${seen.count} times`));
    }
  }
  const m = hit.metrics || {};
  for (const [k, label] of [["views", "views"], ["searches", "searches"], ["upvotes", "upvotes"], ["points", "points"], ["likes", "likes"]]) {
    if (m[k]) { meta.push(h("span", { class: "time-info" }, `${human(m[k])} ${label}`)); break; }
  }
  const x = hit.extra || {};
  if (x.subreddit) meta.push(h("span", { class: "time-info" }, `r/${x.subreddit}`));
  else if (x.publisher || x.channel) meta.push(h("span", { class: "time-info" }, x.publisher || x.channel));
  if (!TRENDING_SOURCES.has(hit.source) && hit.published_at) meta.push(h("span", { class: "time-info" }, ago(hit.published_at)));
  if (hit.new) meta.push(h("span", { class: "new-tag" }, "NEW"));
  return h("div", { class: "news-item" },
    h("div", { class: "news-number" }, n),
    h("div", { class: "news-content" }, h("div", { class: "news-meta" }, meta), extLink(hit.title, hit.url, "news-title")),
  );
}

function wordGroup(name, count, hot, index, hits, key) {
  const open = S.expanded[key];
  const shown = open ? hits : hits.slice(0, 5);
  return h("section", { class: "word-group" },
    h("div", { class: "word-header" },
      h("div", { class: "word-info" }, h("span", { class: "word-name" }, name), h("span", { class: "word-count" + (hot ? " hot" : "") }, count)),
      h("span", { class: "word-index" }, index)),
    shown.map((hit, i) => newsItem(i + 1, hit)),
    hits.length > 5 ? h("button", { class: "more-btn", onclick: () => { S.expanded[key] = !open; render(); } }, open ? "Show fewer" : `Show ${hits.length - 5} more`) : null,
  );
}

function crossGroup(signals, index) {
  return h("section", { class: "word-group" },
    h("div", { class: "word-header" },
      h("div", { class: "word-info" }, h("span", { class: "word-name" }, "Trending on more than one platform"), h("span", { class: "word-count hot" }, String(signals.length))),
      h("span", { class: "word-index" }, index)),
    signals.map((s, i) => {
      const others = s.items.filter((it) => it.title.toLowerCase() !== s.title.toLowerCase()).slice(0, 2);
      const lead = s.items[0];
      return h("div", { class: "news-item" },
        h("div", { class: "news-number" }, i + 1),
        h("div", { class: "news-content" },
          h("div", { class: "news-meta" }, s.platforms.map((p) => h("span", { class: "plat-tag" }, PLATFORMS[p] || p)), s.status === "new" ? h("span", { class: "new-tag" }, "NEW") : null),
          extLink(s.title, lead && lead.url, "news-title"),
          others.map((it) => h("div", { class: "evidence" }, `${PLATFORMS[it.platform] || it.platform}: `, extLink(it.title.length > 100 ? it.title.slice(0, 98) + "..." : it.title, it.url))),
        ));
    }),
  );
}

function everythingBlock(r) {
  if (!r.sections.length) return null;
  const tab = S.everythingTab && r.sections.some((s) => s.source === S.everythingTab) ? S.everythingTab : r.sections[0].source;
  const sec = r.sections.find((s) => s.source === tab);
  const det = h("details", { class: "everything" },
    h("summary", {}, h("span", { class: "word-name" }, "Everything else trending"), h("span", { class: "word-count" }, `${r.sections.length} lists`)),
    h("div", { class: "tabs", role: "tablist" }, r.sections.map((s) => h("button", { role: "tab", "aria-selected": s.source === tab ? "true" : "false", onclick: () => { S.everythingTab = s.source; render(); } }, s.label))),
    sec.entries.map((e, i) => newsItem(e.rank || i + 1, { ...e, seen: null, new: e.status === "new" })),
  );
  if (S.everythingOpen) det.open = true;
  det.addEventListener("toggle", () => { S.everythingOpen = det.open; });
  return det;
}
function problemsBlock(r) {
  const errs = Object.entries(r.errors || {});
  const skipped = Object.entries(r.skipped || {});
  if (!errs.length && !skipped.length) return null;
  return h("details", { class: "problems" },
    h("summary", {}, `${errs.length + skipped.length} source${errs.length + skipped.length === 1 ? "" : "s"} did not run this scan`),
    h("ul", {}, errs.map(([k, v]) => h("li", {}, `${k}: ${v}`)), skipped.map(([k, v]) => h("li", {}, `${k}: skipped, ${v}`))),
  );
}

/** Draw the topic highlights in the report-card style and download a PNG. */
function shareImage(r) {
  const W = 1080, pad = 56, textX = pad + 64, textW = W - textX - pad;
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d");
  const font = (w, s) => `${w} ${s}px system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`;
  const wrap = (text, maxW, f) => {
    ctx.font = f;
    const words = text.split(/\s+/); const lines = []; let line = "";
    for (const wd of words) { const t = line ? line + " " + wd : wd; if (ctx.measureText(t).width > maxW && line) { lines.push(line); line = wd; } else line = t; }
    if (line) lines.push(line);
    if (lines.length > 2) { lines.length = 2; lines[1] = lines[1].replace(/\s*\S*$/, "") + "..."; }
    return lines;
  };
  const blocks = (r.topics || []).filter((t) => t.hits.length).slice(0, 4).map((t) => ({ t, hits: t.hits.slice(0, 3) }));
  let H = 220;
  for (const b of blocks) { H += 70; for (const hit of b.hits) H += 34 + 34 * wrap(hit.title, textW, font(500, 26)).length + 18; }
  H += 90;
  c.width = W; c.height = H;
  ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = "#111827"; ctx.textAlign = "left";
  ctx.font = font(700, 44); ctx.fillText("Trend report", pad, 100);
  ctx.fillStyle = "#4b5563"; ctx.font = font(400, 24); ctx.fillText(dayFmt.format(new Date(r.taken_at)), pad, 142);
  ctx.fillStyle = "#e5e7eb"; ctx.fillRect(pad, 180, W - pad * 2, 2);
  let y = 230;
  for (const b of blocks) {
    ctx.fillStyle = "#1a1a1a"; ctx.font = font(700, 32); ctx.fillText(b.t.name, pad, y + 26);
    const w = ctx.measureText(b.t.name).width;
    ctx.fillStyle = b.t.new ? "#dc2626" : "#6b7280"; ctx.font = font(700, 22); ctx.fillText(b.t.new ? `${b.t.new} new` : `${b.t.total}`, pad + w + 16, y + 24);
    ctx.fillStyle = "#f0f0f0"; ctx.fillRect(pad, y + 44, W - pad * 2, 2);
    y += 70;
    b.hits.forEach((hit, i) => {
      ctx.fillStyle = "#f3f4f6"; ctx.beginPath(); ctx.arc(pad + 18, y + 30, 18, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#6b7280"; ctx.font = font(600, 20); ctx.textAlign = "center"; ctx.fillText(String(i + 1), pad + 18, y + 37); ctx.textAlign = "left";
      ctx.fillStyle = "#666666"; ctx.font = font(500, 20); ctx.fillText(PLATFORMS[hit.platform] || hit.platform, textX, y + 22);
      y += 34;
      ctx.fillStyle = "#2563eb";
      for (const ln of wrap(hit.title, textW, font(500, 26))) { ctx.font = font(500, 26); ctx.fillText(ln, textX, y + 22); y += 34; }
      y += 18;
    });
  }
  ctx.fillStyle = "#e5e7eb"; ctx.fillRect(pad, H - 76, W - pad * 2, 2);
  ctx.fillStyle = "#4b5563"; ctx.font = font(400, 20); ctx.fillText("Made with TrendScout, the open-source trend radar", pad, H - 34);
  c.toBlob((blob) => {
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: `trendscout-${r.id}.png` });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

// ------------------------------------------------------------------ settings editor

const MODULES = [
  { id: "topics", title: "Topics", body: modTopics, done: () => (S.form.topics || []).length > 0, summary: () => `${S.form.topics.length} topic${S.form.topics.length === 1 ? "" : "s"}` },
  { id: "lists", title: "Trending lists", body: modLists, done: () => Object.values(S.form.sources).some(Boolean), summary: () => `${S.cfg.sources.filter((s) => s.kind === "global" && S.form.sources[s.name]).length} on` },
  { id: "channels", title: "Where alerts go", body: modChannels, done: () => S.secrets && S.secrets.groups.some((g) => g.kind === "channel" && g.enabled), summary: () => { const on = S.secrets ? S.secrets.groups.filter((g) => g.kind === "channel" && g.enabled).map((g) => CHANNELS[g.id] ? CHANNELS[g.id].label : g.label) : []; return on.length ? on.join(", ") : "none yet"; } },
  { id: "style", title: "What alerts contain", body: modStyle, done: () => true, summary: () => STYLE_NAMES[S.form.alerts.send] },
  { id: "schedule", title: "When to check", body: modSchedule, done: () => S.form.schedule.enabled !== false, summary: () => (S.estimate && S.estimate.schedule_text) || "" },
  { id: "key", title: "Scavio key", body: () => keyGroup("scavio"), done: () => S.state && S.state.has_key, summary: () => (S.state && S.state.has_key ? "set" : "free sources only") },
  { id: "ai", title: "AI briefing", body: () => keyGroup("llm"), done: () => { const g = group("llm"); return g && g.enabled; }, summary: () => { const g = group("llm"); return g && g.enabled ? "on" : "off"; } },
  { id: "region", title: "Region", body: modRegion, done: () => true, summary: () => regionName(S.form.country) },
  { id: "advanced", title: "Advanced", body: modAdvanced, done: () => false, summary: () => "" },
];

async function viewSettings() {
  await ensureConfig();
  if (!S.secrets) S.secrets = await api("GET", "/api/secrets");
  if (!S.background) S.background = await api("GET", "/api/background");
  if (!S.history) S.history = await api("GET", "/api/snapshots");
  return h("div", { class: "editor" },
    h("div", { class: "modules-col", id: "modules" },
      h("div", { class: "modules-head", id: "modules-head" }),
      MODULES.map((m, i) => moduleCard(m, i + 1)),
    ),
    codePanel(),
  );
}

function paintModulesHead() {
  const head = $("#modules-head");
  if (!head) return;
  const err = Object.values(S.formErrors)[0];
  const state = S.saveState || "saved";
  const status = state === "error"
    ? h("span", { class: "save-state bad", role: "alert" }, `Not saved: ${friendly(err || "check the highlighted setting")}`)
    : state === "saved"
      ? h("span", { class: "save-state" }, icon("check"), "All changes saved")
      : h("span", { class: "save-state" }, "Saving");
  head.replaceChildren(h("div", { class: "row" }, h("h2", { class: "modules-title" }, "Settings"), status));
}

function moduleCard(m, n) {
  const open = S.open.has(m.id);
  const card = h("section", { class: "module" + (open ? " open" : ""), id: "mod-" + m.id },
    h("button", { class: "module-head", "aria-expanded": open ? "true" : "false", onclick: () => { open ? S.open.delete(m.id) : S.open.add(m.id); render(); } },
      h("span", { class: "module-title" }, m.title),
      h("span", { class: "module-summary" }, m.summary()),
      icon("chevron", "chev")),
  );
  if (open) card.appendChild(h("div", { class: "module-body" }, m.body()));
  setTimeout(paintModulesHead, 0);
  return card;
}

// ---- right-hand code panel: what saving would write, the next alert, the .env file

const refreshCode = debounce(async () => {
  if (route() !== "settings") return;
  try {
    if (S.codeTab === "yaml") S.yaml = (await api("POST", "/api/config/yaml", { config: (S.cfg && S.cfg.config) || S.form })).text;
    else if (S.codeTab === "alert") S.alertPreview = await api("POST", `/api/alert-preview?mode=${encodeURIComponent(S.form.alerts.send)}`, { config: S.form });
  } catch { /* an invalid form shows its error in the modules column */ }
  paintCode();
}, 300);

function codePanel() {
  const tabs = [["yaml", "file", "config.yaml"], ["alert", "bell", "Next alert"], ["env", "key", ".env"]];
  setTimeout(() => { paintCode(); if ((S.codeTab === "yaml" && S.yaml === null) || (S.codeTab === "alert" && !S.alertPreview)) refreshCode(); }, 0);
  return h("div", { class: "code-col" },
    h("div", { class: "code-tabs" },
      tabs.map(([id, ic, label]) => h("button", { class: "code-tab" + (S.codeTab === id ? " active" : ""), onclick: () => { S.codeTab = id; render(); } }, icon(ic), label)),
      h("span", { class: "code-status", id: "code-status" })),
    h("div", { class: "code-body", id: "code-body" }),
    h("div", { class: "code-foot" }, S.codeTab === "yaml" ? "Your config.yaml. It updates as you change settings on the left." : S.codeTab === "alert" ? "Built from your latest scan with the settings on the left." : "Keys are never shown, only their last four characters."),
  );
}

function paintCode() {
  const body = $("#code-body");
  if (!body) return;
  const status = $("#code-status");
  if (status) status.replaceChildren(isDirty() ? "Saving" : "Saved");
  let text = "";
  if (S.codeTab === "yaml") text = S.yaml === null ? "# Loading ..." : S.yaml;
  else if (S.codeTab === "alert") text = S.alertPreview ? S.alertPreview.text : "Loading ...";
  else text = envText();
  body.replaceChildren(...text.split("\n").map((line) => codeLine(line, S.codeTab)));
}

function envText() {
  if (!S.secrets) return "";
  const lines = [`# ${S.secrets.path}`, ""];
  for (const g of S.secrets.groups) {
    lines.push(`# ${g.label}`);
    for (const v of g.vars) {
      const shown = !v.set ? "" : v.secret ? (v.hint ? `<saved, ends in ${v.hint.slice(3)}>` : "<saved>") : v.hint;
      lines.push(`${v.name}=${shown}` + (v.source === "environment" ? "   # from your shell" : ""));
    }
    lines.push("");
  }
  return lines.join("\n");
}

function codeLine(line, kind) {
  const el = h("div", { class: "code-line" });
  if (!line) { el.textContent = " "; return el; }
  if (kind === "alert") {
    if (/^\S/.test(line)) el.appendChild(h("span", { class: "tok-key" }, line));
    else if (/^\s+https?:\/\//.test(line)) el.appendChild(h("span", { class: "tok-url" }, line));
    else el.appendChild(h("span", {}, line));
    return el;
  }
  const hash = line.indexOf("#");
  const code = hash === 0 || (hash > 0 && /\s/.test(line[hash - 1])) ? line.slice(0, hash) : line;
  const comment = code === line ? "" : line.slice(code.length);
  const m = /^(\s*-?\s*)([\w.]+)(:|=)(.*)$/.exec(code);
  if (m) el.append(m[1], h("span", { class: "tok-key" }, m[2]), m[3], h("span", { class: "tok-val" }, m[4]));
  else el.append(h("span", { class: "tok-val" }, code));
  if (comment) el.appendChild(h("span", { class: "tok-comment" }, comment));
  return el;
}

// ---- module bodies

function modTopics() {
  const f = S.form;
  return [
    h("p", { class: "hint" }, "Every scan looks for these on the trending lists and anywhere else you choose."),
    h("div", { class: "row wrap" },
      h("button", { class: "btn btn-green", onclick: () => openTopicDialog(null, "settings") }, icon("plus"), "Add topic"),
      h("span", { class: "hint" }, "or add a starter set:"),
      NICHES.map((n) => h("button", { class: "btn btn-light small", onclick: () => {
        let added = 0;
        for (const t of n.topics) if (!f.topics.some((x) => x.name.toLowerCase() === t.name.toLowerCase())) { f.topics.push(newTopic(t)); added++; }
        if (!added) { toast(`You already have the ${n.name} topics.`); return; }
        saveConfig().then((ok) => ok && toast(`Added ${added} topics from ${n.name}.`));
      } }, icon("plus"), n.name))),
    f.topics.length ? h("div", { class: "topic-list" }, f.topics.map((t, i) => topicRow(t, i))) : h("p", { class: "hint" }, "No topics yet."),
  ];
}

function wordsField(label, help, list, onchange, id) {
  const input = h("input", { type: "text", id, placeholder: "Type a word and press Enter", autocomplete: "off" });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      const v = input.value.trim().replace(/,$/, "");
      if (v && !list.some((x) => x.toLowerCase() === v.toLowerCase())) { list.push(v); onchange(); }
      input.value = "";
    }
  });
  return h("div", { class: "field" },
    label ? h("label", { class: "label-sm", for: id }, label) : null,
    h("div", { class: "words" }, list.map((w, i) => h("span", { class: "chip" }, w, h("button", { class: "x", "aria-label": `Remove ${w}`, onclick: () => { list.splice(i, 1); onchange(); } }, icon("x")))), input),
    h("div", { class: "hint" }, help),
  );
}

function newTopic(t) { return { name: "", match: [], must: [], exclude: [], search: [], news: true, hackernews: true, notify: null, schedule: null, ...clone(t || {}) }; }

function scheduleText(s) {
  if (!s) return "";
  if (s.enabled === false) return "only when you click Scan now";
  let what;
  if (s.every) {
    const m = /^(\d+)([mhd])$/.exec(s.every);
    const n = m ? Number(m[1]) : 0;
    what = !m ? s.every : m[2] === "h" ? (n === 1 ? "every hour" : `every ${n} hours`) : m[2] === "d" ? (n === 1 ? "once a day" : `every ${n} days`) : `every ${n} minutes`;
  } else {
    const at = s.at || [];
    what = at.length > 1 ? `${at.slice(0, -1).join(", ")} and ${at[at.length - 1]}` : at[0] || "no times yet";
  }
  const days = s.days || WEEKDAYS;
  const dayText = days.length === 7 ? "every day" : JSON.stringify(days) === JSON.stringify(WEEKDAYS.slice(0, 5)) ? "weekdays"
    : JSON.stringify(days) === JSON.stringify(["sat", "sun"]) ? "weekends" : days.map((d) => DAY_NAMES[d]).join(", ");
  return `${what}, ${dayText}`;
}

function connectedChannels() {
  return S.secrets ? S.secrets.groups.filter((g) => g.kind === "channel" && g.enabled).map((g) => g.id) : [];
}

function alertText(t) {
  if (t.notify && !t.notify.length) return "No alerts, shows in the app";
  const chans = (t.notify || connectedChannels()).map((c) => CHANNELS[c] ? CHANNELS[c].label : c);
  const where = !chans.length ? "no channel set up yet" : chans.join(", ");
  const when = t.schedule ? scheduleText(t.schedule) : "with your other topics";
  return `Alerts: ${where}, ${when}`;
}

function whereText(t) {
  const where = ["trending lists"];
  if (t.news !== false) where.push("News");
  if (t.hackernews !== false) where.push("Hacker News");
  for (const p of t.search || []) where.push(PLATFORMS[p]);
  return where.join(", ");
}

function topicRow(t, i) {
  const words = (t.match && t.match.length ? t.match : [t.name]).join(", ");
  return h("div", { class: "topic-row" },
    h("div", { class: "row-line" },
      h("button", { class: "topic-name", onclick: () => openTopicDialog(i, "settings") },
        h("b", {}, t.name),
        h("span", {}, `Words: ${words}`, t.exclude && t.exclude.length ? `; leaves out ${t.exclude.join(", ")}` : ""),
        h("span", {}, `Looks at: ${whereText(t)}`),
        h("span", {}, alertText(t))),
      h("button", { class: "btn btn-light small", onclick: () => openTopicDialog(i, "settings") }, "Edit"),
    ),
  );
}

// ---- the topic popup: every question in one place, for adding and editing

const WHERE = [
  { id: "trending", label: "Trending lists", detail: "What is trending now on TikTok, Google, X, Reddit and the Hacker News front page", free: true, always: true },
  { id: "news", label: "Google News", detail: "Articles from the last two days", free: true },
  { id: "hackernews", label: "Hacker News", detail: "Discussions from the last 7 days", free: true },
  { id: "youtube", label: "YouTube", detail: "The week's most-viewed videos", source: "youtube_keyword" },
  { id: "tiktok", label: "TikTok", detail: "The most-liked videos of the last 7 days", source: "tiktok_keyword" },
  { id: "reddit", label: "Reddit", detail: "Threads from the last 30 days", source: "reddit_keyword" },
];

function openTopicDialog(index, mode) {
  const list = mode === "wizard" ? wizardState().topics : S.form.topics;
  const draft = newTopic(index === null ? {} : list[index]);
  if (!draft.match) draft.match = [];
  S.dialog = { index, mode, draft, preview: null, previewing: false, error: null, armed: false };
  let dlg = $("#topic-dialog");
  if (!dlg) {
    dlg = h("dialog", { id: "topic-dialog", class: "dialog", "aria-labelledby": "dlg-title" });
    dlg.addEventListener("close", () => { S.dialog = null; });
    document.body.appendChild(dlg);
  }
  renderDialog();
  dlg.showModal();
  setTimeout(() => { const n = $("#dlg-name"); if (n) n.focus(); }, 0);
}

function closeDialog() { const dlg = $("#topic-dialog"); if (dlg && dlg.open) dlg.close(); }

function renderDialog() {
  const dlg = $("#topic-dialog");
  if (!dlg || !S.dialog) return;
  const D = S.dialog;
  const d = D.draft;
  const hasKey = S.state && S.state.has_key;
  const editing = D.index !== null;
  const nameInput = h("input", { type: "text", id: "dlg-name", value: d.name, placeholder: "A brand, product, person or hobby", autocomplete: "off",
    oninput: (e) => { d.name = e.target.value; D.error = null; },
    onblur: () => { if (d.name.trim() && !d.match.length) { d.match.push(d.name.trim().toLowerCase()); renderDialog(); } } });
  const redraw = () => renderDialog();

  dlg.replaceChildren(
    h("div", { class: "dialog-head" },
      h("h2", { id: "dlg-title" }, editing ? `Edit ${d.name || "topic"}` : "Add a topic"),
      h("button", { class: "icon-btn", "aria-label": "Close", onclick: closeDialog }, icon("x"))),
    h("div", { class: "dialog-body" },
      h("div", { class: "q" }, h("label", { class: "q-label", for: "dlg-name" }, "What do you want to follow?"), nameInput,
        D.error ? h("div", { class: "error" }, D.error) : null),
      h("div", { class: "q" }, h("span", { class: "q-label" }, "Which words should count?"),
        wordsField("", "Any one of these in a title is a match.", d.match, redraw, "dlg-match")),
      h("div", { class: "q" }, h("span", { class: "q-label" }, "Must any word also appear?", h("span", { class: "optional" }, "Optional")),
        wordsField("", "Narrows a broad word: Apple plus iphone.", d.must, redraw, "dlg-must")),
      h("div", { class: "q" }, h("span", { class: "q-label" }, "Anything to leave out?", h("span", { class: "optional" }, "Optional")),
        wordsField("", "Hides results that mention these.", d.exclude, redraw, "dlg-exclude")),
      h("div", { class: "q" }, h("span", { class: "q-label" }, "Where should TrendScout look?"),
        h("div", { class: "where" }, WHERE.map((w) => {
          const checked = w.always || (w.id === "news" ? d.news !== false : w.id === "hackernews" ? d.hackernews !== false : (d.search || []).includes(w.id));
          const locked = w.always || (!w.free && !hasKey);
          const note = !w.free && !hasKey ? "Needs a Scavio key" : "";
          return h("label", { class: "where-row" + (checked ? " on" : "") + (locked && !w.always ? " locked" : "") },
            h("input", { type: "checkbox", checked, disabled: locked, onchange: (e) => {
              if (w.id === "news") d.news = e.target.checked;
              else if (w.id === "hackernews") d.hackernews = e.target.checked;
              else d.search = e.target.checked ? [...new Set([...(d.search || []), w.id])] : (d.search || []).filter((x) => x !== w.id);
              redraw();
            } }),
            h("span", { class: "where-text" }, h("b", {}, w.label), h("span", {}, w.detail + (w.always ? ", always on" : ""))),
            note ? h("span", { class: "where-note" }, note) : null);
        })),
      ),
      D.mode === "wizard" ? null : notifyQuestion(d, redraw),
      D.mode === "wizard" ? null : scheduleQuestion(d, redraw),
      h("div", { class: "q" },
        h("div", { class: "row" },
          h("button", { class: "btn btn-light small", disabled: !d.name.trim() || D.previewing || isRunning(), onclick: () => {
            D.previewing = true; D.preview = null; renderDialog();
            startJob("POST", "/api/preview-topic", { topic: { ...d, name: d.name.trim(), search: [] } });
          } }, D.previewing ? "Looking" : "Preview"),
          h("span", { class: "hint" }, "Preview checks the latest scan, Google News and Hacker News. TikTok, YouTube and Reddit results show after the next scan.")),
        D.preview ? previewResults(D.preview, `${d.name.trim()} matches`) : null),
    ),
    h("div", { class: "dialog-foot" },
      editing ? h("button", { class: "btn btn-red-text", onclick: () => {
        if (!D.armed) { D.armed = true; renderDialog(); return; }
        removeTopic(D.index, D.mode);
      } }, D.armed ? "Click again to remove" : "Remove topic") : h("span", {}),
      h("div", { class: "row" },
        h("button", { class: "btn btn-light", onclick: closeDialog }, "Cancel"),
        h("button", { class: "btn btn-blue", onclick: submitTopic }, editing ? "Save topic" : "Add topic")),
    ),
  );
}

function notifyQuestion(d, redraw) {
  const connected = connectedChannels();
  const chosen = d.notify === null || d.notify === undefined ? connected : d.notify;
  const body = connected.length
    ? h("div", { class: "where" }, connected.map((id) => {
      const on = chosen.includes(id);
      return h("label", { class: "where-row" + (on ? " on" : "") },
        h("input", { type: "checkbox", checked: on, onchange: (e) => {
          const next = e.target.checked ? [...new Set([...chosen, id])] : chosen.filter((x) => x !== id);
          // Every channel ticked is the default; store nothing, so new channels apply too.
          d.notify = next.length === connected.length ? null : connected.filter((c) => next.includes(c));
          redraw();
        } }),
        h("span", { class: "where-text" }, h("b", {}, CHANNELS[id].label)));
    }))
    : null;
  const none = d.notify && !d.notify.length;
  return h("div", { class: "q" },
    h("span", { class: "q-label" }, "How should you hear about it?"),
    connected.length
      ? [body, h("div", { class: "hint" }, none ? "No channel ticked: this topic only shows in the app." : "Untick all to keep this topic in the app only.")]
      : h("div", { class: "notice" }, "You have not set up an alert channel yet, so this topic shows in the app only for now. ",
        h("button", { class: "link-btn", onclick: () => { closeDialog(); S.open.add("channels"); location.hash = "#/settings/channels"; } }, "Set up alerts")),
  );
}

function scheduleQuestion(d, redraw) {
  const shared = S.form && S.form.schedule;
  const current = d.schedule ? scheduleId(d.schedule) : "same";
  const options = [
    { id: "same", label: "With my other topics", detail: shared ? scheduleText(shared).replace(/^./, (c) => c.toUpperCase()) : "" },
    // A preset that equals the shared schedule would just repeat the first option.
    ...SCHEDULES.filter((s) => s.id !== "off" && !(shared && s.schedule && scheduleId(shared) === s.id)),
  ];
  return h("div", { class: "q" },
    h("span", { class: "q-label" }, "When should it alert you?"),
    h("div", { class: "options" }, options.map((o) => modeCard("dlg-schedule", o, current === o.id, () => {
      if (o.id === "same") d.schedule = null;
      else if (o.id === "custom") d.schedule = { enabled: true, at: d.schedule && d.schedule.at && d.schedule.at.length ? d.schedule.at.slice() : ["09:00"], days: (d.schedule && d.schedule.days) || WEEKDAYS.slice(), custom: true };
      else d.schedule = { enabled: true, ...clone(o.schedule), days: (o.schedule.days || WEEKDAYS).slice() };
      redraw();
    }))),
    current === "custom" ? customSchedule(d.schedule, redraw, "-dlg") : null,
  );
}

function submitTopic() {
  const D = S.dialog;
  const d = D.draft;
  d.name = d.name.trim();
  const list = D.mode === "wizard" ? wizardState().topics : S.form.topics;
  if (!d.name) { D.error = "Give the topic a name."; renderDialog(); return; }
  if (list.some((t, i) => i !== D.index && t.name.toLowerCase() === d.name.toLowerCase())) { D.error = `${d.name} is already a topic.`; renderDialog(); return; }
  if (d.schedule) delete d.schedule.custom;
  if (D.index === null) list.push(d); else list[D.index] = d;
  // Turning on a search for a topic needs that source switched on.
  if (D.mode !== "wizard") for (const w of WHERE) if (w.source && (d.search || []).includes(w.id)) S.form.sources[w.source] = true;
  const added = D.index === null;
  closeDialog();
  if (D.mode === "wizard") { render(); return; }
  saveConfig().then((ok) => ok && toast(added ? `Added ${d.name}.` : `Saved ${d.name}.`));
}

function removeTopic(index, mode) {
  const list = mode === "wizard" ? wizardState().topics : S.form.topics;
  const [gone] = list.splice(index, 1);
  closeDialog();
  if (mode === "wizard") { render(); return; }
  saveConfig().then((ok) => ok && toast(`Removed ${gone.name}.`));
}

function previewResults(p, title) {
  if (p.loading) return h("div", { class: "preview hint" }, "Looking ...");
  if (p.error) return h("div", { class: "result bad" }, p.error);
  const items = p.hits || p.items || [];
  return h("div", { class: "preview" },
    h("div", { class: "hint" }, p.hits ? `${p.total} matches right now, from the latest scan and recent news.` : `${items.length} items right now.`),
    items.slice(0, 8).map((x, i) => newsItem(i + 1, { ...x, new: false })),
  );
}

function toggleRow(label, help, checked, disabled, onchange) {
  return h("label", { class: "toggle-row" },
    h("span", { class: "toggle-text" }, label, help ? h("span", { class: "hint" }, help) : null),
    h("span", { class: "switch" }, h("input", { type: "checkbox", checked, disabled, onchange: (e) => onchange(e.target.checked) }), h("span", {})),
  );
}

function modLists() {
  const f = S.form;
  const hasKey = S.state.has_key;
  return [
    h("p", { class: "hint" }, "Your topics are matched against these lists on every scan."),
    S.cfg.sources.filter((s) => s.kind === "global").map((s) => {
      const usable = !s.needs_key || hasKey || s.free_fallback;
      const note = usable ? "" : "Needs a Scavio key";
      const preview = S.previews["source:" + s.name];
      return h("div", { class: "list-row" },
        h("div", { class: "row-line" },
          h("span", { class: "toggle-text" }, s.label, note ? h("span", { class: "hint" }, note) : null),
          h("button", { class: "btn btn-light small", disabled: !usable || isRunning(), onclick: () => { S.previews["source:" + s.name] = { loading: true }; render(); startJob("POST", "/api/try", { source: s.name }); } }, "Preview"),
          h("span", { class: "switch" }, h("input", { type: "checkbox", "aria-label": s.label, checked: f.sources[s.name], disabled: !usable, onchange: (e) => { f.sources[s.name] = e.target.checked; changed(true); } }), h("span", {})),
        ),
        preview ? previewResults(preview, s.label) : null,
      );
    }),
  ];
}

function modChannels() {
  return [
    h("p", { class: "hint" }, "Pick a channel to set it up. Each has the steps and a test button."),
    channelTiles((id) => { S.openChannel = S.openChannel === id ? null : id; render(); }, S.openChannel),
    S.openChannel ? channelPanel(S.openChannel, false) : null,
  ];
}

function modeCard(name, opt, current, onpick, type = "radio") {
  return h("label", { class: "option" + (current ? " current" : "") },
    h("input", { type, name, checked: current, onchange: onpick }),
    h("span", { class: "option-text" }, h("b", {}, opt.label), h("span", {}, opt.detail)),
  );
}

function modStyle() {
  const f = S.form;
  return [
    h("div", { class: "options" }, ALERT_STYLES.map((s) => modeCard("style", s, f.alerts.send === s.id, () => { f.alerts.send = s.id; changed(true); }))),
    h("div", { class: "toggle-row plain" },
      h("span", { class: "toggle-text" }, "Breakouts", h("span", { class: "hint" }, "Also send a term trending on this many platforms at once, even if it matches no topic.")),
      h("select", { onchange: (e) => { f.alerts.breakout_platforms = Number(e.target.value); changed(true); } },
        [2, 3, 4, 5].map((n) => h("option", { value: n, selected: f.alerts.breakout_platforms === n }, `${n}+ platforms`)))),
  ];
}

function scheduleId(s) {
  if (!s || s.enabled === false) return "off";
  if (s.custom) return "custom";
  for (const p of SCHEDULES) {
    if (!p.schedule) continue;
    const sameDays = JSON.stringify(p.schedule.days || WEEKDAYS) === JSON.stringify(s.days || WEEKDAYS);
    if (p.schedule.every && s.every === p.schedule.every && sameDays) return p.id;
    if (p.schedule.at && !s.every && JSON.stringify(p.schedule.at) === JSON.stringify(s.at) && sameDays) return p.id;
  }
  return "custom";
}
function applySchedule(p) {
  const f = S.form;
  if (p.id === "off") { f.schedule = { ...f.schedule, enabled: false }; delete f.schedule.custom; return; }
  if (p.id === "custom") {
    f.schedule = { enabled: true, at: f.schedule.at && f.schedule.at.length ? f.schedule.at.slice() : ["08:00"], days: (f.schedule.days || WEEKDAYS).slice(), custom: true };
    return;
  }
  f.schedule = { enabled: true, ...clone(p.schedule), days: (p.schedule.days || WEEKDAYS).slice() };
}

function modSchedule() {
  const f = S.form;
  const id = scheduleId(f.schedule);
  const own = (f.topics || []).filter((t) => t.schedule);
  return [
    h("p", { class: "hint" }, own.length
      ? ["Topics without their own times alert on this schedule. ", `Own times: ${own.map((t) => `${t.name} (${scheduleText(t.schedule)})`).join("; ")}.`]
      : "Every topic alerts on this schedule unless you give it its own times."),
    h("div", { class: "options" }, SCHEDULES.map((s) => modeCard("schedule", s, id === s.id, () => { applySchedule(s); changed(true); }))),
    id === "custom" ? customSchedule(f.schedule) : null,
    weekView(f.schedule),
    backgroundRow(),
  ];
}

/** A Monday-to-Sunday strip from 00 to 24 with a block at every scan time. */
function weekView(s) {
  const times = [];
  if (s.enabled !== false) {
    if (s.every) {
      const m = /^(\d+)([mhd])$/.exec(s.every);
      const step = m ? Number(m[1]) * { m: 1, h: 60, d: 1440 }[m[2]] : 180;
      for (let t = 0; t < 1440; t += step) times.push(t);
    } else for (const a of s.at || []) { const [hh, mm] = a.split(":").map(Number); times.push(hh * 60 + mm); }
  }
  const days = s.days || WEEKDAYS;
  const today = WEEKDAYS[(new Date().getDay() + 6) % 7];
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const pct = (min) => `${(min / 1440) * 100}%`;
  const axis = h("div", { class: "week-axis" }, [0, 4, 8, 12, 16, 20, 24].map((hr) => { const sp = h("span", {}, String(hr).padStart(2, "0")); sp.style.left = pct(hr * 60); return sp; }));
  return h("div", { class: "week" },
    h("div", { class: "week-title" }, "Your week"),
    h("div", { class: "week-grid" },
      h("span", {}), axis,
      WEEKDAYS.map((d) => {
        const lane = h("div", { class: "week-lane" });
        if (days.includes(d)) for (const t of times) {
          const b = h("span", { class: "week-block", title: `${DAY_NAMES[d]} ${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}` });
          b.style.left = pct(t);
          b.style.width = times.length > 8 ? "6px" : "34px";
          lane.appendChild(b);
        }
        if (d === today) { const now = h("span", { class: "week-now" }); now.style.left = pct(nowMin); lane.appendChild(now); }
        return [h("span", { class: "week-day" + (d === today ? " today" : "") }, DAY_NAMES[d]), lane];
      }),
    ),
    h("div", { class: "week-legend" }, h("span", { class: "week-block legend" }), "a scan", h("span", { class: "week-now legend" }), "now"),
  );
}

function customSchedule(s, onchange = () => onchange(), idSuffix = "") {
  if (!s.at) s.at = [];
  const timeInput = h("input", { type: "time", id: "custom-time" + idSuffix, value: "12:00" });
  return h("div", { class: "custom-box" },
    h("div", { class: "field" }, h("span", { class: "label-sm" }, "Times"),
      h("div", { class: "words" }, s.at.map((t, i) => h("span", { class: "chip" }, t, h("button", { class: "x", "aria-label": `Remove ${t}`, onclick: () => { s.at.splice(i, 1); onchange(); } }, icon("x")))),
        timeInput, h("button", { class: "btn btn-green small", onclick: () => { const v = timeInput.value; if (v && !s.at.includes(v)) { s.at.push(v); s.at.sort(); onchange(); } } }, icon("plus"), "Add time"))),
    h("div", { class: "field" }, h("span", { class: "label-sm" }, "Days"),
      h("div", { class: "row wrap" }, WEEKDAYS.map((d) => h("button", { class: "day-btn", "aria-pressed": (s.days || []).includes(d) ? "true" : "false", onclick: () => {
        s.days = (s.days || []).includes(d) ? s.days.filter((x) => x !== d) : WEEKDAYS.filter((x) => (s.days || []).includes(x) || x === d);
        onchange();
      } }, DAY_NAMES[d])))),
  );
}

function backgroundRow() {
  const b = S.background;
  if (!b) return null;
  if (!b.supported) return h("p", { class: "hint" }, "Scans run while TrendScout is open. ", b.detail);
  return [
    toggleRow("Keep checking when TrendScout is closed",
      b.installed ? (b.running ? "On. TrendScout starts when you log in." : "Installed, starting.") : "Off. Scans only run while TrendScout is open.",
      b.installed, false, async (want) => {
        try { S.background = await api("PUT", "/api/background", { on: want }); toast(want ? "TrendScout now keeps checking in the background." : "Background checking is off."); }
        catch (err) { toast(err.message, true); }
        render();
      }),
    b.installed && b.env_only && b.env_only.length ? h("div", { class: "warn-box" }, `Background checks cannot see ${b.env_only.join(", ")} from your shell. Save ${b.env_only.length > 1 ? "them" : "it"} in TrendScout instead.`) : null,
  ];
}

function modRegion() {
  const f = S.form;
  return h("div", { class: "grid-2" },
    h("div", { class: "field" }, h("label", { class: "label-sm", for: "f-country" }, "Country"),
      h("select", { id: "f-country", onchange: (e) => { f.country = e.target.value; changed(true); } },
        [...new Set([...(S.cfg.countries || []), f.country])].map((c) => h("option", { value: c, selected: c === f.country }, regionName(c)))),
      h("span", { class: "hint" }, "Google, Google News and X follow this country.")),
    h("div", { class: "field" }, h("label", { class: "label-sm", for: "f-language" }, "Language"),
      h("input", { type: "text", id: "f-language", value: f.language, maxlength: 5, oninput: (e) => { f.language = e.target.value.trim().toLowerCase(); changed(false); } })),
  );
}
function numField(key, label, min, max, help) {
  const f = S.form;
  return h("div", { class: "field" }, h("label", { class: "label-sm", for: `f-${key}` }, label),
    h("input", { type: "number", id: `f-${key}`, min, max, value: f[key], oninput: (e) => { f[key] = e.target.value === "" ? "" : Number(e.target.value); changed(false); } }),
    help ? h("span", { class: "hint" }, help) : null);
}
function textField(key, label, help, ph) {
  const f = S.form;
  return h("div", { class: "field" }, h("label", { class: "label-sm", for: `f-${key}` }, label),
    h("input", { type: "text", id: `f-${key}`, value: f[key] ?? "", placeholder: ph, oninput: (e) => { f[key] = e.target.value.trim() || (key === "site_url" ? null : ""); changed(false); } }),
    help ? h("span", { class: "hint" }, help) : null);
}
function modAdvanced() {
  return [
    h("div", { class: "grid-2" },
      numField("top_n", "Items per topic and list", 1, 50),
      numField("tiktok_video_calls", "TikTok video calls per scan", 1, 5, "About 6 trending videos each."),
      numField("hackernews_limit", "Hacker News stories", 1, 100),
      numField("keep_days", "Keep scans for (days)", 1, 365),
      textField("data_dir", "History folder"),
      textField("reports_dir", "Reports folder"),
      textField("site_dir", "Web page folder"),
      textField("site_url", "Public report URL", "Optional. Alerts link to it.", "https://you.github.io/trendscout/"),
    ),
    wordsField("Hide on the public page", "Items with these words stay off the web page. Profanity and adult content are already left out.",
      S.form.site_exclude || (S.form.site_exclude = []), () => changed(true), "f-site-exclude"),
    historyTable(),
    h("p", { class: "hint" }, `TrendScout ${S.state.version}. Settings file: `, h("code", {}, S.cfg.path)),
  ];
}
function regionName(code) {
  try { return `${new Intl.DisplayNames(undefined, { type: "region" }).of(code)} (${code})`; } catch { return code; }
}
function historyTable() {
  const rows = S.history || [];
  if (!rows.length) return null;
  return h("div", { class: "history" },
    h("span", { class: "label-sm" }, `Saved scans (${rows.length})`),
    h("table", {}, h("tbody", {}, rows.slice(0, 50).map((s) => h("tr", {},
      h("td", {}, shortFmt.format(new Date(s.taken_at))),
      h("td", { class: "num" }, `${s.items} items`),
      h("td", { class: "num" }, h("button", { class: "btn btn-light small", onclick: () => { S.reportId = s.id === rows[0].id ? null : s.id; S.report = null; location.hash = "#/report"; } }, "Open"),
        h("button", { class: "btn btn-red-text small", disabled: isRunning(), onclick: async (e) => {
          const btn = e.currentTarget;
          if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Delete this scan"; return; }
          try { S.history = await api("DELETE", `/api/snapshots?id=${encodeURIComponent(s.id)}`); S.report = null; toast("Scan deleted."); await refreshState(); render(); } catch (err) { toast(err.message, true); }
        } }, "Delete")),
    )))),
  );
}

// ---- keys (Scavio, AI briefing)

function group(id) { return S.secrets && S.secrets.groups.find((g) => g.id === id); }

function keyGroup(id) {
  const g = group(id);
  const hasDraft = () => g.vars.some((v) => S.drafts[v.name] !== undefined);
  const saveBtn = h("button", { class: "btn btn-blue small", disabled: !hasDraft(), onclick: async () => {
    const values = {};
    for (const v of g.vars) if (S.drafts[v.name] !== undefined) values[v.name] = S.drafts[v.name];
    try {
      S.secrets = await api("PUT", "/api/secrets", { values });
      for (const v of g.vars) { delete S.drafts[v.name]; delete S.replacing[v.name]; }
      S.results[id] = null; toast("Saved to .env.");
      await refreshState(); S.cfg = await api("GET", "/api/config"); render();
    } catch (e) { toast(e.message, true); }
  } }, "Save");
  const test = id === "scavio"
    ? h("button", { class: "btn btn-light small", disabled: !g.enabled, onclick: async () => {
      try { const k = await api("POST", "/api/test/scavio"); S.results.scavio = k.ok ? { ok: true, text: "The key works." } : { ok: false, text: `Did not work: ${k.message}` }; }
      catch (e) { S.results.scavio = { ok: false, text: e.message }; }
      render();
    } }, "Check key")
    : h("button", { class: "btn btn-light small", disabled: !g.enabled || isRunning() || !S.state.latest, onclick: () => { S.results.llm = { text: "Writing ..." }; startJob("POST", "/api/test/llm"); } }, "Try it on the latest scan");
  const r = S.results[id];
  return [
    h("p", { class: "hint" }, id === "scavio" ? ["Unlocks TikTok, YouTube, Reddit and X. Without it, only the free sources run. ", g.enabled ? null : extLink("Get a free key", SCAVIO_URL)] : "Optional. A short summary at the top of each report and alert, from any OpenAI-compatible model."),
    g.vars.map((v) => varField(v, () => { saveBtn.disabled = !hasDraft(); })),
    h("div", { class: "row" }, g.vars.every((v) => v.source === "environment") ? null : saveBtn, test),
    r ? h("div", { class: "result" + (r.ok === true ? " ok" : r.ok === false ? " bad" : "") }, r.pre ? h("pre", {}, r.text) : r.text) : null,
  ];
}

// ---- channels (shared by the wizard and the editor)

function channelTiles(onpick, current) {
  return h("div", { class: "tiles" }, Object.entries(CHANNELS).map(([id, c]) => {
    const g = group(id);
    const on = g && g.enabled;
    return h("button", { class: "tile" + (current === id ? " current" : ""), "aria-expanded": current === id ? "true" : "false", onclick: () => onpick(id) },
      h("span", { class: "tile-text" }, h("b", {}, c.label), h("span", { class: on ? "on" : "" }, on ? "Connected" : "Set up")));
  }));
}

function channelPanel(id, inWizard) {
  if (!S.secrets) { api("GET", "/api/secrets").then((s) => { S.secrets = s; render(); }); return h("p", { class: "hint" }, "Loading ..."); }
  const g = group(id);
  const c = CHANNELS[id];
  const hasDraft = () => g.vars.some((v) => S.drafts[v.name] !== undefined);
  if (id === "ntfy" && !g.vars[0].set && S.drafts.NTFY_TOPIC === undefined) S.drafts.NTFY_TOPIC = randomTopicName();
  const save = async () => {
    const values = {};
    for (const v of g.vars) if (S.drafts[v.name] !== undefined) values[v.name] = S.drafts[v.name];
    try {
      S.secrets = await api("PUT", "/api/secrets", { values });
      for (const v of g.vars) { delete S.drafts[v.name]; delete S.replacing[v.name]; }
      S.results[id] = null; S.alertPreview = null;
      toast(`${c.label} saved.`);
      await refreshState();
      render();
    } catch (e) { toast(e.message, true); }
  };
  const saveBtn = h("button", { class: "btn btn-blue small", disabled: !hasDraft(), onclick: save }, "Save");
  const testBtn = h("button", { class: "btn btn-light small", disabled: !g.enabled || hasDraft() || isRunning(), onclick: () => {
    S.results[id] = { text: "Sending ..." };
    startJob("POST", "/api/test/channel", inWizard ? { channel: id, config: { topics: wizardState().topics } } : { channel: id });
  } }, "Send a test");
  const result = S.results[id];
  return h("div", { class: "channel-panel" },
    h("ol", { class: "steps-list" }, c.steps.map((s) => h("li", {}, stepText(s)))),
    h("div", { class: "field-grid" }, g.vars.filter((v) => !(id === "ntfy" && v.name === "NTFY_SERVER")).map((v) => varField(v, () => { saveBtn.disabled = !hasDraft(); testBtn.disabled = true; }))),
    id === "telegram" ? telegramFinder(g) : null,
    h("div", { class: "row" }, saveBtn, testBtn,
      g.enabled && !inWizard ? h("button", { class: "btn btn-red-text small", onclick: async () => {
        const values = Object.fromEntries(g.vars.filter((v) => v.source === ".env").map((v) => [v.name, ""]));
        try { S.secrets = await api("PUT", "/api/secrets", { values }); toast(`${c.label} turned off.`); await refreshState(); render(); } catch (e) { toast(e.message, true); }
      } }, "Turn off") : null),
    result ? h("div", { class: "result" + (result.ok === true ? " ok" : result.ok === false ? " bad" : "") }, result.text) : null,
  );
}

function telegramFinder(g) {
  if (!g.vars.find((v) => v.name === "TELEGRAM_BOT_TOKEN").set) return null;
  return h("div", { class: "row wrap" },
    h("button", { class: "btn btn-light small", onclick: async () => {
      try { S.tgChats = await api("POST", "/api/telegram/chats"); if (!S.tgChats.length) toast("No messages yet. Send your bot a message in Telegram, then try again.", true); }
      catch (e) { toast(e.message, true); }
      render();
    } }, "Find my chat"),
    (S.tgChats || []).map((ch) => h("button", { class: "chip", onclick: () => { S.drafts.TELEGRAM_CHAT_ID = ch.id; render(); } }, `Use ${ch.name}`)),
  );
}

function varField(v, onDraft) {
  const id = `v-${v.name}`;
  const draft = S.drafts[v.name];
  if (v.source === "environment") {
    return h("div", { class: "field" }, h("span", { class: "label-sm" }, v.label),
      h("div", { class: "secret-set" }, v.secret ? (v.hint ? `Set, ends in ${v.hint.slice(3)}` : "Set") : v.hint, h("span", { class: "hint" }, "from your shell environment")));
  }
  if (v.secret && v.set && !S.replacing[v.name]) {
    return h("div", { class: "field" }, h("span", { class: "label-sm" }, v.label),
      h("div", { class: "secret-set" }, h("span", {}, v.hint ? `Saved, ends in ${v.hint.slice(3)}` : "Saved"),
        h("button", { class: "btn btn-light small", onclick: () => { S.replacing[v.name] = true; render(); } }, "Replace")));
  }
  const original = v.secret ? "" : (v.hint || "");
  const example = v.placeholder && !/^Paste/.test(v.placeholder) ? `e.g. ${v.placeholder}` : v.placeholder;
  const input = h("input", { type: v.secret ? "password" : "text", id, autocomplete: "off", spellcheck: "false", placeholder: example, value: draft !== undefined ? draft : original });
  input.addEventListener("input", () => {
    if (input.value === original && !S.replacing[v.name]) delete S.drafts[v.name]; else S.drafts[v.name] = input.value.trim();
    onDraft();
  });
  return h("div", { class: "field" }, h("label", { class: "label-sm", for: id }, v.label), input);
}

// ------------------------------------------------------------------ setup wizard (same module look)

function wizardState() {
  if (!S.wizard) S.wizard = { step: 0, topics: [], key: "", keyResult: null, channel: null, schedule: "twice", style: "new", background: true, busy: false };
  return S.wizard;
}
function viewWizard() {
  const w = wizardState();
  const steps = ["Topics", "Scavio key", "Alerts", "Schedule"];
  return h("div", { class: "wizard" },
    h("div", { class: "wizard-head" }, h("h1", {}, "Set up TrendScout"), h("p", { class: "hint" }, "You can change all of this later in Settings.")),
    h("ol", { class: "wizard-steps" }, steps.map((s, i) => h("li", { class: "step" + (i < w.step ? " done" : "") + (i === w.step ? " active" : ""), "aria-current": i === w.step ? "step" : null }, h("span", { class: "n" }, String(i + 1)), s))),
    h("section", { class: "module open" },
      h("div", { class: "module-head static" }, h("span", { class: "module-title" }, `${w.step + 1}. ${["What do you want to follow?", "Connect TikTok, YouTube, Reddit and X", "Where should alerts go?", "How often should it check?"][w.step]}`)),
      h("div", { class: "module-body" }, [wizTopics, wizKey, wizAlerts, wizSchedule][w.step]())),
  );
}
function wizNav(nextLabel, onNext, canNext = true) {
  const w = wizardState();
  return h("div", { class: "wiz-nav" },
    w.step > 0 ? h("button", { class: "btn btn-light", onclick: () => { w.step--; render(); } }, "Back") : h("span", {}),
    h("button", { class: "btn btn-blue", disabled: !canNext || w.busy, onclick: onNext }, nextLabel),
  );
}
function wizTopics() {
  const w = wizardState();
  return [
    h("p", { class: "hint" }, "Pick a starter set, add your own, or both."),
    h("div", { class: "options" }, NICHES.map((n, i) => {
      const on = n.topics.every((t) => w.topics.some((x) => x.name === t.name));
      return modeCard("niche-" + i, { label: n.name, detail: n.topics.map((t) => t.name).join(", "), icon: ["bolt", "gift", "star", "sun", "list"][i], tone: ["blue", "orange", "purple", "green", "indigo"][i] }, on, () => {
        if (on) w.topics = w.topics.filter((x) => !n.topics.some((t) => t.name === x.name));
        else for (const t of n.topics) if (!w.topics.some((x) => x.name === t.name)) w.topics.push(newTopic(t));
        render();
      }, "checkbox");
    })),
    h("div", {}, h("button", { class: "btn btn-green", onclick: () => openTopicDialog(null, "wizard") }, icon("plus"), "Add your own topic")),
    w.topics.length ? h("div", { class: "words" }, w.topics.map((t, i) => h("span", { class: "chip" },
      h("button", { class: "chip-name", title: "Edit", onclick: () => openTopicDialog(i, "wizard") }, t.name),
      h("button", { class: "x", "aria-label": `Remove ${t.name}`, onclick: () => { w.topics.splice(i, 1); render(); } }, icon("x"))))) : null,
    wizNav(w.topics.length ? "Continue" : "Choose at least one topic", () => { w.step = 1; render(); }, w.topics.length > 0),
  ];
}

function wizKey() {
  const w = wizardState();
  const input = h("input", { type: "password", id: "wiz-key", placeholder: "Paste your Scavio API key", autocomplete: "off", value: w.key, oninput: (e) => { w.key = e.target.value.trim(); w.keyResult = null; } });
  const check = async () => {
    if (!w.key) return;
    w.busy = true; render();
    try {
      await api("PUT", "/api/secrets", { values: { SCAVIO_API_KEY: w.key } });
      const k = await api("POST", "/api/test/scavio");
      w.keyResult = k.ok ? { ok: true, text: "Connected." } : { ok: false, text: `That key did not work: ${k.message}` };
    } catch (e) { w.keyResult = { ok: false, text: e.message }; }
    w.busy = false; render();
  };
  const envKey = S.state && S.state.has_key && !w.key;
  return [
    h("p", { class: "hint" }, "Those platforms need a Scavio key. Without one, TrendScout still checks Google Trends, Hacker News and the news for free."),
    envKey ? h("div", { class: "result ok" }, "A Scavio key is already set on this computer. You can continue.") : [
      h("div", { class: "add-row" }, input, h("button", { class: "btn btn-blue", onclick: check, disabled: w.busy }, w.busy ? "Checking" : "Check key")),
      w.keyResult ? h("div", { class: "result " + (w.keyResult.ok ? "ok" : "bad") }, w.keyResult.text) : null,
      h("p", { class: "hint" }, "No key yet? ", extLink("Get one free", SCAVIO_URL), "."),
    ],
    wizNav(envKey || (w.keyResult && w.keyResult.ok) ? "Continue" : "Continue without a key", () => { w.step = 2; render(); }),
  ];
}
function wizAlerts() {
  const w = wizardState();
  return [
    h("p", { class: "hint" }, "Pick one now; you can add more later. Or skip and just open TrendScout to see what is new."),
    channelTiles((id) => { w.channel = w.channel === id ? null : id; render(); }, w.channel),
    w.channel ? channelPanel(w.channel, true) : null,
    wizNav(w.channel ? "Continue" : "Skip for now", () => { w.step = 3; render(); }),
  ];
}
function wizSchedule() {
  const w = wizardState();
  return [
    h("div", { class: "options" }, SCHEDULES.filter((s) => s.id !== "custom").map((s) => modeCard("wiz-schedule", s, w.schedule === s.id, () => { w.schedule = s.id; render(); }))),
    h("span", { class: "label-sm" }, "What alerts contain"),
    h("div", { class: "options" }, ALERT_STYLES.map((s) => modeCard("wiz-style", s, w.style === s.id, () => { w.style = s.id; render(); }))),
    S.background && S.background.supported ? toggleRow("Keep checking when TrendScout is closed", "It starts in the background when you log in, so alerts keep coming.", w.background, false, (v) => { w.background = v; }) : null,
    wizNav(w.busy ? "Setting up" : "Start my first scan", finishWizard),
  ];
}
async function finishWizard() {
  const w = wizardState();
  w.busy = true; render();
  const preset = SCHEDULES.find((s) => s.id === w.schedule);
  const config = { topics: w.topics, alerts: { send: w.style }, schedule: w.schedule === "off" ? { enabled: false } : preset.schedule };
  try {
    S.cfg = await api("POST", "/api/init", { config });
    S.form = clone(S.cfg.config);
    if (w.background && S.background && S.background.supported) {
      try { S.background = await api("PUT", "/api/background", { on: true }); }
      catch (e) { toast(`Background checking could not start: ${e.message}`, true); }
    }
    S.wizard = null;
    await refreshState();
    location.hash = "#/report";
    startScan();
  } catch (e) { w.busy = false; toast(friendly(e.message), true); render(); }
}

// ------------------------------------------------------------------ boot

(async function boot() {
  if (!TOKEN) { showLocked(); return; }
  $("#scan-now").addEventListener("click", startScan);
  try { await refreshState(); }
  catch (e) { if (e.status !== 401) $("#view").replaceChildren(h("div", { class: "empty-card" }, h("h1", {}, "TrendScout is not responding"), h("p", {}, e.message))); return; }
  if (!location.hash) history.replaceState(null, "", "#/report");
  if (S.state.has_config) { try { S.cfg = await api("GET", "/api/config"); } catch { /* the view shows the error */ } }
  else { try { S.background = await api("GET", "/api/background"); S.secrets = await api("GET", "/api/secrets"); } catch { /* optional */ } }
  const mod = location.hash.split("/")[2];
  if (mod) S.open.add(mod);
  await render();
  if (mod) { const el = document.getElementById("mod-" + mod); if (el) el.scrollIntoView({ block: "start" }); }
  setInterval(() => { if (!isRunning()) refreshState().catch(() => {}); }, 20000);
})();
