// Gestures page of the settings window: what each gesture does, and recording your own.
// Inlined into settings_ui.html by settings_panel.py; uses its h(), api(), call(), change(),
// toast(), seg(), settingRow(), section(), head(), groupButtons() and `state`.
"use strict";

const KIND_NAMES = { builtin: "Built-in action", keys: "Keyboard shortcut", open: "Open app or website",
                     shortcut: "Apple Shortcut", shell: "Terminal command", none: "Nothing" };
const MOD_KEYS = new Set(["MetaLeft", "MetaRight", "ControlLeft", "ControlRight", "AltLeft",
                          "AltRight", "ShiftLeft", "ShiftRight", "CapsLock", "Fn"]);
const MOD_SYMBOL = { ctrl: "⌃", alt: "⌥", shift: "⇧", cmd: "⌘" };
const POLL_MS = 200;

// Page-local state, kept across re-renders (state pushes re-render the page).
const gx = {
  editing: null,      // { target: "shape" | "custom", id } of the open action editor
  renaming: null,     // id of the gesture whose name is being edited
  confirmDelete: null,
  draft: null,        // { kind, value } being edited
  shortcuts: null,    // Apple Shortcuts names, loaded once
  rec: "idle",        // idle | running | review
  recFor: "",         // id of the gesture being re-recorded, "" for a new one
  recName: "",
  live: null,         // last gesture_live reply
  timer: null,
  testing: false,     // a Test of a program action is running; its result comes by polling
};

