import assert from "node:assert/strict";
import test from "node:test";
import { sessionToken } from "../src/auth.ts";

test("token moves from the fragment to sessionStorage and leaves the address bar", () => {
  const values = new Map();
  let replacement;
  const browser = {
    location: { hash: "#t=local-secret", pathname: "/", search: "" },
    sessionStorage: {
      setItem: (key, value) => values.set(key, value),
      getItem: (key) => values.get(key) ?? null,
    },
    history: { replaceState: (...args) => { replacement = args; } },
  };
  assert.equal(sessionToken(browser), "local-secret");
  assert.deepEqual(replacement, [null, "", "/"]);
  browser.location.hash = "";
  assert.equal(sessionToken(browser), "local-secret");
  browser.location.hash = "#t=new-secret";
  assert.equal(sessionToken(browser), "new-secret");
});

test("a page without a fragment or stored token cannot authenticate", () => {
  assert.equal(sessionToken({
    location: { hash: "" },
    sessionStorage: { getItem: () => null },
  }), null);
});
