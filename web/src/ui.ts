import type { Session, SessionEvent, Speaker, startSession } from "./rtc";

export function mountConversation(
  document: Document,
  token: string | null,
  browser: Pick<Window, "localStorage">,
  connect: typeof startSession,
): () => void {
  const find = <T extends HTMLElement>(id: string): T => {
    const element = document.querySelector<T>(`#${id}`);
    if (!element) throw new Error(`Missing conversation element: ${id}`);
    return element;
  };
  const start = find<HTMLButtonElement>("connect");
  const mute = find<HTMLButtonElement>("mute");
  const end = find<HTMLButtonElement>("end");
  const audio = find<HTMLAudioElement>("audio");
  const advisory = find("advisory");
  const acknowledge = find<HTMLButtonElement>("acknowledge");
  const status = find("status");
  const notice = find("notice");
  const orb = find("orb");
  const presence = find("presence");
  const transcript = find("transcript");
  const empty = find("transcript-empty");
  const turns = new Map<string, { row: HTMLLIElement; text: HTMLParagraphElement; final: boolean }>();
  let seen = false;
  try { seen = browser.localStorage.getItem("ames-advisory-seen") === "true"; } catch { /* Site storage may be disabled. */ }
  let phase: "idle" | "connecting" | "connected" = "idle";
  let muted = false;
  let session: Session | undefined;
  let attempt = 0;
  let who: Speaker = "none";

  // A function, so TypeScript does not narrow `phase` across an await it cannot see through.
  const stale = (current: number) => current !== attempt || phase === "idle";
  const renderOrb = () => {
    orb.dataset.speaking = who;
    presence.textContent = who === "ames" ? "Ames is speaking" : who === "user" ? "You're speaking" : phase === "connected" ? "Listening" : phase === "connecting" ? "Connecting" : "Ready to talk";
    orb.setAttribute("aria-label", presence.textContent);
  };
  const finish = (text: string, button = "Start conversation") => {
    const wasRunning = phase !== "idle";
    phase = "idle";
    session?.close();
    session = undefined;
    muted = false;
    mute.textContent = "Mute microphone";
    mute.setAttribute("aria-pressed", "false");
    mute.disabled = true;
    end.disabled = true;
    start.disabled = !token || !seen;
    start.textContent = button;
    if (wasRunning) start.focus();
    status.textContent = text;
    who = "none";
    renderOrb();
  };
  const showNotice = (text: string, code: string) => {
    notice.textContent = text;
    notice.dataset.code = code;
    notice.hidden = false;
  };
  const receive = (event: SessionEvent) => {
    switch (event.type) {
      case "speaking":
        who = event.who;
        renderOrb();
        break;
      case "transcript": {
        const key = `${event.turn_id}:${event.role}`;
        let turn = turns.get(key);
        if (turn?.final) break;
        if (!turn) {
          const row = document.createElement("li");
          const speaker = document.createElement("span");
          speaker.className = "speaker";
          speaker.textContent = event.role === "ames" ? "Ames" : "You";
          const text = document.createElement("p");
          row.dataset.role = event.role;
          row.append(speaker, text);
          transcript.append(row);
          turn = { row, text, final: false };
          turns.set(key, turn);
        }
        turn.text.textContent = event.text;
        turn.final = event.final;
        turn.row.dataset.final = String(event.final);
        empty.hidden = true;
        transcript.scrollTop = transcript.scrollHeight;
        break;
      }
      case "notice":
        if (event.code !== "server_error") finish(event.code === "session_ended" ? "Session ended" : "Session replaced");
        showNotice(event.text, event.code);
        break;
    }
  };

  advisory.hidden = seen;
  finish(token ? "Ready when you are" : "Open the link from the server to connect.");
  acknowledge.addEventListener("click", () => {
    seen = true;
    try { browser.localStorage.setItem("ames-advisory-seen", "true"); } catch { /* Acknowledgement still applies to this visit. */ }
    advisory.hidden = true;
    start.disabled = !token;
    start.focus();
  });
  start.addEventListener("click", async () => {
    if (!token || !seen || phase !== "idle") return;
    const current = ++attempt;
    phase = "connecting";
    notice.hidden = true;
    notice.textContent = "";
    delete notice.dataset.code;
    transcript.replaceChildren();
    turns.clear();
    empty.hidden = false;
    start.disabled = true;
    end.disabled = false;
    end.focus();
    status.textContent = "Connecting...";
    renderOrb();
    try {
      const next = connect(token, audio, (event) => {
        if (!stale(current)) receive(event);
      }, () => {
        if (stale(current)) return;
        finish("Disconnected", "Reconnect");
        if (notice.dataset.code !== "server_error") {
          showNotice("Connection lost. Reconnect to start a new session.", "connection_lost");
        }
      });
      session = next;
      await next.ready;
      if (stale(current)) return;
      phase = "connected";
      status.textContent = "Connected";
      mute.disabled = false;
      renderOrb();
    } catch (error) {
      if (stale(current)) return;
      finish("Could not connect", "Reconnect");
      const denied = error instanceof Error && ["NotAllowedError", "PermissionDeniedError"].includes(error.name);
      showNotice(denied
        ? "Microphone access was denied. In Chrome, open the site controls beside the address bar, allow Microphone, then reconnect."
        : error instanceof Error ? error.message : "Could not connect. Try reconnecting.", denied ? "mic_denied" : "connection_error");
    }
  });
  mute.addEventListener("click", () => {
    if (phase !== "connected") return;
    muted = !muted;
    session?.setMuted(muted);
    mute.setAttribute("aria-pressed", String(muted));
    mute.textContent = muted ? "Unmute microphone" : "Mute microphone";
  });
  end.addEventListener("click", () => {
    if (phase === "idle") return;
    ++attempt;
    finish("Session ended");
  });
  return () => {
    ++attempt;
    phase = "idle";
    session?.close();
  };
}
