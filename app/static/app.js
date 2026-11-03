"use strict";

const $ = (s, el = document) => el.querySelector(s);
const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
let board = null;
let version = null;
let lastToday = null;
let token = localStorage.getItem("ticklight-token") || "";

// ---------- API ----------
async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401) {
    token = prompt("API token:") || "";
    localStorage.setItem("ticklight-token", token);
    return api(method, path, body);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// ---------- helpers ----------
function parseDate(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
function isoDate(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function daysBetween(a, b) { return Math.round((parseDate(b) - parseDate(a)) / 86400000); }
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function nowHM() { const n = new Date(); return `${String(n.getHours()).padStart(2, "0")}:${String(n.getMinutes()).padStart(2, "0")}`; }

function dayLabel(iso) {
  const diff = daysBetween(board.today_date, iso);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  const d = parseDate(iso);
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
}

// ---------- rendering ----------
function taskEl(t, opts = {}) {
  const li = el("li", `task p${t.priority}`);
  li.dataset.id = t.id;

  const check = el("button", "check");
  check.title = opts.done ? "Undo" : "Complete";
  check.onclick = (e) => { e.stopPropagation(); opts.done ? undoDone(t) : complete(t, li); };
  li.append(check);

  const body = el("div", "body");
  body.append(el("div", "title", t.title));
  const meta = el("div", "meta");
  if (!opts.done) {
    if (t.priority === 2) meta.append(el("span", "chip high", "❗ high"));
    if (t.due_date && opts.showDate) {
      const diff = daysBetween(board.today_date, t.due_date);
      if (diff < 0) meta.append(el("span", "chip overdue", diff === -1 ? "yesterday" : `${-diff} days late`));
      else meta.append(el("span", "chip", dayLabel(t.due_date)));
    }
    if (t.due_time) {
      const late = t.due_date === board.today_date && t.due_time <= nowHM();
      meta.append(el("span", "chip time" + (late ? " late" : ""), "🕑 " + t.due_time));
    }
    if (t.recurrence_text) meta.append(el("span", "chip", "🔁 " + t.recurrence_text));
    if (t.current_streak >= 2) meta.append(el("span", "chip streak", `🔥 ${t.current_streak}`));
    if (t.subtasks && t.subtasks.length) {
      const all = t.subtasks_done === t.subtasks.length;
      meta.append(el("span", "chip subs" + (all ? " all" : ""), `☑ ${t.subtasks_done}/${t.subtasks.length}`));
    }
  } else {
    if (t.skipped) meta.append(el("span", "chip", "skipped"));
    else if (t.streak >= 2 && t.recurrence) meta.append(el("span", "chip streak", `🔥 ${t.streak}`));
  }
  if (meta.childElementCount) body.append(meta);
  if (opts.checklist && !opts.done && t.subtasks && t.subtasks.length) body.append(checklistEl(t));
  body.onclick = () => openEditor(t.id);
  li.append(body);

  if (!opts.done) {
    const acts = el("div", "acts");
    if (t.recurrence) {
      const skip = el("button", "", "⏭");
      skip.title = "Skip this occurrence";
      skip.onclick = (e) => { e.stopPropagation(); action(t, "skip", li, `Skipped “${t.title}”`); };
      acts.append(skip);
    }
    const sub = el("button", "", "☑+");
    sub.title = "Add subtasks";
    sub.onclick = (e) => { e.stopPropagation(); openEditor(t.id, { focusSubs: true }); };
    acts.append(sub);
    const del = el("button", "", "🗑");
    del.title = "Delete";
    del.onclick = (e) => { e.stopPropagation(); remove(t, li); };
    acts.append(del);
    li.append(acts);
  }
  return li;
}

// Inline checklist shown under tasks in the Today/Overdue column
function checklistEl(t) {
  const ul = el("ul", "subs");
  for (const st of t.subtasks) {
    const li = el("li", st.done ? "done" : "");
    const box = el("button", "sub-check", st.done ? "✓" : "");
    box.title = st.done ? "Untick" : "Tick";
    li.append(box, el("span", "", st.title));
    li.onclick = async (e) => {
      e.stopPropagation();
      li.classList.toggle("done");
      await api("PATCH", `/api/subtasks/${st.id}`, { done: !st.done }).catch((err) => alert(err.message));
      await load();
    };
    ul.append(li);
  }
  return ul;
}

function fill(id, tasks, opts) {
  const ul = $("#" + id);
  ul.replaceChildren(...tasks.map((t) => taskEl(t, opts)));
  const c = $(`#${id}-count`);
  if (c) c.textContent = tasks.length || "";
}

function render() {
  const b = board;
  $("#overdue-wrap").hidden = !b.overdue.length;
  fill("overdue", b.overdue, { showDate: true, checklist: true });
  fill("today", b.today, { checklist: true });
  $("#today-empty").hidden = !!(b.today.length || b.overdue.length);

  // upcoming grouped by day
  const up = $("#upcoming");
  up.replaceChildren();
  let cur = null, ul = null;
  for (const t of b.upcoming) {
    if (t.due_date !== cur) {
      cur = t.due_date;
      up.append(el("div", "day-h", dayLabel(cur)));
      ul = el("ul", "tasks compact");
      up.append(ul);
    }
    ul.append(taskEl(t));
  }
  $("#upcoming-empty").hidden = !!b.upcoming.length;
  $("#later-wrap").hidden = !b.later.length;
  fill("later", b.later, { showDate: true });

  fill("anytime", b.anytime, {});
  $("#anytime-empty").hidden = !!b.anytime.length;
  fill("done", b.done_today, { done: true });
  $("#done-count").textContent = b.done_today.length || "";

  const doneN = b.done_today.filter((t) => !t.skipped).length;
  const left = b.today.length + b.overdue.length;
  $("#progress").innerHTML = left
    ? `<b>${doneN}</b> done · ${left} left`
    : doneN ? `<b>All done</b> · ${doneN} today` : "";
}

async function load() {
  try {
    board = await api("GET", "/api/board");
    version = board.version;
    lastToday = board.today_date;
    $("#offline").hidden = true;
    render();
  } catch (e) {
    $("#offline").hidden = false;
  }
}

// Poll a cheap version number; reload when anything changes (e.g. from Telegram)
async function poll() {
  try {
    const v = await api("GET", "/api/version");
    $("#offline").hidden = true;
    if (v.version !== version || v.today !== lastToday) await load();
  } catch {
    $("#offline").hidden = false;
  }
}

// ---------- actions ----------
let toastTimer = null;
function toast(msg, undo) {
  $("#toast-msg").textContent = msg;
  $("#toast").hidden = false;
  $("#toast-undo").hidden = !undo;
  $("#toast-undo").onclick = async () => { $("#toast").hidden = true; await undo(); await load(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 6000);
}

async function action(t, act, li, msg) {
  if (li) li.classList.add("removing");
  try {
    await api("POST", `/api/tasks/${t.id}/${act}`);
    toast(msg, () => api("POST", `/api/tasks/${t.id}/undo`));
  } catch (e) { alert(e.message); }
  await load();
}

function complete(t, li) {
  return action(t, "complete", li, `Done: “${t.title}”`);
}

async function undoDone(t) {
  await api("POST", `/api/tasks/${t.id}/undo`).catch((e) => alert(e.message));
  await load();
}

async function remove(t, li) {
  if (li) li.classList.add("removing");
  await api("DELETE", `/api/tasks/${t.id}`);
  toast(`Deleted “${t.title}”`, () => api("POST", `/api/tasks/${t.id}/restore`));
  await load();
}

// ---------- quick add ----------
const qi = $("#quick-input");
let previewTimer = null;
qi.addEventListener("input", () => {
  clearTimeout(previewTimer);
  const text = qi.value.trim();
  if (!text) { $("#quick-preview").textContent = ""; return; }
  previewTimer = setTimeout(async () => {
    try {
      const p = await api("POST", "/api/parse", { text });
      const pv = $("#quick-preview");
      pv.replaceChildren(el("b", "", p.title));
      if (p.summary) pv.append(" · " + p.summary);
      pv.append("  ↵");
    } catch { /* ignore */ }
  }, 150);
});
$("#quick").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = qi.value.trim();
  if (!text) return;
  try {
    const t = await api("POST", "/api/quick", { text });
    qi.value = "";
    $("#quick-preview").textContent = "";
    toast(`Added “${t.title}”`, () => api("DELETE", `/api/tasks/${t.id}`));
    await load();
  } catch (err) { alert(err.message); }
});

// ---------- editor ----------
const dlg = $("#editor");
const form = $("#edit-form");
let editing = null;

function showRepeatFields() {
  const type = form.rtype.value;
  dlg.querySelectorAll(".r").forEach((n) => { n.hidden = !n.classList.contains("r-" + type); });
  const unit = { daily: "day(s)", weekly: "week(s)", "monthly-day": "month(s)", "monthly-nth": "month(s)" }[type] || "";
  dlg.querySelectorAll(".unit").forEach((n) => (n.textContent = unit));
}
form.rtype.addEventListener("change", showRepeatFields);

function ruleToForm(r, due) {
  const d = due ? parseDate(due) : new Date();
  form.interval.value = (r && r.interval) || 1;
  form.after_days.value = (r && r.days && r.type === "after") ? r.days : 7;
  const weekday = (d.getDay() + 6) % 7;
  const days = r && r.type === "weekly" && r.days && r.days.length ? r.days : [weekday];
  form.querySelectorAll("[name=wd]").forEach((c) => (c.checked = days.includes(Number(c.value))));
  form.mday.value = r && r.day > 0 ? r.day : d.getDate();
  form.mlast.checked = !!(r && r.day === -1);
  form.nth.value = r && r.nth ? r.nth : Math.min(4, Math.ceil(d.getDate() / 7));
  form.nth_wd.value = r && r.weekday != null ? r.weekday : weekday;
  form.ymonth.value = r && r.month ? r.month : d.getMonth() + 1;
  form.yday.value = r && r.type === "yearly" && r.day ? r.day : d.getDate();
  let type = "";
  if (r) type = r.type === "monthly" ? (r.nth != null ? "monthly-nth" : "monthly-day") : r.type;
  form.rtype.value = type;
  showRepeatFields();
}

function formToRule() {
  const type = form.rtype.value;
  const interval = Math.max(1, Number(form.interval.value) || 1);
  switch (type) {
    case "": return null;
    case "daily": return { type, interval };
    case "weekly": return { type, interval, days: [...form.querySelectorAll("[name=wd]:checked")].map((c) => Number(c.value)) };
    case "monthly-day": return { type: "monthly", interval, day: form.mlast.checked ? -1 : Number(form.mday.value) || 1 };
    case "monthly-nth": return { type: "monthly", interval, nth: Number(form.nth.value), weekday: Number(form.nth_wd.value) };
    case "yearly": return { type, month: Number(form.ymonth.value), day: Number(form.yday.value) || 1 };
    case "after": return { type, days: Math.max(1, Number(form.after_days.value) || 1) };
  }
}

// Subtasks in the editor. Existing tasks save each change straight away;
// a new task keeps them in `pendingSubs` and saves them after the task is created.
let editSubs = [];
let pendingSubs = [];

function renderEditSubs() {
  const ul = $("#edit-subs");
  ul.replaceChildren();
  const items = editing ? editSubs : pendingSubs.map((title, i) => ({ id: i, title, done: false }));
  for (const st of items) {
    const li = el("li", st.done ? "done" : "");
    const box = el("button", "sub-check", st.done ? "✓" : "");
    box.type = "button";
    box.disabled = !editing;
    box.onclick = async () => {
      const t = await api("PATCH", `/api/subtasks/${st.id}`, { done: !st.done });
      editSubs = t.subtasks; renderEditSubs(); load();
    };
    const input = el("input");
    input.value = st.title;
    input.onchange = async () => {
      if (!input.value.trim()) return;
      if (editing) { const t = await api("PATCH", `/api/subtasks/${st.id}`, { title: input.value }); editSubs = t.subtasks; load(); }
      else pendingSubs[st.id] = input.value;
    };
    input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); input.blur(); } };
    const del = el("button", "sub-del", "✕");
    del.type = "button";
    del.title = "Remove";
    del.onclick = async () => {
      if (editing) { const t = await api("DELETE", `/api/subtasks/${st.id}`); editSubs = t.subtasks; load(); }
      else pendingSubs.splice(st.id, 1);
      renderEditSubs();
    };
    li.append(box, input, del);
    ul.append(li);
  }
  const done = items.filter((x) => x.done).length;
  $("#subs-legend").textContent = items.length ? `Subtasks ${done}/${items.length}` : "Subtasks";
}

