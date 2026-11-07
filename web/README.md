# Web foundation

Use Node 22.18 or newer within Node 22 (`nvm use` reads `.nvmrc`):

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
page; the authenticated echo runs from the built page on port 8765.

The client moves `#t=...` into `sessionStorage` and clears the fragment from the
address bar. Connect echo asks for the microphone, gathers local ICE candidates,
then sends an offer with `X-Ames-Token` in a header. The token is never sent in a
query string or offer body. Use headphones to avoid feeding the echo back into
the microphone. Playback has a native play button if Chrome blocks autoplay.

`peerConfiguration()` supplies an empty `iceServers` list. Reconnecting closes
the previous local peer, and a server replacement notice releases the old tab's
microphone and keeps the notice visible. The built assets are local; there are
no CDN assets or remote fonts. Conversation controls and transcript UI are S4.

The tests run in Node without a browser, microphone, or network connection.
