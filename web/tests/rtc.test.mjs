import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parseHTML } from "linkedom";
import { peerConfiguration, startSession } from "../src/rtc.ts";

test("WebRTC uses no STUN or TURN servers", () => {
  assert.deepEqual(peerConfiguration(), { iceServers: [] });
});

test("peer configurations do not share a mutable ICE list", () => {
  const config = peerConfiguration();
  config.iceServers.push({ urls: "stun:invalid.example" });
  assert.deepEqual(peerConfiguration().iceServers, []);
});

function browser(t, { status = 200, permissionError, gather = false, permission, opens = true } = {}) {
  const peerDescriptor = Object.getOwnPropertyDescriptor(globalThis, "RTCPeerConnection");
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  t.after(() => {
    if (peerDescriptor) Object.defineProperty(globalThis, "RTCPeerConnection", peerDescriptor);
    else delete globalThis.RTCPeerConnection;
    Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
  });
  let peer;
  const track = { enabled: true, stop: t.mock.fn() };
  const stream = { getAudioTracks: () => [track], getTracks: () => [track] };
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { mediaDevices: { getUserMedia: async () => {
      if (permissionError) throw permissionError;
      return permission ?? stream;
    } } },
  });
  globalThis.RTCPeerConnection = class extends EventTarget {
    constructor(config) {
      super();
      peer = this;
      this.config = config;
      this.iceGatheringState = gather ? "gathering" : "complete";
      this.connectionState = "new";
      this.channel = new EventTarget();
    }
    createDataChannel() { return this.channel; }
    addTrack(track, stream) { this.sentTrack = { track, stream }; }
    async createOffer() { return { type: "offer", sdp: "local-sdp" }; }
    async setLocalDescription(description) {
      this.localDescription = description;
      if (gather) setImmediate(() => {
        this.iceGatheringState = "complete";
        this.dispatchEvent(new Event("icegatheringstatechange"));
      });
    }
    async setRemoteDescription(description) {
      this.remoteDescription = description;
      if (opens) this.channel.readyState = "open";
    }
    close() { this.connectionState = "closed"; }
  };
  const fetch = t.mock.method(globalThis, "fetch", async () => {
    assert.equal(peer.iceGatheringState, "complete");
    return { ok: status === 200, status, text: async () => "The voice pipeline could not start.",
      json: async () => ({ type: "answer", sdp: "answer-sdp" }) };
  });
  return { get peer() { return peer; }, track, stream, fetch, audio: { srcObject: null } };
}

for (const gather of [false, true]) {
  test(`offer sends the token header after local ICE gathering (wait=${gather})`, async (t) => {
    const setup = browser(t, { gather });
    const session = startSession("local-secret", setup.audio, () => {}, () => {});
    await session.ready;
    assert.deepEqual(setup.peer.config, { iceServers: [] });
    assert.equal(setup.fetch.mock.calls[0].arguments[0], "/api/offer");
    const request = setup.fetch.mock.calls[0].arguments[1];
    assert.equal(request.headers["X-Ames-Token"], "local-secret");
    assert.equal(request.method, "POST");
    assert.deepEqual(JSON.parse(request.body), { type: "offer", sdp: "local-sdp" });
    assert.deepEqual(setup.peer.remoteDescription, { type: "answer", sdp: "answer-sdp" });
    session.close();
    assert.equal(setup.peer.connectionState, "closed");
    assert.equal(setup.track.stop.mock.callCount(), 1);
  });
}