$("#sub-input").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const title = e.target.value.trim();
  if (!title) return;
  e.target.value = "";
  if (editing) {
    const t = await api("POST", `/api/tasks/${editing}/subtasks`, { title }).catch((err) => alert(err.message));
    if (t) { editSubs = t.subtasks; load(); }
  } else pendingSubs.push(title);
  renderEditSubs();
});
// Pasting several lines adds one subtask per line
$("#sub-input").addEventListener("paste", async (e) => {
  const lines = (e.clipboardData.getData("text") || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
  if (lines.length < 2) return;
  e.preventDefault();
  if (editing) { const t = await api("POST", `/api/tasks/${editing}/subtasks`, { titles: lines }); editSubs = t.subtasks; load(); }
  else pendingSubs.push(...lines);
  renderEditSubs();
});

async function openEditor(id, opts = {}) {
  editing = id || null;
  editSubs = [];
  pendingSubs = [];
  form.reset();
  $("#edit-heading").textContent = id ? "Edit task" : "New task";
  $("#edit-delete").hidden = !id;
  $("#edit-stats").hidden = true;
  let t = null;
  if (id) {
    t = await api("GET", `/api/tasks/${id}`);
    form.title.value = t.title;
    form.notes.value = t.notes || "";
    form.due_date.value = t.due_date || "";
    form.due_time.value = t.due_time || "";
    form.priority.value = t.priority;
    editSubs = t.subtasks || [];
    if (t.recurrence) {
      const s = $("#edit-stats");
      s.hidden = false;
      const doneCount = t.history.filter((h) => !h.skipped).length;
      s.replaceChildren(`🔥 Streak ${t.current_streak} · best ${t.best_streak}` + (t.history.length ? ` · recent: ${doneCount} done, ${t.history.length - doneCount} skipped` : ""));
      const hist = el("div", "hist");
      for (const h of [...t.history].reverse()) {
        const sq = el("span", h.skipped ? "skip" : "");
        sq.title = `${h.skipped ? "Skipped" : "Done"} ${h.done_at.slice(0, 16).replace("T", " ")}`;
        hist.append(sq);
      }
      s.append(hist);
    }
  } else {
    form.priority.value = 1;
  }
  ruleToForm(t && t.recurrence, t && t.due_date);
  renderEditSubs();
  $("#subs-hint").hidden = !(t && t.recurrence);
  dlg.showModal();
  (opts.focusSubs ? $("#sub-input") : form.title).focus();
}

$("#edit-cancel").onclick = () => dlg.close();
$("#edit-delete").onclick = async () => {
  const t = await api("GET", `/api/tasks/${editing}`);
  dlg.close();
  await remove(t);
};
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    title: form.title.value,
    notes: form.notes.value,
    priority: Number(form.priority.value),
    due_date: form.due_date.value || null,
    due_time: form.due_time.value || null,
    recurrence: formToRule(),
  };
  try {
    if (editing) await api("PATCH", `/api/tasks/${editing}`, body);
    else {
      const t = await api("POST", "/api/tasks", body);
      if (pendingSubs.length) await api("POST", `/api/tasks/${t.id}/subtasks`, { titles: pendingSubs });
    }
    dlg.close();
    await load();
  } catch (err) { alert(err.message); }
});

