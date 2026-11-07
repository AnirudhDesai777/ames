import { sessionToken } from "./auth";
import { startSession } from "./rtc";
import { mountConversation } from "./ui";

const close = mountConversation(document, sessionToken(window), window, startSession);
window.addEventListener("pagehide", close);