function gState() { return state.gestures; }
function keyLabel(code) {
  if (code.startsWith("Key")) return code.slice(3);
  if (code.startsWith("Digit")) return code.slice(5);
  return { Space: "Space", Enter: "↩", Tab: "⇥", Backspace: "⌫", Escape: "Esc", ArrowLeft: "←",
           ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓" }[code] || code;
}
function keysText(v) {
  if (!v || !v.code) return "";
  return ["ctrl", "alt", "shift", "cmd"].filter((m) => v.modifiers.includes(m)).map((m) => MOD_SYMBOL[m]).join("")
    + keyLabel(v.code);
}
function sameAction(a, b) {
  if (a.kind !== b.kind) return false;
  if (a.kind !== "keys") return a.value === b.value;
  return a.value.code === b.value.code && [...a.value.modifiers].sort().join() === [...b.value.modifiers].sort().join();
}
function presetFor(action) {
  return gState().actions.presets.find((p) => p.action.kind !== "builtin" && sameAction(p.action, action));
}
function actionText(action) {
  const preset = action.kind === "keys" && presetFor(action);
  return preset ? `${preset.group} · ${preset.label} (${keysText(action.value)})` : action.describe;
}
function presetPicker() {
  const groups = [...new Set(gState().actions.presets.map((p) => p.group))];
  return h("select", { class: "select", "aria-label": "Ready-made actions",
    onchange: (e) => {
      const p = gState().actions.presets[Number(e.target.value)];
      if (p) { gx.draft = copyAction(p.action); render(); }
    } },
    h("option", { value: "" }, "Presets…"),
    groups.map((g) => h("optgroup", { label: g }, gState().actions.presets
      .map((p, i) => [p, i]).filter(([p]) => p.group === g)
      .map(([p, i]) => h("option", { value: String(i) }, p.label)))));
}
function copyAction(a) { return { kind: a.kind, value: a.kind === "keys" ? { ...a.value, modifiers: [...a.value.modifiers] } : a.value }; }
function blankValue(kind) {
  const g = gState();
  if (kind === "builtin") return Object.keys(g.actions.builtins)[0];
  if (kind === "keys") return { code: "", modifiers: [] };
  return "";
}

// ---------- the action editor (shared by built-in shapes, your gestures, new recordings) ----------
function actionEditor(onSave, saveLabel = "Save") {
  const g = gState();
  const d = gx.draft;
  const kinds = g.actions.kinds.filter((k) => k !== "shell" || g.allow_shell || d.kind === "shell");
  const kindSelect = h("select", { class: "select", "aria-label": "What it does",
    onchange: (e) => { gx.draft = { kind: e.target.value, value: blankValue(e.target.value) }; render(); } },
    kinds.map((k) => h("option", { value: k, selected: k === d.kind }, KIND_NAMES[k])));
  return h("div", { class: "editor" },
    h("div", { class: "editor-row" }, presetPicker(),
      h("span", { class: "detail" }, "for Chrome, Zoom, Meet, Teams, YouTube, or pick your own:")),
    h("div", { class: "editor-row" }, kindSelect, valueInput(d)),
    d.kind === "keys" ? h("div", { class: "hint" }, "Shortcuts go to the app in front, so click into your meeting or browser window first.") : null,
    d.kind === "keys" ? h("div", { class: "hint" }, "Click the box, then press the keys. ⌘Q, ⌘W and ⌘, belong to Handsight itself.") : null,
    d.kind === "shell" ? h("div", { class: "hint" }, "Runs in zsh as you, for up to 30 seconds. Its output goes to the activity log.") : null,
    h("div", { class: "editor-actions" },
      h("button", { class: "btn", onclick: testDraft }, "Test"),
      h("span", { class: "spacer" }),
      h("button", { class: "btn", onclick: closeEditor }, "Cancel"),
      h("button", { class: "btn primary", onclick: () => onSave(gx.draft) }, saveLabel)));
}

function valueInput(d) {
  const g = gState();
  if (d.kind === "builtin") {
    return h("select", { class: "select grow", "aria-label": "Action",
      onchange: (e) => { d.value = e.target.value; } },
      Object.entries(g.actions.builtins).map(([k, label]) => h("option", { value: k, selected: k === d.value }, label)));
  }
  if (d.kind === "keys") {
    const box = h("button", { class: "keycap grow", "aria-label": "Press the shortcut keys",
      onkeydown: (e) => {
        e.preventDefault(); e.stopPropagation();
        if (MOD_KEYS.has(e.code)) return;
        const mods = [];
        if (e.metaKey) mods.push("cmd");
        if (e.ctrlKey) mods.push("ctrl");
        if (e.altKey) mods.push("alt");
        if (e.shiftKey) mods.push("shift");
        if (!g.actions.key_codes.includes(e.code)) { toast("That key can't be used", true); return; }
        d.value = { code: e.code, modifiers: mods };
        box.textContent = keysText(d.value);
      } }, keysText(d.value) || "Click, then press keys");
    return box;
  }
  if (d.kind === "open") {
    const input = h("input", { type: "text", class: "text-input grow", value: d.value || "",
      placeholder: "https://… or Choose an app", spellcheck: "false",
      oninput: (e) => { d.value = e.target.value; } });
    return h("div", { class: "editor-row grow" }, input,
      h("button", { class: "btn", onclick: async () => {
        const r = await api("choose_app");
        if (r && r.path) { d.value = r.path; input.value = r.path; }
      } }, "Choose app…"));
  }
  if (d.kind === "shortcut") {
    if (gx.shortcuts === null) loadShortcuts();
    if (gx.shortcuts.length) {
      if (!d.value) d.value = gx.shortcuts[0];
      return h("select", { class: "select grow", "aria-label": "Shortcut",
        onchange: (e) => { d.value = e.target.value; } },
        gx.shortcuts.map((name) => h("option", { value: name, selected: name === d.value }, name)));
    }
    return h("input", { type: "text", class: "text-input grow", value: d.value || "",
      placeholder: "Shortcut name, exactly as in the Shortcuts app", oninput: (e) => { d.value = e.target.value; } });
  }
  if (d.kind === "shell") {
    return h("input", { type: "text", class: "text-input grow mono", value: d.value || "",
      placeholder: "say \"hello\"", spellcheck: "false", oninput: (e) => { d.value = e.target.value; } });
  }
  return h("span", { class: "detail grow" }, "This gesture does nothing.");
}

function loadShortcuts(tries = 0) {
  gx.shortcuts = gx.shortcuts || [];
  api("list_shortcuts").then((r) => {
    if (r && r.loading && tries < 20) { setTimeout(() => loadShortcuts(tries + 1), 500); return; }
    gx.shortcuts = (r && r.shortcuts) || [];
    if (gx.shortcuts.length) render();
  });
}

function openEditor(target, id, action) {
  gx.editing = { target, id };
  gx.draft = copyAction(action);
  render();
}
function closeEditor() {
  gx.editing = null; gx.draft = null;
  if (gx.rec === "review") gx.rec = "idle";
  render();
}
async function testDraft() {
  const r = await api("test_action", gx.draft);
  if (!r || !r.ok) { toast(r ? r.error : "No reply", true); return; }
  toast(r.message);
  if (r.pending) { gx.testing = true; startPolling(); }  // the result arrives via gesture_live
}
function showTestResult(text) {
  gx.testing = false;
  toast(text, /^(failed|gave up|could not)/.test(text));
}
async function saveFor(method, id) {
  const r = await call(method, id, gx.draft);
  if (r && r.ok) { gx.editing = null; gx.draft = null; render(); toast("Saved"); }
}

// ---------- rows ----------
function gestureRow(glyph, name, action, target, id, extraButtons = []) {
  const open = gx.editing && gx.editing.target === target && gx.editing.id === id;
  const row = h("div", { class: "row" + (action.kind === "none" ? " dim-text" : "") },
    h("div", { class: "glyph" }, glyph),
    h("div", { class: "text" }, h("div", { class: "title" }, name),
      h("div", { class: "detail" }, actionText(action))),
    extraButtons,
    h("button", { class: "btn", "aria-expanded": String(open),
      onclick: () => (open ? closeEditor() : openEditor(target, id, action)) }, open ? "Close" : "Change"));
  if (!open) return row;
  return [row, h("div", { class: "sub" }, actionEditor(() =>
    saveFor(target === "shape" ? "set_binding" : "set_custom_action", id)))];
}

function customRow(g) {
  // Renaming happens inline (browser dialogs would block the app).
  const rename = h("button", { class: "btn", onclick: () => { gx.renaming = g.id; render(); } }, "Rename");
  const rerecord = h("button", { class: "btn", onclick: () => startRecording(g.id, g.name) }, "Re-record");
  const del = h("button", { class: "btn danger", onclick: () => {
    if (gx.confirmDelete === g.id) { gx.confirmDelete = null; call("delete_custom", g.id).then((r) => r && r.ok && toast("Deleted")); }
    else { gx.confirmDelete = g.id; render(); setTimeout(() => { if (gx.confirmDelete === g.id) { gx.confirmDelete = null; render(); } }, 3000); }
  } }, gx.confirmDelete === g.id ? "Really delete?" : "Delete");
  if (gx.renaming === g.id) {
    const input = h("input", { type: "text", class: "text-input grow", value: g.name, maxlength: "30" });
    const save = () => call("rename_custom", g.id, input.value).then((r) => { if (r && r.ok) { gx.renaming = null; render(); } });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { gx.renaming = null; render(); } });
    setTimeout(() => input.focus(), 0);
    return h("div", { class: "row" }, h("div", { class: "glyph" }, "✋"), input,
      h("button", { class: "btn", onclick: () => { gx.renaming = null; render(); } }, "Cancel"),
      h("button", { class: "btn primary", onclick: save }, "Save"));
  }
  return gestureRow("✋", g.name, g.action, "custom", g.id, [rename, rerecord, del]);
}

