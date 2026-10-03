const assert = require("node:assert/strict");
const { test } = require("node:test");
const braces = require("braces");
const micromatch = require("micromatch");

test("transitive consumers resolve the bounded local braces package", () => {
  assert.equal(require("braces/package.json").name, "@devfeed/braces");
  assert.equal(require.resolve("braces"), require.resolve("../packages/braces"));
});

test("deep patterns fail safely below the upstream character cap", () => {
  for (const [open, close] of [
    ["{", "}"],
    ["(", ")"],
  ]) {
    const pattern = open.repeat(3500) + "a,b" + close.repeat(3500);
    assert.ok(pattern.length < 10000);
    for (const method of [braces, braces.parse, braces.compile, braces.expand, braces.stringify]) {
      assert.throws(() => method(pattern), { name: "SyntaxError", message: /maximum depth/ });
    }
    assert.throws(() => braces.parse(open.repeat(3500)), {
      name: "SyntaxError",
      message: /maximum depth/,
    });
  }
});

test("walker limits cover caller-supplied deep and cyclic ASTs", () => {
  let ast = { type: "text", value: "hello" };
  for (let depth = 0; depth < 3500; depth++) ast = { type: "root", nodes: [ast] };
  const cyclic = { type: "root", nodes: [] };
  cyclic.nodes.push(cyclic);
  for (const method of [braces.compile, braces.expand, braces.stringify]) {
    for (const input of [ast, cyclic]) {
      assert.throws(() => method(input), { name: "SyntaxError", message: /maximum depth/ });
    }
  }
});

test("normal nesting, ranges, escapes, and glob matching remain compatible", () => {
  assert.deepEqual(braces.expand("src/{web,admin}/{1..3}.js"), [
    "src/web/1.js",
    "src/web/2.js",
    "src/web/3.js",
    "src/admin/1.js",
    "src/admin/2.js",
    "src/admin/3.js",
  ]);
  assert.equal(braces.compile("{a,{b,c}}"), "(a|(b|c))");
  assert.equal(braces.stringify(braces.parse("{a,{b,c}}")), "{a,{b,c}}");
  assert.deepEqual(braces.expand("\\{a,b\\}"), ["{a,b}"]);
  assert.deepEqual(braces.expand("{".repeat(90) + "x" + "}".repeat(90)), [
    "{".repeat(90) + "x" + "}".repeat(90),
  ]);
  assert.throws(() => braces.expand("{1..2000}"), /range limit/);
  assert.deepEqual(
    micromatch(["src/web/a.ts", "src/admin/b.tsx", "docs/a.md"], "src/{web,admin}/**/*.{ts,tsx}"),
    ["src/web/a.ts", "src/admin/b.tsx"],
  );
});
