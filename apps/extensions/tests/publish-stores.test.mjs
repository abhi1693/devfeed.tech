import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { test } from "node:test";
import { publishReleaseExtensions } from "../../../scripts/publish_extension_stores.mjs";

const version = "0.1.12";

function fixture() {
  const { privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
    publicKeyEncoding: { type: "spki", format: "pem" },
  });
  const serviceAccount = JSON.stringify({
    client_email: "release-bot@example.test",
    private_key: privateKey,
  });
  const env = {
    CWS_SERVICE_ACCOUNT_JSON: serviceAccount,
    CWS_PUBLISHER_ID: "publisher-id",
    CWS_EXTENSION_ID: "chrome-extension-id",
    EDGE_ADDONS_API_KEY: "edge-api-key",
    EDGE_ADDONS_CLIENT_ID: "edge-client-id",
    EDGE_ADDONS_PRODUCT_ID: "00000000-0000-4000-8000-000000000000",
  };
  let notes = [
    `- Chrome Web Store: automatic submission pending (version \`${version}\`). <!-- devfeed-store-chrome -->`,
    `- Microsoft Edge Add-ons: automatic submission pending (version \`${version}\`). <!-- devfeed-store-edge -->`,
  ].join("\n");
  const calls = [];
  const fetchImpl = async (input, options = {}) => {
    const url = new URL(input);
    calls.push({ url, options });
    if (url.hostname === "oauth2.googleapis.com") {
      assert.equal(options.method, "POST");
      assert.match(new URLSearchParams(options.body).get("assertion"), /^eyJ/);
      return Response.json({ access_token: "test-access-token" });
    }
    if (url.hostname === "chromewebstore.googleapis.com") {
      assert.equal(options.headers.authorization, "Bearer test-access-token");
      if (url.pathname.endsWith(":fetchStatus")) {
        return Response.json({
          publishedItemRevisionStatus: { distributionChannels: [{ crxVersion: "0.1.11" }] },
        });
      }
      if (url.pathname.includes(":upload")) {
        assert.equal(options.headers["content-type"], "application/zip");
        return Response.json({ crxVersion: version, uploadState: "UPLOAD_COMPLETE" });
      }
      if (url.pathname.endsWith(":publish")) {
        assert.deepEqual(JSON.parse(options.body), {
          publishType: "DEFAULT_PUBLISH",
          blockOnWarnings: true,
        });
        return Response.json({ state: "PENDING_REVIEW" });
      }
    }
    if (url.hostname === "api.addons.microsoftedge.microsoft.com") {
      assert.equal(options.headers.authorization, "ApiKey edge-api-key");
      assert.equal(options.headers["X-ClientID"], "edge-client-id");
      if (url.pathname.endsWith("/submissions/draft/package")) {
        assert.equal(options.method, "POST");
        assert.equal(options.headers["content-type"], "application/zip");
        return new Response(null, { status: 202, headers: { location: "/upload-operation" } });
      }
      if (url.pathname.endsWith("/submissions")) {
        assert.deepEqual(JSON.parse(options.body), {
          notes: "DevFeed release v0.1.12; automated extension update.",
        });
        return new Response(null, { status: 202, headers: { location: "/publish-operation" } });
      }
      if (url.pathname.includes("/operations/")) {
        return Response.json({ status: "Succeeded", message: "created submission" });
      }
    }
    throw new Error(`Unexpected request ${options.method ?? "GET"} ${url}`);
  };
  return {
    env,
    calls,
    fetchImpl,
    readNotes: async () => notes,
    writeNotes: async (_tag, value) => {
      notes = value;
    },
    get notes() {
      return notes;
    },
  };
}

test("submits release packages to both stores and records outcomes in release notes", async () => {
  const state = fixture();
  const results = [];
  await publishReleaseExtensions({
    chromeArchive: Buffer.from("chrome-zip"),
    edgeArchive: Buffer.from("edge-zip"),
    releaseTag: "v0.1.12",
    version,
    env: state.env,
    fetchImpl: state.fetchImpl,
    wait: async () => {},
    readNotes: state.readNotes,
    writeNotes: state.writeNotes,
    log: (result) => results.push(result),
  });

  assert.ok(state.calls.some(({ url }) => url.pathname.includes("/upload/v2/publishers/")));
  assert.ok(state.calls.some(({ url }) => url.pathname.endsWith(":publish")));
  assert.ok(state.calls.some(({ url }) => url.pathname.endsWith("/submissions/draft/package")));
  assert.ok(state.calls.some(({ url }) => url.pathname.endsWith("/submissions")));
  assert.match(state.notes, /Chrome Web Store: submitted version `0\.1\.12`/);
  assert.match(state.notes, /Microsoft Edge Add-ons: submitted version `0\.1\.12`/);
  assert.equal(results.length, 2);

  const requestCount = state.calls.length;
  await publishReleaseExtensions({
    chromeArchive: Buffer.from("chrome-zip"),
    edgeArchive: Buffer.from("edge-zip"),
    releaseTag: "v0.1.12",
    version,
    env: state.env,
    fetchImpl: state.fetchImpl,
    readNotes: state.readNotes,
    writeNotes: state.writeNotes,
    log: () => {},
  });
  assert.equal(state.calls.length, requestCount, "release reruns skip completed submissions");
});
