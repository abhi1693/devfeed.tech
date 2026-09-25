import { createSign } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFile, mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CWS_SCOPE = "https://www.googleapis.com/auth/chromewebstore";
const CWS_API = "https://chromewebstore.googleapis.com";
const EDGE_API = "https://api.addons.microsoftedge.microsoft.com/v1";
const EDGE_TOKEN_API =
  "https://login.microsoftonline.com/5c9eedce-81bc-42f3-8823-48ba6258b391/oauth2/v2.0/token";
const POLL_INTERVAL_MS = 5000;
const POLL_ATTEMPTS = 60;

function required(env, name) {
  const value = env[name]?.trim();
  if (!value) throw new Error(`Required release setting is missing: ${name}`);
  return value;
}

function encodeBase64Url(value) {
  return Buffer.from(value).toString("base64url");
}

export function serviceAccountAssertion(serviceAccount, now = Math.floor(Date.now() / 1000)) {
  if (!serviceAccount.client_email || !serviceAccount.private_key) {
    throw new Error("Chrome service account JSON must include client_email and private_key");
  }
  const header = encodeBase64Url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claims = encodeBase64Url(
    JSON.stringify({
      iss: serviceAccount.client_email,
      scope: CWS_SCOPE,
      aud: "https://oauth2.googleapis.com/token",
      iat: now,
      exp: now + 1800,
    }),
  );
  const unsigned = `${header}.${claims}`;
  const signer = createSign("RSA-SHA256");
  signer.update(unsigned);
  return `${unsigned}.${signer.sign(serviceAccount.private_key).toString("base64url")}`;
}

async function jsonResponse(response, label) {
  const raw = await response.text();
  let body = {};
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      throw new Error(`${label} returned a non-JSON response (HTTP ${response.status})`);
    }
  }
  if (!response.ok) {
    const reason = body.error?.message ?? body.message ?? `HTTP ${response.status}`;
    throw new Error(`${label} failed: ${String(reason).slice(0, 500)}`);
  }
  return body;
}

async function getChromeToken(serviceAccount, fetchImpl) {
  const assertion = serviceAccountAssertion(serviceAccount);
  const response = await fetchImpl("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion,
    }),
  });
  const body = await jsonResponse(response, "Chrome Web Store token exchange");
  if (!body.access_token) throw new Error("Chrome token exchange returned no access token");
  return body.access_token;
}

function versionsInRevision(revision) {
  return (revision?.distributionChannels ?? [])
    .map((channel) => channel.crxVersion)
    .filter(Boolean);
}

async function chromeStatus({ fetchImpl, token, itemPath }) {
  const response = await fetchImpl(`${CWS_API}/v2/${itemPath}:fetchStatus`, {
    headers: { authorization: `Bearer ${token}` },
  });
  return jsonResponse(response, "Chrome Web Store status check");
}

async function pollChromeUpload({ fetchImpl, token, itemPath, initial, wait }) {
  if (initial.crxVersion) return initial;
  if (initial.uploadState !== "UPLOAD_IN_PROGRESS") {
    throw new Error(`Chrome upload did not complete (state: ${initial.uploadState ?? "unknown"})`);
  }
  for (let attempt = 0; attempt < POLL_ATTEMPTS; attempt += 1) {
    await wait(POLL_INTERVAL_MS);
    const status = await chromeStatus({ fetchImpl, token, itemPath });
    if (status.lastAsyncUploadState === "UPLOAD_COMPLETE") return status;
    if (status.lastAsyncUploadState && status.lastAsyncUploadState !== "UPLOAD_IN_PROGRESS") {
      throw new Error(`Chrome upload ended in state ${status.lastAsyncUploadState}`);
    }
  }
  throw new Error("Chrome package upload did not finish within five minutes");
}

