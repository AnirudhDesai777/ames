export function sessionToken(browser: Pick<Window, "location" | "history" | "sessionStorage">): string | null {
  const token = new URLSearchParams(browser.location.hash.slice(1)).get("t");
  if (token) {
    browser.sessionStorage.setItem("ames-token", token);
    browser.history.replaceState(null, "", browser.location.pathname + browser.location.search);
  }
  return browser.sessionStorage.getItem("ames-token");
}
