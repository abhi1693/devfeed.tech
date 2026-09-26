import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
export async function signInResponse(path, provider = "https://identity.example/authorize") {
  const { stdout } = await run(
    "uv",
    [
      "run",
      "--locked",
      "python",
      fileURLToPath(new URL("./sign-in.py", import.meta.url)),
      path,
      provider,
    ],
    { cwd: fileURLToPath(new URL("../../", import.meta.url)) },
  );
  return JSON.parse(stdout);
}

export async function checkGuestTopicSignIn(
  page,
  topicBase,
  extension = false,
  provider = "https://identity.example/authorize",
) {
  for (const suffix of ["?sort=most_liked", "/articles", "/articles?sort=oldest"]) {
    await page.goto(topicBase + suffix);
    const follow = page
      .getByRole("region", { name: "Feed controls" })
      .getByRole("link", { name: "Follow", exact: true });
    const popup = extension ? page.context().waitForEvent("page") : null;
    await follow.click();
    const destination = popup ? await popup : page;
    await destination.waitForURL((url) => url.pathname === "/login");
    const continueLink = destination.getByRole("link", { name: "Continue to sign in" });
    await continueLink.waitFor();
    const request = destination.context().waitForEvent("request", {
      predicate: (request) => new URL(request.url()).pathname === "/api/v1/user/auth/login",
    });
    const response = destination.context().waitForEvent("response", {
      predicate: (response) => new URL(response.url()).pathname === "/api/v1/user/auth/login",
    });
    await continueLink.click();
    const login = await request;
    const expected = new URL(topicBase + suffix.replace("?language=en", ""));
    assert.equal(
      new URL(login.url()).searchParams.get("return_to"),
      extension ? "/extension/login-complete" : expected.pathname + expected.search,
    );
    const result = await response;
    assert.equal(result.status(), 302);
    const providerUrl = new URL(provider);
    const location = new URL(result.headers().location);
    assert.equal(location.origin + location.pathname, providerUrl.origin + providerUrl.pathname);
    await destination.waitForURL(
      (url) => url.origin + url.pathname === providerUrl.origin + providerUrl.pathname,
    );
    await destination.getByText("Sign-in provider", { exact: true }).waitFor();
    if (extension) await destination.close();
  }
}
