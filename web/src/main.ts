import { peerConfiguration } from "./rtc";

const status = document.querySelector<HTMLParagraphElement>("#status");
if (status) {
  status.textContent = `Transport scaffold ready (${peerConfiguration().iceServers?.length} ICE servers). Browser connection flow follows in S1b.`;
}