// ---------- recording ----------
async function startRecording(replaceId = "", name = "") {
  const r = await api("start_recording");
  if (!r || !r.ok) { toast(r ? r.error : "No reply", true); return; }
  gx.rec = "running"; gx.recFor = replaceId; gx.recName = name; gx.live = r.live;
  gx.editing = null;
  render();
}
async function cancelRecording() {
  await api("cancel_recording");
  gx.rec = "idle"; gx.editing = null; gx.draft = null;
  render();
}
async function saveRecording() {
  const r = await call("save_recording", gx.recName.trim(), gx.draft, gx.recFor);
  if (r && r.ok) {
    gx.rec = "idle"; gx.editing = null; gx.draft = null;
    render();
    toast(r.warning || "Gesture saved", Boolean(r.warning));
  }
}

function recordingCard() {
  const live = gx.live && gx.live.recording;
  if (gx.rec === "running") {
    let title = "Starting…", detail = "", share = 0;
    if (live && live.state === "countdown") {
      title = `Get ready… ${live.seconds}`;
      detail = "Hold your new gesture up in front of the camera, near your face.";
    } else if (live && live.state === "capturing") {
      title = "Recording, keep it still";
      detail = `${live.samples} frames so far`;
      share = live.progress;
    }
    return h("div", { class: "card rec-card" },
      h("div", { class: "row" }, h("div", { class: "rec-dot" }),
        h("div", { class: "text" }, h("div", { class: "title", id: "rec-title" }, title),
          h("div", { class: "detail", id: "rec-detail" }, detail)),
        h("button", { class: "btn", onclick: cancelRecording }, "Cancel")),
      h("div", { class: "meter", style: "margin:0 16px 14px" }, h("i", { id: "rec-meter", style: `width:${share * 100}%` })));
  }
  if (gx.rec === "review") {
    if (!gx.draft) gx.draft = { kind: "none", value: "" };
    const name = h("input", { type: "text", class: "text-input grow", value: gx.recName, maxlength: "30",
      placeholder: "Name, e.g. Rock on", oninput: (e) => { gx.recName = e.target.value; } });
    return h("div", { class: "card rec-card" },
      h("div", { class: "row" }, h("div", { class: "glyph" }, "✅"),
        h("div", { class: "text" }, h("div", { class: "title" }, gx.recFor ? "New recording ready" : "Got it! Name it and pick what it does"),
          h("div", { class: "detail" }, `${(live && live.samples) || ""} frames recorded`))),
      h("div", { class: "sub" }, h("div", { class: "editor-row", style: "margin-bottom:8px" }, name),
        actionEditor(saveRecording, gx.recFor ? "Save new recording" : "Save gesture")),
      h("div", { class: "sub" }, h("button", { class: "btn", onclick: cancelRecording }, "Discard"),
        " ", h("button", { class: "btn", onclick: () => startRecording(gx.recFor, gx.recName) }, "Record again")));
  }
  return null;
}

