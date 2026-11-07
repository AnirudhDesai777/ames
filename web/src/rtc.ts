export type Mood = "neutral" | "amused" | "excited" | "soft" | "annoyed" | "concerned";
export type Speaker = "user" | "ames" | "none";
export type SessionEvent =
  | { type: "transcript"; role: "user" | "ames"; text: string; final: boolean; turn_id: number }
  | { type: "speaking"; who: Speaker }
  | { type: "state"; mood: Mood; turn_id: number }
  | { type: "notice"; code: "session_replaced" | "session_ended" | "server_error"; text: string };

export type Session = { ready: Promise<void>; close: () => void; setMuted: (muted: boolean) => void };

export function peerConfiguration(): RTCConfiguration {
  return { iceServers: [] };
}

function serverEvent(raw: unknown): SessionEvent | null {
  if (typeof raw !== "string") return null;
  let message = null;
  try { message = JSON.parse(raw); } catch { return null; }
  if (message?.label !== "rtvi-ai" || message.type !== "server-message") return null;
  const data = message.data;
  if (!data || typeof data !== "object") return null;
  const turn = Number.isSafeInteger(data.turn_id) && data.turn_id >= 0;
  switch (data.type) {
    case "transcript":
      return turn && ["user", "ames"].includes(data.role) && typeof data.text === "string" && typeof data.final === "boolean" ? data : null;
    case "speaking":
      return ["user", "ames", "none"].includes(data.who) ? data : null;
    case "state":
      return turn && ["neutral", "amused", "excited", "soft", "annoyed", "concerned"].includes(data.mood) ? data : null;
    case "notice":
      return ["session_replaced", "session_ended", "server_error"].includes(data.code) && typeof data.text === "string" ? data : null;
    default:
      return null;
  }
}

export function startSession(
  token: string,
  audio: HTMLAudioElement,
  receive: (event: SessionEvent) => void,
  disconnected: () => void,
): Session {
  const peer = new RTCPeerConnection(peerConfiguration());
  const controller = new AbortController();
  let stream: MediaStream | undefined;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    controller.abort();
    peer.close();
    stream?.getTracks().forEach((track) => { track.stop(); });
    audio.srcObject = null;
  };
  const lost = () => {
    if (closed) return;
    close();
    disconnected();
  };
  peer.addEventListener("connectionstatechange", () => {
    if (["failed", "disconnected", "closed"].includes(peer.connectionState)) lost();
  });
  peer.addEventListener("track", (event) => {
    if (closed) return;
    audio.srcObject = new MediaStream([event.track]);
    // Native audio controls let the user resume playback when Chrome blocks autoplay.
    audio.play().catch(() => { audio.controls = true; });
  });
  const channel = peer.createDataChannel("rtvi-ai");
  channel.addEventListener("close", lost);
  channel.addEventListener("message", (event) => {
    if (closed) return;
    const data = serverEvent(event.data);
    if (!data) return;
    if (data.type === "notice" && data.code !== "server_error") close();
    receive(data);
  });
  const connect = async () => {
    try {
      controller.signal.throwIfAborted();
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true } });
      if (closed) stream.getTracks().forEach((track) => { track.stop(); });
      controller.signal.throwIfAborted();
      for (const track of stream.getAudioTracks()) peer.addTrack(track, stream);
      await peer.setLocalDescription(await peer.createOffer());
      controller.signal.throwIfAborted();
      if (peer.iceGatheringState !== "complete") {
        await new Promise<void>((resolve, reject) => {
          const cleanup = () => {
            clearTimeout(timeout);
            peer.removeEventListener("icegatheringstatechange", finish);
            controller.signal.removeEventListener("abort", abort);
          };
          const finish = () => {
            if (peer.iceGatheringState !== "complete") return;
            cleanup();
            resolve();
          };
          const abort = () => { cleanup(); reject(controller.signal.reason); };
          const timeout = setTimeout(() => {
            cleanup();
            reject(new Error("Local ICE gathering timed out."));
          }, 10000);
          peer.addEventListener("icegatheringstatechange", finish);
          controller.signal.addEventListener("abort", abort, { once: true });
          finish();
        });
      }
      controller.signal.throwIfAborted();
      const response = await fetch("/api/offer", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Ames-Token": token },
        body: JSON.stringify(peer.localDescription),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Offer rejected (${response.status}). Reopen the server link.`);
      const answer = await response.json();
      controller.signal.throwIfAborted();
      await peer.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
      controller.signal.throwIfAborted();
    } catch (error) {
      close();
      throw error;
    }
  };
  return {
    ready: connect(), close,
    setMuted: (muted) => { stream?.getAudioTracks().forEach((track) => { track.enabled = !muted; }); },
  };
}
