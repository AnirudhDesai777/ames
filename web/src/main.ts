import { sessionToken } from "./auth";
import { startSession } from "./rtc";
import { mountConversation } from "./ui";

const close = mountConversation(document, sessionToken(window), window.localStorage, startSession);
window.addEventListener("pagehide", close);