// ---------- bulk add ----------
const bulkDlg = $("#bulk");
let bulkTimer = null;
let bulkCount = 0;

function openBulk(text = "") {
  $("#bulk-text").value = text;
  $("#bulk-append").value = "";
  bulkDlg.showModal();
  const ta = $("#bulk-text");
  ta.focus();
  ta.setSelectionRange(ta.value.length, ta.value.length);
  bulkPreview();
}

function bulkPreview() {
  clearTimeout(bulkTimer);
  bulkTimer = setTimeout(async () => {
    const text = $("#bulk-text").value;
    let res = { count: 0, tasks: [] };
    if (text.trim()) {
      try {
        res = await api("POST", "/api/bulk", { text, append: $("#bulk-append").value, dry_run: true });
      } catch (e) {
        $("#bulk-preview").replaceChildren(el("li", "", e.message));
        $("#bulk-save").disabled = true;
        return;
      }
    }
    bulkCount = res.count;
    const ol = $("#bulk-preview");
    ol.replaceChildren(...res.tasks.map((t) => {
      const li = el("li");
      li.append(el("span", "bp-title p" + t.priority, t.title));
      if (t.summary) li.append(el("div", "bp-meta", t.summary));
      if (t.subtasks.length) {
        const ul = el("ul");
        t.subtasks.forEach((st) => ul.append(el("li", "", st)));
        li.append(ul);
      }
      return li;
    }));
    $("#bulk-empty").hidden = res.count > 0;
    $("#bulk-save").disabled = res.count === 0;
    $("#bulk-save").textContent = res.count ? `Add ${res.count} task${res.count === 1 ? "" : "s"}` : "Add tasks";
  }, 200);
}

