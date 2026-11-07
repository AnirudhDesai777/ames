# Web foundation

Use Node 22.18 or newer within Node 22 (`nvm use` reads `.nvmrc`):

```sh
npm ci
npm run lint
npm test
npm run build
npm run dev
```

Vite serves a scaffold page at `http://127.0.0.1:5173`. It does not request the
microphone or open a session yet. `peerConfiguration()` provides the empty
`iceServers` list for the connection flow in S1b. The built assets are local;
there are no CDN assets or remote fonts.

The tests run in Node without a browser, microphone, or network connection.
