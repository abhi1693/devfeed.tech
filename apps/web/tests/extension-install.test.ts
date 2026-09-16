import { expect, it } from "vitest";
import { extensionBrowser } from "@/lib/extension-install";

it.each([
  ["Mozilla/5.0 Chrome/130.0.0.0 Safari/537.36", "https:", "chrome"],
  ["Mozilla/5.0 Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0", "https:", "edge"],
  ["Mozilla/5.0 Firefox/130.0", "https:", null],
  ["Mozilla/5.0 Version/18.0 Safari/605.1.15", "https:", null],
  ["Mozilla/5.0 Chrome/130.0.0.0 OPR/115.0", "https:", null],
  ["Mozilla/5.0 Android Chrome/130.0.0.0 Mobile", "https:", null],
  ["Mozilla/5.0 iPhone CriOS/130.0.0.0 Mobile", "https:", null],
  ["Mozilla/5.0 Chrome/130.0.0.0", "chrome-extension:", null],
  ["Mozilla/5.0 Chrome/130.0.0.0 Edg/130.0.0.0", "chrome-extension:", null],
])("detects the appropriate supported browser for %s on %s", (agent, protocol, expected) => {
  expect(extensionBrowser(agent, protocol)).toBe(expected);
});
