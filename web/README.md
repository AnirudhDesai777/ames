# Web client

Use Node 22.18 or newer within Node 22:

```sh
npm ci
npm run lint
npm test
npm run build
npm run dev
```

Build the client before starting the server from the repository root with
`AMES_STUB=1 uv run --locked python -m server.app`. Use the Chrome link opened by
the server at `http://127.0.0.1:8765`. Port 5173 serves only the Vite development
page; authenticated sessions run from the built page on port 8765.

The client moves `#t=...` into `sessionStorage` and clears the fragment from the
address bar. The first-run advisory must be acknowledged before starting a
conversation. Its acknowledgement lives in `localStorage`; clearing site data
shows it again. If storage is unavailable, acknowledgement lasts for that visit.

Start conversation asks for the microphone with echo cancellation, noise
suppression, and automatic gain control, gathers local ICE candidates, then
sends an offer with `X-Ames-Token` in a header. The token is never sent in a query
string or offer body. `peerConfiguration()` supplies an empty `iceServers` list.
Stub mode replies with tone audio through the live pipeline. Audio playback
contains native controls if Chrome blocks autoplay.

The orb reflects `speaking`, with a text label and
reduced-motion support. The live transcript updates each role and turn in place
until its final event. Transcript and notice content is rendered as plain text.
The transcript stays after End session and clears when a new conversation starts.
Mute microphone disables the outgoing audio track; incoming audio keeps playing.
End session closes the local peer and releases the microphone, including when
connection setup is still pending. Neither control sends an HTTP request.
The text box and Send button are enabled while a session is connected, muted or not. Enter or Send posts `{"type": "text", "text": "..."}` on the data channel and clears the box; the server treats it as a spoken turn, cutting off any reply in progress.
The Web search switch is off by default; it reads and sets `/api/search` with the token header, and a `search` event shows a notice while a query runs. The Saved memories panel loads `/api/memories` when opened and deletes a memory with `DELETE /api/memories/{id}`.
The Female voice and Male voice buttons work before and during a session. The choice is kept in `localStorage` and sent to the server as `set_voice` when a session connects and when it changes.

`session_replaced` and `session_ended` close the session and keep the server's
notice on screen until the user starts another conversation. `server_error`
shows the server's text while leaving the session open. Microphone denial shows
Chrome permission instructions; a lost connection offers Reconnect without
promising memory retention. Temporary WebRTC disconnections can recover, and a
server error notice survives later connection loss. Late events
from a closed session cannot replace a terminal notice or update a new session.

`server/events.py` defines the RTVI event payloads. `rtc.ts` validates and routes
them; `ui.ts` updates the existing HTML controls; `main.ts` supplies authentication
and closes the session on page exit. All assets are bundled locally, with no
UI framework, CDN assets, or remote fonts.

The tests use the existing Node test runner with LinkeDOM for component tests.
They require no browser, microphone, GPU, network, or downloaded models. Run
component acceptance tests with:

```sh
node --experimental-strip-types --test tests/ui.test.mjs
```

Browser checks for the maintainer: in Chrome, verify first-run acknowledgement,
mic permission denial and recovery, real audio playback and mute, End during
setup, connection loss and reconnect, keyboard navigation, reduced motion, and
narrow-window layout. Open a second session to check the replacement notice.