export async function publishChrome({
  archive,
  version,
  serviceAccountJson,
  publisherId,
  extensionId,
  fetchImpl = fetch,
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  let serviceAccount;
  try {
    serviceAccount = JSON.parse(serviceAccountJson);
  } catch {
    throw new Error("CWS_SERVICE_ACCOUNT_JSON is not valid JSON");
  }
  const token = await getChromeToken(serviceAccount, fetchImpl);
  const itemPath = `publishers/${encodeURIComponent(publisherId)}/items/${encodeURIComponent(extensionId)}`;
  const current = await chromeStatus({ fetchImpl, token, itemPath });
  if (current.takenDown || current.warned) {
    throw new Error("Chrome listing is warned or taken down; refusing automatic submission");
  }

  const publishedVersions = versionsInRevision(current.publishedItemRevisionStatus);
  const submittedVersions = versionsInRevision(current.submittedItemRevisionStatus);
  if (publishedVersions.includes(version)) return "already published";
  if (submittedVersions.includes(version)) return "already submitted for review";
  if (submittedVersions.length > 0) {
    throw new Error(
      `Chrome has a different version under review (${submittedVersions.join(", ")}); refusing to replace it`,
    );
  }

  const uploadResponse = await fetchImpl(`${CWS_API}/upload/v2/${itemPath}:upload`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/zip" },
    body: archive,
  });
  const upload = await jsonResponse(uploadResponse, "Chrome Web Store package upload");
  const uploaded = await pollChromeUpload({
    fetchImpl,
    token,
    itemPath,
    initial: upload,
    wait,
  });
  if (uploaded.crxVersion && uploaded.crxVersion !== version) {
    throw new Error(`Chrome accepted package version ${uploaded.crxVersion}; expected ${version}`);
  }

  const publishResponse = await fetchImpl(`${CWS_API}/v2/${itemPath}:publish`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify({ publishType: "DEFAULT_PUBLISH", blockOnWarnings: true }),
  });
  const published = await jsonResponse(publishResponse, "Chrome Web Store publish request");
  return `submitted${published.state ? ` (${published.state})` : ""}`;
}

async function edgeRequest({
  fetchImpl,
  url,
  apiKey,
  clientId,
  method = "GET",
  body,
  contentType,
}) {
  const headers = { authorization: `ApiKey ${apiKey}`, "X-ClientID": clientId };
  if (contentType) headers["content-type"] = contentType;
  const response = await fetchImpl(url, { method, headers, body });
  return { response, body: await jsonResponse(response, "Microsoft Edge Add-ons API") };
}

function operationId(location) {
  if (!location) throw new Error("Edge Add-ons API did not return an operation location");
  const segments = new URL(location, EDGE_API).pathname.split("/").filter(Boolean);
  const id = segments.at(-1);
  if (!id) throw new Error("Edge Add-ons API returned an invalid operation location");
  return id;
}

async function pollEdgeOperation({ fetchImpl, baseUrl, apiKey, clientId, wait }) {
  for (let attempt = 0; attempt < POLL_ATTEMPTS; attempt += 1) {
    const { body } = await edgeRequest({
      fetchImpl,
      url: baseUrl,
      apiKey,
      clientId,
    });
    if (body.status === "Succeeded") return body;
    if (body.status !== "InProgress") {
      throw new Error(
        `Edge store operation failed (${body.status ?? "unknown"}): ${body.message ?? body.errorCode ?? "no details"}`,
      );
    }
    await wait(POLL_INTERVAL_MS);
  }
  throw new Error("Edge store operation did not finish within five minutes");
}

export async function publishEdge({
  archive,
  releaseTag,
  productId,
  clientId,
  apiKey,
  fetchImpl = fetch,
  wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
}) {
  const product = encodeURIComponent(productId);
  const uploadUrl = `${EDGE_API}/products/${product}/submissions/draft/package`;
  const { response: uploadResponse } = await edgeRequest({
    fetchImpl,
    url: uploadUrl,
    apiKey,
    clientId,
    method: "POST",
    body: archive,
    contentType: "application/zip",
  });
  if (uploadResponse.status !== 202) {
    throw new Error(`Edge package upload returned unexpected HTTP ${uploadResponse.status}`);
  }
  const uploadId = operationId(uploadResponse.headers.get("location"));
  const uploadBase = `${EDGE_API}/products/${product}/submissions/draft/package/operations/${encodeURIComponent(uploadId)}`;
  await pollEdgeOperation({ fetchImpl, baseUrl: uploadBase, apiKey, clientId, wait });

  const publishUrl = `${EDGE_API}/products/${product}/submissions`;
  const { response: publishResponse } = await edgeRequest({
    fetchImpl,
    url: publishUrl,
    apiKey,
    clientId,
    method: "POST",
    body: JSON.stringify({ notes: `DevFeed release ${releaseTag}; automated extension update.` }),
    contentType: "application/json",
  });
  if (publishResponse.status !== 202) {
    throw new Error(`Edge publish request returned unexpected HTTP ${publishResponse.status}`);
  }
  const publishId = operationId(publishResponse.headers.get("location"));
  const publishBase = `${EDGE_API}/products/${product}/submissions/operations/${encodeURIComponent(publishId)}`;
  const result = await pollEdgeOperation({
    fetchImpl,
    baseUrl: publishBase,
    apiKey,
    clientId,
    wait,
  });
  return `submitted${result.message ? ` (${result.message})` : ""}`;
}

