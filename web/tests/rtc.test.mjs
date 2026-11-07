import assert from "node:assert/strict";
import test from "node:test";
import { peerConfiguration, startEcho } from "../src/rtc.ts";

test("WebRTC uses no STUN or TURN servers", () => {
  assert.deepEqual(peerConfiguration(), { iceServers: [] });
});

test("peer configurations do not share a mutable ICE list", () => {
  const config = peerConfiguration();
  config.iceServers.push({ urls: "stun:invalid.example" });
  assert.deepEqual(peerConfiguration().iceServers, []);
});

function browser(t, { status = 200, permissionError, gather = false } = {}) {
  const peerDescriptor = Object.getOwnPropertyDescriptor(globalThis, "RTCPeerConnection");
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  t.after(() => {
    if (peerDescriptor) Object.defineProperty(globalThis, "RTCPeerConnection", peerDescriptor);
    else delete globalThis.RTCPeerConnection;
    Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
  });
  let peer;
  const track = { stop: t.mock.fn() };
  const stream = { getAudioTracks: () => [track], getTracks: () => [track] };
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { mediaDevices: { getUserMedia: async () => {
      if (permissionError) throw permissionError;
      return stream;
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
    async setRemoteDescription(description) { this.remoteDescription = description; }
    close() { this.connectionState = "closed"; }
  };
  const fetch = t.mock.method(globalThis, "fetch", async () => {
    assert.equal(peer.iceGatheringState, "complete");
    return { ok: status === 200, status, json: async () => ({ type: "answer", sdp: "answer-sdp" }) };
  });
  return { get peer() { return peer; }, track, stream, fetch, audio: { srcObject: null } };
}

for (const gather of [false, true]) {
  test(`offer sends the token header after local ICE gathering (wait=${gather})`, async (t) => {
    const setup = browser(t, { gather });
    const close = await startEcho("local-secret", setup.audio, () => {});
    assert.deepEqual(setup.peer.config, { iceServers: [] });
    assert.equal(setup.fetch.mock.calls[0].arguments[0], "/api/offer");
    const request = setup.fetch.mock.calls[0].arguments[1];
    assert.equal(request.headers["X-Ames-Token"], "local-secret");
    assert.equal(request.method, "POST");
    assert.deepEqual(JSON.parse(request.body), { type: "offer", sdp: "local-sdp" });
    assert.deepEqual(setup.peer.remoteDescription, { type: "answer", sdp: "answer-sdp" });
    close();
    assert.equal(setup.peer.connectionState, "closed");
    assert.equal(setup.track.stop.mock.callCount(), 1);
  });
}

test("a rejected offer releases the microphone and peer", async (t) => {
  const setup = browser(t, { status: 401 });
  await assert.rejects(startEcho("stale-token", setup.audio, () => {}), /Offer rejected \(401\)/);
  assert.equal(setup.peer.connectionState, "closed");
  assert.equal(setup.track.stop.mock.callCount(), 1);
});

test("microphone denial releases the peer without sending an offer", async (t) => {
  const setup = browser(t, { permissionError: new Error("Microphone denied") });
  await assert.rejects(startEcho("token", setup.audio, () => {}), /Microphone denied/);
  assert.equal(setup.peer.connectionState, "closed");
  assert.equal(setup.fetch.mock.callCount(), 0);
});

test("a replacement notice closes the peer and releases the microphone", async (t) => {
  const setup = browser(t);
  const notices = [];
  await startEcho("token", setup.audio, (text) => notices.push(text));
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
  await startEcho("token", setup.audio, (text) => notices.push(text));
  setup.peer.connectionState = "failed";
  setup.peer.dispatchEvent(new Event("connectionstatechange"));
  assert.deepEqual(notices, ["Connection lost. Connect again."]);
  assert.equal(setup.track.stop.mock.callCount(), 1);
});