$("#bulk-text").addEventListener("input", bulkPreview);
$("#bulk-append").addEventListener("input", bulkPreview);
// Tab indents (to make a subtask) instead of leaving the textarea
$("#bulk-text").addEventListener("keydown", (e) => {
  const ta = e.target;
  if (e.key !== "Tab") return;
  e.preventDefault();
  const start = ta.value.lastIndexOf("\n", ta.selectionStart - 1) + 1;
  if (e.shiftKey) {
    if (ta.value.slice(start, start + 2) === "  ") ta.setRangeText("", start, start + 2, "end");
  } else ta.setRangeText("  ", start, start, "end");
  bulkPreview();
});
bulkDlg.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#bulk-form").requestSubmit(); }
});
$("#bulk-cancel").onclick = () => bulkDlg.close();
$("#bulk-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!bulkCount) return;
  $("#bulk-save").disabled = true;
  try {
    const res = await api("POST", "/api/bulk", { text: $("#bulk-text").value, append: $("#bulk-append").value });
    bulkDlg.close();
    const ids = res.tasks.map((t) => t.id);
    toast(`Added ${res.count} task${res.count === 1 ? "" : "s"}`,
      () => Promise.all(ids.map((id) => api("DELETE", `/api/tasks/${id}`))));
    await load();
  } catch (err) {
    alert(err.message);
    $("#bulk-save").disabled = false;
  }
});
$("#btn-bulk").onclick = () => openBulk();

