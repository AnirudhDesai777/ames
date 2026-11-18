import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parseHTML } from "linkedom";
import { mountConversation } from "../src/ui.ts";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");

function setup({ token = "test-token", seen = true, storage, browser, pending = false, error } = {}) {
  const { document } = parseHTML(html);
  const values = new Map(seen ? [["ames-advisory-seen", "true"]] : []);
  storage ??= { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const sessions = [];
  const connect = (credential, audio, receive, disconnected) => {
    let resolve;
    let reject;
    const ready = new Promise((yes, no) => { resolve = yes; reject = no; });
    const session = { ready, close: test.mock.fn(), setMuted: test.mock.fn(), setVoice: test.mock.fn(), sendText: test.mock.fn(), receive, disconnected, resolve, reject, credential, audio };
    sessions.push(session);
    if (error) reject(error);
    else if (!pending) resolve();
    return session;
  };
  const dispose = mountConversation(document, token, browser ?? { localStorage: storage }, connect);
  const element = (id) => document.getElementById(id);
  return { document, element, sessions, values, dispose,
    click: (id) => element(id).click(),
    receive: (event) => sessions.at(-1).receive(event),
  };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

async function connected(options) {
  const ui = setup(options);
  ui.click("connect");
  await tick();
  return ui;
}

test("first-run advisory blocks a conversation until acknowledged and persists acknowledgement", async () => {
  const ui = setup({ seen: false });
  assert.equal(ui.element("advisory").hidden, false);
  assert.match(ui.element("advisory").textContent, /AI with no cloud monitoring/);
  assert.match(ui.element("advisory").textContent, /Nobody can guarantee/);
  assert.match(ui.element("advisory").textContent, /grain of salt/);
  assert.equal(ui.element("connect").disabled, true);
  ui.click("connect");
  assert.equal(ui.sessions.length, 0);
  ui.click("acknowledge");
  assert.equal(ui.element("advisory").hidden, true);
  assert.equal(ui.values.get("ames-advisory-seen"), "true");
  ui.click("connect");
  await tick();
  assert.equal(ui.sessions.length, 1);
  assert.equal(ui.sessions[0].credential, "test-token");
  assert.equal(ui.sessions[0].audio, ui.element("audio"));
  assert.equal(setup({ storage: { getItem: (key) => ui.values.get(key) } }).element("advisory").hidden, true);
});

test("unavailable localStorage still requires acknowledgement for this visit", async () => {
  const ui = setup({ storage: { getItem() { throw Error("disabled"); }, setItem() { throw Error("disabled"); } } });
  assert.equal(ui.element("advisory").hidden, false);
  ui.click("acknowledge");
  ui.click("connect");
  await tick();
  assert.equal(ui.sessions.length, 1);
});

test("a blocked localStorage property still mounts and keeps acknowledgement for this visit", async () => {
  const browser = { get localStorage() { throw new DOMException("Site data blocked", "SecurityError"); } };
  const ui = setup({ browser });
  assert.equal(ui.element("advisory").hidden, false);
  assert.equal(ui.element("connect").disabled, true);
  ui.click("acknowledge");
  assert.equal(ui.element("advisory").hidden, true);
  ui.click("connect");
  await tick();
  ui.click("end");
  ui.click("connect");
  await tick();
  assert.equal(ui.sessions.length, 2);
  assert.equal(ui.element("advisory").hidden, true);
  assert.equal(setup({ browser }).element("advisory").hidden, false);
});

test("missing token keeps connect disabled and shows server-link instructions", () => {
  const ui = setup({ token: null, seen: false });
  ui.click("acknowledge");
  ui.click("connect");
  assert.equal(ui.element("connect").disabled, true);
  assert.equal(ui.sessions.length, 0);
  assert.match(ui.element("status").textContent, /link from the server/);
});

test("orb reflects every speaker with an accessible text equivalent", async () => {
  const ui = await connected();
  for (const who of ["user", "ames", "none"]) {
    ui.receive({ type: "speaking", who });
    assert.equal(ui.element("orb").dataset.speaking, who);
    assert.match(ui.element("presence").textContent, who === "ames" ? /Ames is speaking/ : who === "user" ? /You're speaking/ : /Listening/);
  }
  assert.equal(ui.element("orb").getAttribute("aria-label"), "Listening");
});

test("transcript updates partials in place, separates speakers and turns, and renders text safely", async () => {
  const ui = await connected();
  assert.equal(ui.element("transcript-empty").hidden, false);
  ui.receive({ type: "transcript", role: "user", text: "Hel", final: false, turn_id: 1 });
  ui.receive({ type: "transcript", role: "user", text: "Hello", final: true, turn_id: 1 });
  ui.receive({ type: "transcript", role: "user", text: "late partial", final: false, turn_id: 1 });
  ui.receive({ type: "transcript", role: "ames", text: '<img src=x onerror="bad()">', final: true, turn_id: 1 });
  ui.receive({ type: "transcript", role: "user", text: "Next turn", final: true, turn_id: 2 });
  const rows = ui.element("transcript").querySelectorAll("li");
  assert.equal(rows.length, 3);
  assert.equal(rows[0].querySelector("p").textContent, "Hello");
  assert.equal(rows[0].dataset.final, "true");
  assert.equal(rows[0].querySelector("span").textContent, "You");
  assert.equal(rows[1].querySelector("span").textContent, "Ames");
  assert.equal(rows[1].querySelector("p").textContent, '<img src=x onerror="bad()">');
  assert.equal(ui.element("transcript").querySelector("img"), null);
  assert.equal(ui.element("transcript-empty").hidden, true);
  assert.equal(ui.element("transcript").getAttribute("role"), "log");
});

test("mute and unmute control only local microphone sending", async () => {
  const ui = await connected();
  assert.equal(ui.element("mute").disabled, false);
  ui.click("mute");
  assert.equal(ui.element("mute").getAttribute("aria-pressed"), "true");
  assert.equal(ui.element("mute").textContent, "Unmute microphone");
  ui.click("mute");
  assert.equal(ui.element("mute").getAttribute("aria-pressed"), "false");
  assert.deepEqual(ui.sessions[0].setMuted.mock.calls.map((call) => call.arguments), [[true], [false]]);
  assert.equal(ui.sessions.length, 1);
});

test("end closes locally, resets controls and preserves the transcript until a new conversation", async () => {
  const ui = await connected();
  ui.receive({ type: "transcript", role: "ames", text: "Hello", final: true, turn_id: 1 });
  ui.click("mute");
  ui.click("end");
  assert.equal(ui.sessions[0].close.mock.callCount(), 1);
  assert.equal(ui.element("mute").disabled, true);
  assert.equal(ui.element("end").disabled, true);
  assert.equal(ui.element("mute").getAttribute("aria-pressed"), "false");
  assert.equal(ui.element("connect").disabled, false);
  assert.match(ui.element("transcript").textContent, /Hello/);
  ui.click("connect");
  await tick();
  assert.equal(ui.element("transcript").children.length, 0);
  assert.equal(ui.element("transcript-empty").hidden, false);
});

for (const control of ["end", "mute"]) {
  test(`focus moves to End on connect and back to Start when finishing from ${control}`, async (t) => {
    const ui = setup({ pending: true });
    let focused;
    for (const id of ["connect", "end", "mute"]) {
      t.mock.method(ui.element(id), "focus", function () {
        assert.equal(this.disabled, false);
        focused = this;
      });
    }
    ui.element("connect").focus();
    ui.click("connect");
    assert.equal(focused, ui.element("end"));
    ui.sessions[0].resolve();
    await tick();
    assert.equal(focused, ui.element("end"));
    ui.element(control).focus();
    if (control === "end") ui.click("end");
    else ui.receive({ type: "notice", code: "session_ended", text: "Session ended." });
    assert.equal(focused, ui.element("connect"));
  });
}

for (const code of ["session_replaced", "session_ended"]) {
  test(`${code} notice stays visible after disconnect and late setup completion`, async () => {
    const ui = setup({ pending: true });
    ui.click("connect");
    ui.receive({ type: "notice", code, text: `${code} message <b>plain text</b>` });
    ui.sessions[0].disconnected();
    ui.sessions[0].resolve();
    await tick();
    assert.equal(ui.element("notice").textContent, `${code} message <b>plain text</b>`);
    assert.equal(ui.element("notice").hidden, false);
    assert.equal(ui.element("notice").querySelector("b"), null);
    assert.equal(ui.element("notice").dataset.code, code);
    assert.equal(ui.element("mute").disabled, true);
    assert.equal(ui.element("end").disabled, true);
    assert.notEqual(ui.element("status").textContent, "Connected");
  });
}

test("the orb says Connecting, not Listening, until the session is ready", async () => {
  const ui = await connected({ pending: true });
  assert.equal(ui.element("presence").textContent, "Connecting");
  assert.equal(ui.element("mute").disabled, true);
  ui.sessions[0].resolve();
  await tick();
  assert.equal(ui.element("presence").textContent, "Listening");
  assert.equal(ui.element("mute").disabled, false);
});

test("server_error notice leaves the conversation active", async () => {
  const ui = await connected();
  ui.receive({ type: "notice", code: "server_error", text: "Reply timed out. Try again." });
  assert.equal(ui.element("notice").textContent, "Reply timed out. Try again.");
  assert.equal(ui.element("notice").hidden, false);
  assert.equal(ui.element("mute").disabled, false);
  assert.equal(ui.sessions[0].close.mock.callCount(), 0);
});

test("mic-denied notice explains Chrome permission controls and offers reconnect", async () => {
  const ui = await connected({ error: new DOMException("Permission denied", "NotAllowedError") });
  assert.match(ui.element("notice").textContent, /Chrome.*site controls.*allow Microphone.*reconnect/);
  assert.equal(ui.element("notice").dataset.code, "mic_denied");
  assert.equal(ui.element("connect").textContent, "Reconnect");
  assert.equal(ui.element("connect").disabled, false);
  assert.equal(ui.element("end").disabled, true);
});

test("connection-lost notice offers a working reconnect and ignores events from the old session", async () => {
  const ui = await connected();
  const old = ui.sessions[0];
  old.disconnected();
  assert.match(ui.element("notice").textContent, /Connection lost/);
  assert.equal(ui.element("connect").textContent, "Reconnect");
  ui.click("connect");
  await tick();
  assert.equal(ui.sessions.length, 2);
  old.receive({ type: "notice", code: "session_ended", text: "Stale event" });
  old.disconnected();
  assert.equal(ui.element("notice").hidden, true);
  assert.equal(ui.element("status").textContent, "Connected");
});

test("offer failures are visible and allow another attempt", async () => {
  const ui = await connected({ error: new Error("Offer rejected (401). Reopen the server link.") });
  assert.match(ui.element("notice").textContent, /Offer rejected \(401\)/);
  assert.equal(ui.element("notice").hidden, false);
  assert.equal(ui.element("connect").disabled, false);
});

test("end during setup cancels once and late completion cannot restart the UI", async () => {
  const ui = setup({ pending: true });
  ui.click("connect");
  ui.click("connect");
  assert.equal(ui.sessions.length, 1);
  assert.equal(ui.element("end").disabled, false);
  assert.equal(ui.element("mute").disabled, true);
  ui.click("end");
  ui.sessions[0].resolve();
  await tick();
  assert.equal(ui.element("status").textContent, "Session ended");
  assert.equal(ui.sessions[0].close.mock.callCount(), 1);
  assert.equal(ui.element("mute").disabled, true);
});

test("leaving the page closes the session and ignores its later failure", async () => {
  const ui = setup({ pending: true });
  ui.click("connect");
  ui.dispose();
  ui.sessions[0].reject(new Error("Late failure"));
  await tick();
  assert.equal(ui.sessions[0].close.mock.callCount(), 1);
  assert.equal(ui.element("notice").hidden, true);
});

test("playback exposes native controls when autoplay needs a user gesture", () => {
  const ui = setup();
  assert.equal(ui.element("audio").hasAttribute("controls"), true);
  assert.match(ui.document.querySelector(".playback").textContent, /press play/);
});

test("connection loss keeps a preceding server error visible and makes no memory promise", async () => {
  const ui = await connected();
  ui.receive({ type: "notice", code: "server_error", text: "The voice pipeline stopped." });
  ui.sessions[0].disconnected();
  assert.equal(ui.element("notice").textContent, "The voice pipeline stopped.");
  ui.click("connect");
  await tick();
  ui.sessions[1].disconnected();
  assert.equal(ui.element("notice").hidden, false);
  assert.equal(ui.element("notice").textContent, "Connection lost. Reconnect to start a new session.");
});

const voiceButton = (ui, voice) => ui.document.querySelector(`#voice [data-voice="${voice}"]`);

test("the voice toggle works before and during a session, is sent on connect and on change, and is remembered", async () => {
  const ui = setup();
  assert.equal(voiceButton(ui, "female").getAttribute("aria-pressed"), "true");
  assert.equal(voiceButton(ui, "female").textContent, "Female voice");
  assert.equal(voiceButton(ui, "male").textContent, "Male voice");
  voiceButton(ui, "male").click();
  assert.equal(voiceButton(ui, "male").getAttribute("aria-pressed"), "true");
  assert.equal(voiceButton(ui, "female").getAttribute("aria-pressed"), "false");
  assert.equal(ui.values.get("ames-voice"), "male");
  ui.click("connect");
  await tick();
  assert.deepEqual(ui.sessions[0].setVoice.mock.calls.map((call) => call.arguments), [["male"]]);
  voiceButton(ui, "female").click();
  assert.deepEqual(ui.sessions[0].setVoice.mock.calls.at(-1).arguments, ["female"]);
  assert.equal(ui.values.get("ames-voice"), "female");
});

test("a saved voice is restored, and unreadable or invalid storage falls back to the female voice", () => {
  const stored = (value) => ({ getItem: (key) => (key === "ames-voice" ? value : "true"), setItem() {} });
  assert.equal(voiceButton(setup({ storage: stored("male") }), "male").getAttribute("aria-pressed"), "true");
  assert.equal(voiceButton(setup({ storage: stored("robot") }), "female").getAttribute("aria-pressed"), "true");
  const broken = setup({ storage: { getItem() { throw Error("disabled"); }, setItem() { throw Error("disabled"); } } });
  assert.equal(voiceButton(broken, "female").getAttribute("aria-pressed"), "true");
  voiceButton(broken, "male").click();
  assert.equal(voiceButton(broken, "male").getAttribute("aria-pressed"), "true");
});

test("web search starts off, persists on the server, and shows network disclosure and activity", async () => {
  let enabled = false;
  const requests = [];
  const fetch = async (url, options) => {
    requests.push([url, options]);
    if (url === "/api/search" && options.method === "PUT") {
      enabled = JSON.parse(options.body).enabled;
      return { ok: true };
    }
    return { ok: true, json: async () => ({ enabled }) };
  };
  const ui = setup({ browser: { localStorage: { getItem: () => "true" }, fetch } });
  assert.equal(ui.element("search-toggle").disabled, true);
  await tick();
  assert.equal(ui.element("search-toggle").getAttribute("aria-pressed"), "false");
  assert.match(ui.element("search-description").textContent, /stay on your Mac/);
  ui.click("search-toggle");
  await tick();
  assert.equal(ui.element("search-toggle").getAttribute("aria-pressed"), "true");
  assert.match(ui.element("search-description").textContent, /may leave your Mac/);
  assert.deepEqual(requests.at(-1), ["/api/search", {
    method: "PUT", headers: { "X-Ames-Token": "test-token", "Content-Type": "application/json" },
    body: '{"enabled":true}',
  }]);
  const again = setup({ browser: { localStorage: { getItem: () => "true" }, fetch } });
  await tick();
  assert.equal(again.element("search-toggle").getAttribute("aria-pressed"), "true");
  ui.click("connect");
  await tick();
  ui.receive({ type: "search", active: true });
  assert.equal(ui.element("search-activity").hidden, false);
  assert.match(ui.element("search-activity").textContent, /Searching the web/);
  ui.receive({ type: "search", active: false });
  assert.equal(ui.element("search-activity").hidden, true);
  ui.click("search-toggle");
  await tick();
  assert.equal(enabled, false);
  assert.equal(ui.element("search-toggle").getAttribute("aria-pressed"), "false");
});

test("failed search setting change keeps the previous state visible", async () => {
  const fetch = async (url, options) => url === "/api/search" && options.method === "PUT"
    ? { ok: false } : { ok: true, json: async () => ({ enabled: false }) };
  const ui = setup({ browser: { localStorage: { getItem: () => "true" }, fetch } });
  await tick();
  ui.click("search-toggle");
  await tick();
  assert.equal(ui.element("search-toggle").getAttribute("aria-pressed"), "false");
  assert.match(ui.element("search-description").textContent, /Could not change/);
});

test("saved memories are fetched on open and a deleted memory disappears", async () => {
  const requests = [];
  const facts = [{ id: 7, text: "I prefer quiet mornings" },
    { id: 8, text: "<script>not HTML</script>" }];
  const fetch = async (url, options) => {
    requests.push([url, options]);
    if (url === "/api/memories") return { ok: true, json: async () => ({ facts }) };
    if (url === "/api/memories/7") return { ok: true };
    return { ok: true, json: async () => ({ enabled: false }) };
  };
  const ui = setup({ browser: { localStorage: { getItem: () => "true" }, fetch } });
  const details = ui.element("memories");
  details.open = true;
  details.dispatchEvent(new ui.document.defaultView.Event("toggle"));
  await tick();
  assert.equal(ui.element("memory-list").children.length, 2);
  assert.equal(ui.element("memory-list").querySelector("script"), null);
  assert.equal(ui.element("memory-list").children[1].querySelector("span").textContent, facts[1].text);
  ui.element("memory-list").children[0].querySelector("button").click();
  await tick();
  assert.equal(ui.element("memory-list").children.length, 1);
  assert.equal(requests.at(-1)[0], "/api/memories/7");
  assert.equal(requests.at(-1)[1].headers["X-Ames-Token"], "test-token");
});

test("failed memory deletion keeps the fact and allows another attempt", async () => {
  const fetch = async (url) => url === "/api/memories"
    ? { ok: true, json: async () => ({ facts: [{ id: 5, text: "Keep this" }] }) }
    : url === "/api/memories/5" ? { ok: false }
    : { ok: true, json: async () => ({ enabled: false }) };
  const ui = setup({ browser: { localStorage: { getItem: () => "true" }, fetch } });
  const details = ui.element("memories");
  details.open = true;
  details.dispatchEvent(new ui.document.defaultView.Event("toggle"));
  await tick();
  const remove = ui.element("memory-list").querySelector("button");
  remove.click();
  await tick();
  assert.equal(remove.disabled, false);
  assert.equal(ui.element("memory-list").children.length, 1);
  assert.match(ui.element("memory-status").textContent, /Could not delete/);
});

test("rating buttons appear only on finished rateable replies and still work after the session ends", async () => {
  const fetch = test.mock.fn(async () => ({ ok: true }));
  const values = new Map([["ames-advisory-seen", "true"]]);
  const storage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const ui = await connected({ browser: { localStorage: storage, fetch } });
  ui.receive({ type: "transcript", role: "ames", text: "Plain", final: true, turn_id: 1 });
  ui.receive({ type: "transcript", role: "ames", text: "Partial", final: false, turn_id: 2, rateable: true });
  assert.equal(ui.element("transcript").querySelectorAll(".rating").length, 0);
  ui.receive({ type: "transcript", role: "ames", text: "Done", final: true, turn_id: 2, rateable: true });
  const [good, bad] = ui.element("transcript").querySelectorAll(".rating button");
  assert.deepEqual([good.textContent, bad.textContent], ["Good", "Bad"]);
  good.click();
  ui.click("end");
  bad.click();
  const sent = fetch.mock.calls.filter((call) => call.arguments[0] === "/api/rate")
    .map((call) => [call.arguments[0], call.arguments[1].headers["X-Ames-Token"], JSON.parse(call.arguments[1].body)]);
  assert.deepEqual(sent, [
    ["/api/rate", "test-token", { turn_id: 2, rating: "good" }],
    ["/api/rate", "test-token", { turn_id: 2, rating: "bad" }],
  ]);
  assert.deepEqual([good.getAttribute("aria-pressed"), bad.getAttribute("aria-pressed")], ["false", "true"]);
});

test("the text box is enabled only while connected, sends trimmed text on click or Enter and then clears", async () => {
  const ui = setup({ pending: true });
  assert.equal(ui.element("typed-text").disabled, true);
  assert.equal(ui.element("typed-send").disabled, true);
  ui.click("connect");
  await tick();
  assert.equal(ui.element("typed-text").disabled, true);
  ui.sessions[0].resolve();
  await tick();
  const box = ui.element("typed-text");
  assert.equal(box.disabled, false);
  assert.equal(ui.element("typed-send").disabled, false);
  assert.equal(box.getAttribute("aria-label"), "Type a message to Ames");
  box.value = "   ";
  ui.click("typed-send");
  box.value = "  hello there ";
  ui.click("typed-send");
  assert.equal(box.value, "");
  box.value = "again";
  const enter = new ui.document.defaultView.Event("keydown");
  enter.key = "Enter";
  box.dispatchEvent(enter);
  assert.deepEqual(ui.sessions[0].sendText.mock.calls.map((call) => call.arguments), [["hello there"], ["again"]]);
  ui.click("mute");
  assert.equal(box.disabled, false);
  ui.click("end");
  assert.equal(box.disabled, true);
  assert.equal(ui.element("typed-send").disabled, true);
});