function readReleaseBody(tag, exec = execFileSync) {
  return exec("gh", ["release", "view", tag, "--json", "body", "--jq", ".body"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trimEnd();
}

function writeReleaseBody(tag, body, exec = execFileSync) {
  return mkdtemp(join(tmpdir(), "devfeed-store-release-")).then(async (directory) => {
    const notes = join(directory, "release-notes.md");
    try {
      await writeFile(notes, `${body.trimEnd()}\n`, "utf8");
      exec("gh", ["release", "edit", tag, "--notes-file", notes], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
      });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
}

function releaseStoreLine(body, store) {
  const marker = `<!-- devfeed-store-${store} -->`;
  return body.split("\n").find((line) => line.includes(marker));
}

function updateStoreLine(body, store, label, text) {
  const marker = `<!-- devfeed-store-${store} -->`;
  const line = `- ${label}: ${text}. ${marker}`;
  const lines = body.split("\n");
  const index = lines.findIndex((current) => current.includes(marker));
  if (index < 0) throw new Error(`Release notes are missing the ${label} status marker`);
  lines[index] = line;
  return lines.join("\n");
}

function alreadySubmitted(line, version) {
  return Boolean(
    line &&
    /(?:submitted|published|already submitted|already published)/i.test(line) &&
    line.includes(`version \`${version}\``),
  );
}

export async function publishReleaseExtensions({
  chromeArchive,
  edgeArchive,
  releaseTag,
  version,
  env = process.env,
  fetchImpl = fetch,
  wait,
  readNotes = (tag) => readReleaseBody(tag),
  writeNotes = (tag, body) => writeReleaseBody(tag, body),
  log = (message) => console.log(message),
}) {
  const chromeSettings = {
    serviceAccountJson: required(env, "CWS_SERVICE_ACCOUNT_JSON"),
    publisherId: required(env, "CWS_PUBLISHER_ID"),
    extensionId: required(env, "CWS_EXTENSION_ID"),
  };
  const edgeSettings = {
    apiKey: required(env, "EDGE_ADDONS_API_KEY"),
    clientId: required(env, "EDGE_ADDONS_CLIENT_ID"),
    productId: required(env, "EDGE_ADDONS_PRODUCT_ID"),
  };

  let notes = await readNotes(releaseTag);
  for (const store of ["chrome", "edge"]) {
    const label = store === "chrome" ? "Chrome Web Store" : "Microsoft Edge Add-ons";
    if (alreadySubmitted(releaseStoreLine(notes, store), version)) {
      log(`${label}: release notes show version ${version} was already submitted.`);
      continue;
    }
    const result =
      store === "chrome"
        ? await publishChrome({
            archive: chromeArchive,
            version,
            ...chromeSettings,
            fetchImpl,
            ...(wait ? { wait } : {}),
          })
        : await publishEdge({
            archive: edgeArchive,
            releaseTag,
            ...edgeSettings,
            fetchImpl,
            ...(wait ? { wait } : {}),
          });
    notes = updateStoreLine(notes, store, label, `submitted version \`${version}\`; ${result}`);
    await writeNotes(releaseTag, notes);
    log(`${label}: ${result} for version ${version}.`);
  }
}

async function main() {
  const env = process.env;
  const releaseTag = required(env, "RELEASE_TAG");
  const manifest = JSON.parse(await readFile("apps/extensions/chrome/manifest.json", "utf8"));
  const version = manifest.version;
  const chromeArchive = await readFile(
    `apps/extensions/dist/devfeed-chrome-extension-${version}.zip`,
  );
  const edgeArchive = await readFile(`apps/extensions/dist/devfeed-edge-extension-${version}.zip`);
  await publishReleaseExtensions({ chromeArchive, edgeArchive, releaseTag, version, env });
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : "Extension store submission failed");
    process.exitCode = 1;
  });
}
