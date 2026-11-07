import { sessionToken } from "./auth";
import { startSession } from "./rtc";

const status = document.querySelector<HTMLParagraphElement>("#status");
const connect = document.querySelector<HTMLButtonElement>("#connect");
const audio = document.querySelector<HTMLAudioElement>("#audio");
if (!status || !connect || !audio) throw new Error("Missing echo controls.");
const token = sessionToken(window);
status.textContent = token ? "Ready for the audio echo. Use headphones." : "Open the link from the server to connect.";
connect.disabled = !token;
let close: (() => void) | undefined;
connect.addEventListener("click", async () => {
  if (!token) return;
  connect.disabled = true;
  close?.();
  status.textContent = "Connecting...";
  try {
    const session = startSession(token, audio,
      (event) => { if (event.type === "notice") status.textContent = event.text; },
      () => { status.textContent = "Connection lost. Connect again."; });
    close = session.close;
    await session.ready;
    status.textContent = "Audio echo connected (stub models).";
  } catch (error) {
    status.textContent = error instanceof Error ? error.message : "Could not connect.";
  } finally {
    connect.disabled = false;
  }
});
window.addEventListener("pagehide", () => close?.());