// Pasting several lines into quick add opens the bulk dialog
qi.addEventListener("paste", (e) => {
  const text = e.clipboardData.getData("text") || "";
  if (!text.trim().includes("\n")) return;
  e.preventDefault();
  const before = qi.value.trim();
  qi.value = "";
  $("#quick-preview").textContent = "";
  openBulk((before ? before + "\n" : "") + text.replace(/\s+$/, ""));
});

// ---------- display mode & shortcuts ----------
function setDisplay(on) {
  document.body.classList.toggle("display", on);
  localStorage.setItem("ticklight-display", on ? "1" : "");
}
// Light/dark: follows the system until you pick one, then remembers your choice
const themeQuery = matchMedia("(prefers-color-scheme: light)");
function applyTheme() {
  const saved = localStorage.getItem("ticklight-theme");
  const light = saved ? saved === "light" : themeQuery.matches;
  document.documentElement.classList.toggle("light", light);
  $("#btn-theme").textContent = light ? "☾" : "☀";
  $("#btn-theme").title = `Switch to ${light ? "dark" : "light"} mode (t)`;
}
function toggleTheme() {
  const light = !document.documentElement.classList.contains("light");
  localStorage.setItem("ticklight-theme", light ? "light" : "dark");
  applyTheme();
}
themeQuery.addEventListener("change", applyTheme);
$("#btn-theme").onclick = toggleTheme;
applyTheme();

$("#btn-display").onclick = () => setDisplay(!document.body.classList.contains("display"));
$("#btn-new").onclick = () => openEditor();
setDisplay(!!localStorage.getItem("ticklight-display") || new URLSearchParams(location.search).has("display"));

document.addEventListener("keydown", (e) => {
  if (dlg.open || bulkDlg.open || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    if (e.key === "Escape" && document.activeElement === qi) { qi.value = ""; qi.blur(); $("#quick-preview").textContent = ""; }
    return;
  }
  if (e.key === "/") { e.preventDefault(); setDisplay(false); qi.focus(); }
  else if (e.key === "n") { e.preventDefault(); openEditor(); }
  else if (e.key === "t") toggleTheme();
  else if (e.key === "b") { e.preventDefault(); openBulk(); }
  else if (e.key === "d") setDisplay(!document.body.classList.contains("display"));
});

// ---------- clock ----------
function tick() {
  const n = new Date();
  $("#time").textContent = n.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("#date").textContent = n.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}
setInterval(tick, 1000);
tick();

// Re-render every minute so "late" times turn red without a data change
setInterval(() => board && render(), 60000);
setInterval(poll, 3000);
load();
