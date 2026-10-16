import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { build } from "esbuild";

const root = fileURLToPath(new URL("../", import.meta.url));
const compiled = await build({
  entryPoints: ["packages/http/src/reader-destinations.ts"],
  absWorkingDir: root,
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
});
const reader = await import(
  `data:text/javascript;base64,${Buffer.from(compiled.outputFiles[0].text).toString("base64")}`
);

function python(wires, records = []) {
  const result = spawnSync(
    "uv",
    [
      "run",
      "--locked",
      "python",
      "-c",
      `import json, sys
from devfeed_http.reader_destinations import parse_reader_destination, serialize_reader_destination
values = json.load(sys.stdin)
destinations = [parse_reader_destination(wire) for wire in values["wires"]]
json.dump({
    "destinations": [destination.as_record() if destination else None for destination in destinations],
    "locations": [serialize_reader_destination(destination) for destination in destinations],
    "records": [serialize_reader_destination(record) for record in values["records"]],
}, sys.stdout, ensure_ascii=True)
`,
    ],
    {
      cwd: root,
      input: JSON.stringify({ wires, records }),
      encoding: "utf8",
      timeout: 30_000,
    },
  );
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

const samples = {
  articleSlug: "structured-login",
  topicSlug: "python",
  sourceSlug: "engineering",
  tag: "C++",
  username: "reader_123",
};

// Exercise every declared destination, including each finite route parameter.
const declared = reader.readerDestinationContract.routes.flatMap((route) => {
  if (!route.login) return [];
  let paths = [""];
  for (const segment of route.segments) {
    const values =
      typeof segment === "string"
        ? [segment]
        : Object.values(reader.readerDestinationContract.parameters[segment.type].values ?? {})
              .length
          ? Object.values(reader.readerDestinationContract.parameters[segment.type].values)
          : [samples[segment.type]];
    assert.ok(
      values.every((value) => typeof value === "string"),
      route.id,
    );
    paths = paths.flatMap((path) => values.map((value) => `${path}/${encodeURIComponent(value)}`));
  }
  const required = reader.readerDestinationContract.queryProfiles[route.query].required ?? [];
  const query = new URLSearchParams(required.map((name) => [name, "a".repeat(43)])).toString();
  return paths.map((path) => `${path || "/"}${query ? `?${query}` : ""}`);
});

test("Python and reader navigation resolve every declared login destination identically", () => {
  const paths = [
    ...declared,
    "/settings/",
    "/latest?sort=most_liked&content_type=tutorial&source_id=00000000-0000-0000-0000-000000000000",
    "/search?q=100%25%20~*%2B%2F&section=articles&content_types=news&sort=oldest&date_from=2024-02-29&date_to=2026-10-15",
    "/tags/%E6%95%B0%E6%8D%AE%E5%BA%93/news?q=%F0%9F%90%8D",
    "/tags/!~*'()%25?q=%252F%20test%20%7e",
    "/search?q=" + encodeURIComponent("🐍".repeat(100)),
    "/search?q=https%3A%2F%2Fexample.com%2F&sort=newest",
    "/search?q=%5Cd+C%3A%5CWindows&sort=newest",
    "/latest?sort=newest&q=cursor&cursor=next%2Bpage",
    "/sources?offset=00020",
    "/topics?offset=1000000",
    "/search?date_from=0001-01-01",
    "/search?date_to=9999-12-31",
    "/search?q=",
    "/latest?",
  ];
  const actual = python(paths);
  for (const [index, path] of paths.entries()) {
    const destination = reader.decodeReaderLoginDestination(path);
    assert.ok(destination, path);
    assert.deepEqual(actual.destinations[index], destination, path);
    const canonical = reader.serializeReaderLoginDestination(destination);
    assert.ok(canonical, path);
    assert.equal(actual.locations[index], canonical, path);
    assert.deepEqual(reader.decodeReaderLoginDestination(canonical), destination, path);
  }
});

test("both consumers reject ambiguous, external, malformed and unsupported destinations", () => {
  const paths = [
    "https://devfeed.tech/latest",
    "//evil.example/latest",
    "/\\evil.example/latest",
    "/latest#",
    "/latest\r\nLocation:evil",
    "/latest ",
    "/lat%65st",
    "/%74opics/python",
    "/topics/python/../news",
    "/topics/python/%2e%2e/news",
    "/topics/python/%2Fnews",
    "/topics/python/%252Fnews",
    "/topics//python",
    "/latest//",
    "/tags/%2e%2e",
    "/tags/%5Cevil",
    "/tags/%FF",
    "/tags/%ED%A0%80",
    "/tags/\ud800",
    "/search?q=\ud800",
    "/search?q=%FF",
    "/search?q=%ED%A0%80",
    "/search?q=%0d%0aLocation%3Aevil",
    "/search?q=%xx",
    "/search?q=one&%71=two",
    "/search?q=one&",
    "/search?q=one&&sort=newest",
    "/search?q",
    "/search?=one",
    "/search?__proto__=evil",
    "/search?next=https%3A%2F%2Fevil.example",
    "/search?section=unknown",
    "/search?sort=most_liked",
    "/search?content_types=article,news",
    "/search?date_from=0000-01-01",
    "/search?date_from=2026-02-29",
    "/search?q=" + encodeURIComponent("🐍".repeat(101)),
    "/topics?offset=-1",
    "/topics?offset=1.2",
    "/topics?offset=1000001",
    "/topics?offset=" + "9".repeat(5000),
    "/latest?source_id=not-a-uuid",
    "/latest?sort=relevance",
    "/latest?content_type=unknown",
    "/mcp/authorize",
    "/mcp/authorize?request=" + "a".repeat(42),
    "/mcp/authorize?request=" + "a".repeat(44),
    "/mcp/authorize?request=" + "a".repeat(43) + "&request=" + "b".repeat(43),
    "/login",
    "/register",
    "/my-feed",
    "/missing",
  ];
  const actual = python(paths);
  for (const [index, path] of paths.entries()) {
    assert.equal(reader.decodeReaderLoginDestination(path), null, path);
    assert.equal(actual.destinations[index], null, path);
    assert.equal(actual.locations[index], null, path);
  }
});

test("stored destination records require the same exact structure in both runtimes", () => {
  const records = [
    null,
    [],
    {},
    "/latest",
    ...[null, false, 123, "value", []].flatMap((value) => [
      { routeId: "latest", parameters: value, query: {} },
      { routeId: "latest", parameters: {}, query: value },
    ]),
    { routeId: "latest", parameters: {}, query: {}, href: "//evil.example" },
    { routeId: "latest", parameters: { slug: "ignored" }, query: {} },
    { routeId: "latest", parameters: {}, query: { q: "bad\r\nvalue" } },
    { routeId: "latest", parameters: {}, query: { q: "\ud800" } },
    { routeId: "latest", parameters: {}, query: { q: 2 } },
    { routeId: "article", parameters: { slug: "../../login" }, query: {} },
    { routeId: "tags-detail", parameters: { slug: "C:\\Windows" }, query: {} },
    {
      routeId: "topics-content",
      parameters: { slug: "python", contentType: "tutorials" },
      query: {},
    },
    { routeId: "mcp-authorize", parameters: {}, query: {} },
    { routeId: "login", parameters: {}, query: {} },
    { routeId: "unknown", parameters: {}, query: {} },
    { routeId: 123, parameters: {}, query: {} },
  ];
  const actual = python([], records);
  for (const [index, record] of records.entries()) {
    assert.equal(reader.serializeReaderLoginDestination(record), null, JSON.stringify(record));
    assert.equal(actual.records[index], null, JSON.stringify(record));
  }
});

test("query names come only from the matched route's declared fields", () => {
  const names = [
    "__proto__",
    "constructor",
    "prototype",
    "hasOwnProperty",
    "toString",
    "valueOf",
    "__defineGetter__",
    "q.__proto__",
    "q[__proto__]",
    "__proto__[polluted]",
  ];
  const paths = names.flatMap((name) => [
    `/search?${encodeURIComponent(name)}=injected`,
    `/search?q=valid&${encodeURIComponent(name)}=injected`,
    `/search?${encodeURIComponent(name)}=injected&q=valid`,
  ]);
  const actual = python(paths);
  for (const [index, path] of paths.entries()) {
    assert.equal(reader.decodeReaderLoginDestination(path), null, path);
    assert.equal(actual.destinations[index], null, path);
    assert.equal(reader.readerSignInDestination(path), "/", path);
  }

  for (const value of names) {
    const path = `/search?q=${encodeURIComponent(value)}`;
    const destination = reader.decodeReaderLoginDestination(path);
    assert.deepEqual(destination?.query, { q: value }, path);
    assert.equal(reader.readerSignInDestination(path), path, path);
  }
});

test("sign-in always validates destinations and safely recovers invalid input", () => {
  const invalid = [
    null,
    false,
    [],
    {},
    "/login",
    "//evil.example/latest",
    "/search?q=one&%71=two",
    "/mcp/authorize?request=short",
    { routeId: "login", parameters: {}, query: {} },
    { routeId: "unknown", parameters: {}, query: {} },
    { routeId: "article", parameters: { slug: "../login" }, query: {} },
    { routeId: "latest", parameters: {}, query: { next: "//evil.example" } },
    { routeId: "latest", parameters: {}, query: {}, href: "//evil.example" },
  ];
  for (const value of invalid)
    assert.equal(reader.readerSignInDestination(value), "/", JSON.stringify(value));

  for (const path of declared) {
    const destination = reader.decodeReaderLoginDestination(path);
    const canonical = reader.serializeReaderLoginDestination(destination);
    assert.equal(reader.readerSignInDestination(path), canonical, path);
    assert.equal(reader.readerSignInDestination(destination), canonical, path);
  }
});