test("a rejected offer releases the microphone and peer", async (t) => {
  const setup = browser(t, { status: 401 });
  await assert.rejects(startSession("stale-token", setup.audio, () => {}, () => {}).ready, /Offer rejected \(401\)/);
  assert.equal(setup.peer.connectionState, "closed");
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("a server failure shows the server's own message", async (t) => {
  const setup = browser(t, { status: 500 });
  await assert.rejects(startSession("token", setup.audio, () => {}, () => {}).ready, /voice pipeline could not start/);
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("a peer that fails before the channel opens rejects instead of reporting a lost session", async (t) => {
  const setup = browser(t, { opens: false });
  const lost = t.mock.fn();
  const session = startSession("token", setup.audio, () => {}, lost);
  while (!setup.peer?.remoteDescription) await new Promise((resolve) => setImmediate(resolve));
  setup.peer.connectionState = "failed";
  setup.peer.dispatchEvent(new Event("connectionstatechange"));
  await assert.rejects(session.ready, /Could not reach the voice server/);
  assert.equal(lost.mock.callCount(), 0);
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("the session is ready only once the data channel opens", async (t) => {
  const setup = browser(t, { opens: false });
  const session = startSession("token", setup.audio, () => {}, () => {});
  let ready = false;
  session.ready.then(() => { ready = true; });
  while (!setup.peer?.remoteDescription) await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(ready, false);
  setup.peer.channel.dispatchEvent(new Event("open"));
  await session.ready;
  session.close();
});

test("microphone denial releases the peer without sending an offer", async (t) => {
  const setup = browser(t, { permissionError: new Error("Microphone denied") });
  await assert.rejects(startSession("token", setup.audio, () => {}, () => {}).ready, /Microphone denied/);
  assert.equal(setup.peer.connectionState, "closed");
  assert.equal(setup.fetch.mock.callCount(), 0);
});

test("a replacement notice closes the peer and releases the microphone", async (t) => {
  const setup = browser(t);
  const notices = [];
  await startSession("token", setup.audio, (event) => notices.push(event.text),
    () => notices.push("Connection lost. Connect again.")).ready;
  setup.peer.channel.dispatchEvent(new MessageEvent("message", { data: JSON.stringify({
    label: "rtvi-ai", type: "server-message",
    data: { type: "notice", code: "session_replaced", text: "Another tab started a session." },
  }) }));
  assert.deepEqual(notices, ["Another tab started a session."]);
  assert.equal(setup.peer.connectionState, "closed");
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("a failed connection releases the microphone", async (t) => {
  const setup = browser(t);
  const notices = [];
  await startSession("token", setup.audio, (event) => notices.push(event.text),
    () => notices.push("Connection lost. Connect again.")).ready;
  setup.peer.connectionState = "failed";
  setup.peer.dispatchEvent(new Event("connectionstatechange"));
  assert.deepEqual(notices, ["Connection lost. Connect again."]);
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("blocked autoplay opens playback instructions and native controls", async (t) => {
  const setup = browser(t);
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "MediaStream");
  t.after(() => {
    if (descriptor) Object.defineProperty(globalThis, "MediaStream", descriptor);
    else delete globalThis.MediaStream;
  });
  globalThis.MediaStream = class {};
  const { document } = parseHTML(readFileSync(new URL("../index.html", import.meta.url), "utf8"));
  const audio = document.getElementById("audio");
  audio.play = t.mock.fn(async () => { throw new DOMException("Autoplay blocked", "NotAllowedError"); });
  const playback = audio.closest("details");
  const session = startSession("token", audio, () => {}, () => {});
  await session.ready;
  assert.equal(playback.hasAttribute("open"), false);
  const event = new Event("track");
  event.track = setup.track;
  setup.peer.dispatchEvent(event);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(audio.play.mock.callCount(), 1);
  assert.equal(playback.hasAttribute("open"), true);
  assert.equal(audio.hasAttribute("controls"), true);
  assert.match(playback.textContent, /press play/);
  session.close();
});

function send(setup, data) {
  setup.peer.channel.dispatchEvent(new MessageEvent("message", { data: JSON.stringify({
    label: "rtvi-ai", type: "server-message", data,
  }) }));
}

test("all server event types reach the client and server errors keep the session open", async (t) => {
  const setup = browser(t);
  const events = [];
  const session = startSession("token", setup.audio, (event) => events.push(event), () => {});
  await session.ready;
  const expected = [
    { type: "transcript", role: "user", text: "Hello", final: false, turn_id: 1 },
    { type: "speaking", who: "ames" },
    { type: "state", mood: "amused", turn_id: 1 },
    { type: "notice", code: "server_error", text: "Please try that again." },
  ];
  expected.forEach((event) => { send(setup, event); });
  assert.deepEqual(events, expected);
  assert.notEqual(setup.peer.connectionState, "closed");
  session.close();
});

test("invalid messages are ignored without breaking the next event", async (t) => {
  const setup = browser(t);
  const events = [];
  const session = startSession("token", setup.audio, (event) => events.push(event), () => {});
  await session.ready;
  for (const raw of ["not json", "null", "{}", '{"label":"other","type":"server-message"}']) {
    setup.peer.channel.dispatchEvent(new MessageEvent("message", { data: raw }));
  }
  for (const data of [null, {}, { type: "speaking", who: "unknown" },
    { type: "state", mood: "unknown", turn_id: 1 },
    { type: "transcript", role: "ames", text: "Hi", final: "yes", turn_id: 1 },
    { type: "transcript", role: "ames", text: "Hi", final: true, turn_id: -1 },
    { type: "notice", code: "unknown", text: "Unknown" }]) send(setup, data);
  send(setup, { type: "speaking", who: "none" });
  assert.deepEqual(events, [{ type: "speaking", who: "none" }]);
  session.close();
});

test("mute toggles the local audio track without another offer", async (t) => {
  const setup = browser(t);
  const session = startSession("token", setup.audio, () => {}, () => {});
  await session.ready;
  session.setMuted(true);
  assert.equal(setup.track.enabled, false);
  session.setMuted(false);
  assert.equal(setup.track.enabled, true);
  assert.equal(setup.fetch.mock.callCount(), 1);
  session.close();
});

test("session-ended notice survives later transport closure and ignores late events", async (t) => {
  const setup = browser(t);
  const events = [];
  const lost = t.mock.fn();
  const session = startSession("token", setup.audio, (event) => events.push(event), lost);
  await session.ready;
  send(setup, { type: "notice", code: "session_ended", text: "Session is over." });
  setup.peer.channel.dispatchEvent(new Event("close"));
  setup.peer.dispatchEvent(new Event("connectionstatechange"));
  send(setup, { type: "speaking", who: "ames" });
  session.close();
  assert.deepEqual(events, [{ type: "notice", code: "session_ended", text: "Session is over." }]);
  assert.equal(lost.mock.callCount(), 0);
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("an unexpected data channel close reports connection loss once", async (t) => {
  const setup = browser(t);
  const lost = t.mock.fn();
  await startSession("token", setup.audio, () => {}, lost).ready;
  setup.peer.channel.dispatchEvent(new Event("close"));
  setup.peer.dispatchEvent(new Event("connectionstatechange"));
  assert.equal(lost.mock.callCount(), 1);
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("ending while permission is pending stops the late microphone without an offer", async (t) => {
  let allow;
  const setup = browser(t, { permission: new Promise((resolve) => { allow = resolve; }) });
  const session = startSession("token", setup.audio, () => {}, () => {});
  session.close();
  allow(setup.stream);
  await assert.rejects(session.ready, { name: "AbortError" });
  assert.equal(setup.fetch.mock.callCount(), 0);
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("a temporary disconnected state can recover without closing the session", async (t) => {
  const setup = browser(t);
  const lost = t.mock.fn();
  const session = startSession("token", setup.audio, () => {}, lost);
  await session.ready;
  for (const state of ["disconnected", "connected"]) {
    setup.peer.connectionState = state;
    setup.peer.dispatchEvent(new Event("connectionstatechange"));
  }
  assert.equal(lost.mock.callCount(), 0);
  assert.equal(setup.track.stop.mock.callCount(), 0);
  session.close();
});
