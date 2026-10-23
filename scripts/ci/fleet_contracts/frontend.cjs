// Exercise the actual standalone image under Fleet's filesystem restrictions.
const assert = require("node:assert/strict");
const fs = require("node:fs");

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
  fs.writeFileSync("/tmp/fleet-smoke", "writable tmp");
  fs.writeFileSync(`/app/apps/${component}/.next/cache/fleet-smoke`, "writable cache");
  assert.throws(() => fs.writeFileSync("/app/readonly", "bad"), { code: "EROFS" });
}
