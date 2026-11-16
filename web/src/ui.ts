import type { Session, SessionEvent, Speaker, startSession, Voice } from "./rtc";

type Rating = "good" | "bad";

export function mountConversation(
  document: Document,
  token: string | null,
  browser: Pick<Window, "localStorage" | "fetch">,
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
  const voices = [...document.querySelectorAll<HTMLButtonElement>("#voice button")];
  const audio = find<HTMLAudioElement>("audio");
  const advisory = find("advisory");
  const acknowledge = find<HTMLButtonElement>("acknowledge");
  const status = find("status");
  const notice = find("notice");
  const orb = find("orb");
  const presence = find("presence");
  const transcript = find("transcript");
  const empty = find("transcript-empty");
  const searchToggle = find<HTMLButtonElement>("search-toggle");
  const searchDescription = find("search-description");
  const searchActivity = find("search-activity");
  const memories = find<HTMLDetailsElement>("memories");
  const memoryStatus = find("memory-status");
  const memoryList = find<HTMLUListElement>("memory-list");
  const turns = new Map<string, { row: HTMLLIElement; text: HTMLParagraphElement; final: boolean }>();
  let seen = false;
  try { seen = browser.localStorage.getItem("ames-advisory-seen") === "true"; } catch { /* Site storage may be disabled. */ }
  let voice: Voice = "female";
  try {
    const saved = browser.localStorage.getItem("ames-voice");
    if (saved === "male" || saved === "female") voice = saved;
  } catch { /* Site storage may be disabled; the default voice applies. */ }
  let phase: "idle" | "connecting" | "connected" = "idle";
  let muted = false;
  let session: Session | undefined;
  let attempt = 0;
  let who: Speaker = "none";
  let webSearch = false;
  let mounted = true;

  const settingsHeaders = { "X-Ames-Token": token ?? "" };
  const renderSearch = () => {
    searchToggle.setAttribute("aria-pressed", String(webSearch));
    searchToggle.textContent = webSearch ? "Turn web search off" : "Turn web search on";
    searchDescription.textContent = webSearch
      ? "On. Search queries may leave your Mac."
      : "Off. Search queries stay on your Mac.";
  };
  renderSearch();
  if (token && browser.fetch) {
    browser.fetch("/api/search", { headers: settingsHeaders })
      .then(async (response) => {
        if (!response.ok) throw new Error("Search setting unavailable");
        const body = await response.json();
        if (typeof body.enabled !== "boolean") throw new Error("Invalid search setting");
        if (!mounted) return;
        webSearch = body.enabled;
        renderSearch();
        searchToggle.disabled = false;
      }).catch(() => { if (mounted) searchDescription.textContent = "Could not load web search setting."; });
  }
  searchToggle.addEventListener("click", async () => {
    if (!token || searchToggle.disabled) return;
    searchToggle.disabled = true;
    try {
      const response = await browser.fetch("/api/search", {
        method: "PUT", headers: { ...settingsHeaders, "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !webSearch }),
      });
      if (!response.ok) throw new Error("Search setting could not be saved");
      if (!mounted) return;
      webSearch = !webSearch;
      renderSearch();
    } catch {
      if (mounted) searchDescription.textContent = "Could not change web search. Try again.";
    } finally {
      if (mounted) searchToggle.disabled = false;
    }
  });
  const loadMemories = async () => {
    if (!token || !browser.fetch) return;
    memoryStatus.textContent = "Loading saved memories...";
    try {
      const response = await browser.fetch("/api/memories", { headers: settingsHeaders });
      if (!response.ok) throw new Error("Could not load memories");
      const body = await response.json();
      if (!Array.isArray(body.facts)) throw new Error("Invalid memories");
      if (!mounted) return;
      memoryList.replaceChildren();
      for (const fact of body.facts) {
        if (!Number.isSafeInteger(fact.id) || fact.id < 1 || typeof fact.text !== "string") continue;
        const row = document.createElement("li");
        const text = document.createElement("span");
        text.textContent = fact.text;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "Delete";
        remove.setAttribute("aria-label", `Delete memory: ${fact.text}`);
        remove.addEventListener("click", async () => {
          remove.disabled = true;
          try {
            const deleted = await browser.fetch(`/api/memories/${fact.id}`, {
              method: "DELETE", headers: settingsHeaders,
            });
            if (!deleted.ok) throw new Error("Could not delete memory");
            if (!mounted) return;
            row.remove();
            memoryStatus.textContent = memoryList.children.length ? "" : "No saved memories.";
          } catch {
            if (mounted) { remove.disabled = false; memoryStatus.textContent = "Could not delete memory. Try again."; }
          }
        });
        row.append(text, remove);
        memoryList.append(row);
      }
      memoryStatus.textContent = memoryList.children.length ? "" : "No saved memories.";
    } catch {
      if (mounted) memoryStatus.textContent = "Could not load memories. Close and reopen to retry.";
    }
  };
  memories.addEventListener("toggle", () => { if (memories.open) void loadMemories(); });

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
  const ratingButtons = (turnId: number) => {
    const group = document.createElement("div");
    group.className = "rating";
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "Rate this reply");
    const buttons = (["good", "bad"] as Rating[]).map((rating) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = rating === "good" ? "Good" : "Bad";
      button.setAttribute("aria-pressed", "false");
      // Rating works after the session ends, so it does not use the session's data channel.
      button.addEventListener("click", () => {
        if (!token) return;
        browser.fetch("/api/rate", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Ames-Token": token },
          body: JSON.stringify({ turn_id: turnId, rating }),
        }).catch(() => { /* A lost rating is not worth interrupting the user. */ });
        for (const other of buttons) other.setAttribute("aria-pressed", String(other === button));
      });
      return button;
    });
    group.append(...buttons);
    return group;
  };
  const receive = (event: SessionEvent) => {
    switch (event.type) {
      case "search":
        searchActivity.hidden = !event.active;
        searchActivity.textContent = event.active ? "Searching the web. This query may leave your Mac." : "";
        break;
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
        if (event.final && event.rateable) turn.row.append(ratingButtons(event.turn_id));
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

  const renderVoice = () => {
    for (const button of voices) button.setAttribute("aria-pressed", String(button.dataset.voice === voice));
  };
  renderVoice();
  for (const button of voices) {
    button.addEventListener("click", () => {
      voice = button.dataset.voice === "male" ? "male" : "female";
      try { browser.localStorage.setItem("ames-voice", voice); } catch { /* The choice still applies to this visit. */ }
      renderVoice();
      if (phase === "connected") session?.setVoice(voice);
    });
  }

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
      next.setVoice(voice);
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
    mounted = false;
    ++attempt;
    phase = "idle";
    session?.close();
  };
}
