import test from "node:test";
import { checkLoginRecovery } from "../../../../scripts/testing/login-recovery.mjs";

test(
  "reader retries a cancelled login and returns to its saved destination",
  { timeout: 120000 },
  (t) => checkLoginRecovery(t),
);
