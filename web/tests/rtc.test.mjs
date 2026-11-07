import assert from "node:assert/strict";
import test from "node:test";
import { peerConfiguration } from "../src/rtc.ts";

test("WebRTC uses no STUN or TURN servers", () => {
  assert.deepEqual(peerConfiguration(), { iceServers: [] });
});

test("peer configurations do not share a mutable ICE list", () => {
  const config = peerConfiguration();
  config.iceServers.push({ urls: "stun:invalid.example" });
  assert.deepEqual(peerConfiguration().iceServers, []);
});