function updateLive(reply) {
  gx.live = reply && reply.live;
  if (!gx.live) return;
  if (gx.live.test) showTestResult(gx.live.test);
  const seen = document.getElementById("seen");
  if (seen) seen.textContent = gx.live.seen || "nothing";
  if (gx.rec !== "running") return;
  const r = gx.live.recording;
  if (r.state === "done") {
    gx.rec = "review";
    const existing = gState().custom.find((c) => c.id === gx.recFor);
    gx.draft = existing ? copyAction(existing.action) : { kind: "none", value: "" };
    render();
  } else if (r.state === "failed") {
    gx.rec = "idle"; render(); toast(r.error, true);
  } else if (r.state === "idle") {
    gx.rec = "idle"; render();
  } else {
    const title = document.getElementById("rec-title");
    const detail = document.getElementById("rec-detail");
    const meter = document.getElementById("rec-meter");
    if (r.state === "countdown" && title) { title.textContent = `Get ready… ${r.seconds}`; }
    if (r.state === "capturing" && title) {
      title.textContent = "Recording, keep it still";
      detail.textContent = `${r.samples} frames so far`;
      meter.style.width = `${r.progress * 100}%`;
    }
  }
}

function startPolling() {
  if (gx.timer) return;
  gx.timer = setInterval(() => {
    if (page !== "gestures" && !gx.testing) { clearInterval(gx.timer); gx.timer = null; return; }
    api("gesture_live").then(updateLive);
  }, POLL_MS);
}

// ---------- the page ----------
PAGES.gestures = function gesturesPage() {
  const sec = section("gestures");
  const g = gState();
  const [master, ...modes] = sec.items;
  const off = !state.flags.gestures;
  const watching = state.status === "watching";
  startPolling();
  const canRecord = watching && !off && g.custom.length < g.max_custom && gx.rec === "idle";
  return h("div", { class: "page" + (off ? " gestures-off" : "") }, head(sec, groupButtons("gestures")),
    h("div", { class: "card" }, settingRow(master)),
    h("div", { class: "live" }, h("span", { class: "detail" }, "Seeing right now: "),
      h("strong", { id: "seen" }, watching && !off ? ((gx.live && gx.live.seen) || "nothing") : "camera off")),

    h("div", { class: "label label-row" }, h("span", {}, "Built-in gestures"),
      h("button", { class: "link", onclick: () => call("reset_bindings").then((r) => r && r.ok && toast("Back to the defaults")) }, "Reset to defaults")),
    h("div", { class: "card dimmable" }, g.shapes.map((s) => gestureRow(s.glyph, s.name, s.action, "shape", s.id))),

    h("div", { class: "label" }, `My gestures · ${g.custom.length} of ${g.max_custom}`),
    recordingCard(),
    h("div", { class: "card dimmable" },
      g.custom.map(customRow),
      h("div", { class: "row" }, h("div", { class: "glyph" }, "＋"),
        h("div", { class: "text" }, h("div", { class: "title" }, "Record a new gesture"),
          h("div", { class: "detail" }, watching ? "Hold any hand shape still for 2 seconds. Handsight learns it from your hand, on this Mac."
                                                 : "Start watching first: recording uses the camera.")),
        h("button", { class: "btn primary", disabled: !canRecord, onclick: () => startRecording() }, "Record"))),

    h("div", { class: "label" }, "Scrolling and the pointer"),
    h("div", { class: "card" }, modes.map((i) => settingRow(i, off))),

    h("div", { class: "label" }, "Hold time"),
    h("div", { class: "card dimmable", style: "padding:14px 16px" },
      h("div", { class: "seg-row" },
        h("span", { class: "detail" }, "How long to hold a gesture before it fires"),
        seg(Object.entries(state.sensitivity_levels).map(([k, s]) => [k, `${SENSITIVITY_NAMES[k] || k} · ${s}s`]),
            state.sensitivity, (k) => change("set_sensitivity", k))),
      h("div", { class: "hint" }, "Careful avoids accidental triggers. A fist always needs at least 0.8 s.")),

    h("div", { class: "label" }, "Advanced"),
    h("div", { class: "card" }, h("div", { class: "row" },
      h("div", { class: "glyph" }, "⌨️"),
      h("div", { class: "text" }, h("div", { class: "title" }, "Allow Terminal commands"),
        h("div", { class: "detail" }, "Lets a gesture run a command line. Only turn this on for commands you wrote or understand.")),
      sw(g.allow_shell, (v) => change("set_allow_shell", v), false, "Allow Terminal commands"))));
};
