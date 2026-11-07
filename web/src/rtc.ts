export function peerConfiguration(): RTCConfiguration {
  return { iceServers: [] };
}

export async function startEcho(
  token: string,
  audio: HTMLAudioElement,
  notice: (text: string) => void,
): Promise<() => void> {
  const peer = new RTCPeerConnection(peerConfiguration());
  let stream: MediaStream | undefined;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    peer.close();
    stream?.getTracks().forEach((track) => { track.stop(); });
    audio.srcObject = null;
  };
  peer.addEventListener("connectionstatechange", () => {
    if (peer.connectionState === "failed" || peer.connectionState === "disconnected") {
      notice("Connection lost. Connect again.");
      close();
    }
  });
  peer.addEventListener("track", (event) => {
    audio.srcObject = new MediaStream([event.track]);
    audio.play().catch(() => notice("Use the audio play button to hear the echo."));
  });
  const channel = peer.createDataChannel("rtvi-ai");
  channel.addEventListener("close", close);
  channel.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.label === "rtvi-ai" && message.type === "server-message" && message.data?.type === "notice") {
      notice(message.data.text);
      close();
    }
  });
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true } });
    for (const track of stream.getAudioTracks()) peer.addTrack(track, stream);
    await peer.setLocalDescription(await peer.createOffer());
    if (peer.iceGatheringState !== "complete") {
      await new Promise<void>((resolve, reject) => {
        const finish = () => {
          if (peer.iceGatheringState !== "complete") return;
          clearTimeout(timeout);
          peer.removeEventListener("icegatheringstatechange", finish);
          resolve();
        };
        const timeout = setTimeout(() => {
          peer.removeEventListener("icegatheringstatechange", finish);
          reject(new Error("Local ICE gathering timed out."));
        }, 10000);
        peer.addEventListener("icegatheringstatechange", finish);
        finish();
      });
    }
    const response = await fetch("/api/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Ames-Token": token },
      body: JSON.stringify(peer.localDescription),
    });
    if (!response.ok) throw new Error(`Offer rejected (${response.status}). Reopen the server link.`);
    const answer = await response.json();
    await peer.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
    return close;
  } catch (error) {
    close();
    throw error;
  }
}
