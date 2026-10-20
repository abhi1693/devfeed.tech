import test from "node:test";
import { checkLoginRecovery } from "../../../scripts/testing/login-recovery.mjs";

test(
  "built extension recovers sign-in and completes in its original browser",
  { timeout: 120000 },
  (t) => checkLoginRecovery(t, process.env.DEVFEED_EXTENSION_BROWSER ?? "chrome"),
);
