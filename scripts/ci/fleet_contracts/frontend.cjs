// Exercise the actual standalone image under Fleet's filesystem restrictions.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const [action, component] = process.argv.slice(2);
if (action === "ready") {
  const path = component === "web" ? "/" : "/login";
  fetch(`http://127.0.0.1:3000${path}`)
    .then(async (response) => {
      assert.ok(response.ok);
      assert.match(await response.text(), /DevFeed/);
    })
    .catch(() => process.exit(1));
} else {
  assert.equal(action, "permissions");
  assert.equal(process.getuid(), 1000);
  assert.equal(process.getgid(), 1000);
  for (const directory of [os.tmpdir(), `/app/apps/${component}/.next/cache`]) {
    const temporary = fs.mkdtempSync(path.join(directory, "fleet-smoke-"));
    try {
      const filename = path.join(temporary, "probe");
      fs.writeFileSync(filename, "writable mount", { flag: "wx" });
      assert.equal(fs.readFileSync(filename, "utf8"), "writable mount");
    } finally {
      fs.rmSync(temporary, { recursive: true });
    }
  }
  assert.throws(() => fs.writeFileSync("/app/readonly", "bad"), { code: "EROFS" });
}
