import { EventEmitter } from "node:events";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("node:dns/promises", () => ({ resolve4: vi.fn() }));
vi.mock("node:https", () => ({ get: vi.fn() }));
vi.mock("node:http", () => ({ get: vi.fn() }));
import { resolve4 } from "node:dns/promises";
import { get } from "node:https";
import { cardAvatar } from "@/lib/server/card-avatar";
import { cardImage } from "@/lib/server/card-image";
import sharp from "sharp";

let png: Buffer;
beforeAll(async () => {
  png = await sharp({
    create: { width: 32, height: 32, channels: 4, background: { r: 240, g: 80, b: 40, alpha: 1 } },
  })
    .png()
    .toBuffer();
});
function reply(body: Buffer, status = 200, headers = {}) {
  vi.mocked(get).mockImplementationOnce(((
    _url: unknown,
    _options: unknown,
    callback: (response: unknown) => void,
  ) => {
    const response = Object.assign(new EventEmitter(), {
      statusCode: status,
      headers,
      destroy: vi.fn(),
    });
    queueMicrotask(() => {
      callback(response);
      response.emit("data", body);
      response.emit("end");
    });
    return new EventEmitter();
  }) as typeof get);
}
afterEach(() => vi.resetAllMocks());

it("embeds raster bytes and pins the connection while preserving the TLS and Host names", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(png);
  const image = await cardAvatar("https://avatars.example.test/photo.png");
  expect(typeof image).toBe("string");
  expect(image).toMatch(/^data:image\/png;base64,/);
  expect(await cardAvatar("https://avatars.example.test/photo.png")).toBe(image);
  const bytes = Buffer.from(image!.split(",")[1], "base64");
  expect(await sharp(bytes).metadata()).toMatchObject({ width: 256, height: 256, format: "png" });
  expect(get).toHaveBeenCalledWith(
    expect.any(URL),
    expect.objectContaining({
      hostname: "93.184.215.14",
      servername: "avatars.example.test",
      headers: expect.objectContaining({ Host: "avatars.example.test" }),
    }),
    expect.any(Function),
  );
  expect(get).toHaveBeenCalledTimes(1);
});

it.each([
  "127.0.0.1",
  "10.1.2.3",
  "169.254.169.254",
  "192.168.1.1",
  "100.64.0.1",
  "0.0.0.0",
  "224.0.0.1",
])("does not fetch private or reserved hosts: %s", async (address) => {
  vi.mocked(resolve4).mockResolvedValue([address] as never);
  expect(await cardAvatar(`https://avatars.example.test/private-${address}.png`)).toBeNull();
  expect(get).not.toHaveBeenCalled();
});

it("revalidates redirect targets before connecting", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(Buffer.alloc(0), 302, { location: "http://127.0.0.1/private" });
  expect(await cardAvatar("https://avatars.example.test/private-redirect.png")).toBeNull();
  expect(get).toHaveBeenCalledTimes(1);
});

it("follows public redirects", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(Buffer.alloc(0), 302, { location: "/new.png" });
  reply(png);
  expect(await cardAvatar("https://avatars.example.test/follow-redirect.png")).toContain(
    "data:image/png;base64,",
  );
  expect(get).toHaveBeenCalledTimes(2);
});

it.each([
  ["active.svg", Buffer.from('<svg onload="alert(1)"/>')],
  ["invalid.html", Buffer.from("<html>Error</html>")],
])("rejects active content and invalid images: %s", async (path, body) => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(body);
  expect(await cardAvatar(`https://avatars.example.test/${path}`)).toBeNull();
});

it("falls back when the image is too large or DNS fails", async () => {
  vi.mocked(resolve4)
    .mockResolvedValueOnce(["93.184.215.14"] as never)
    .mockRejectedValueOnce(new Error("DNS failed"));
  reply(png, 200, { "content-length": String(3 * 1024 * 1024) });
  expect(await cardAvatar("https://avatars.example.test/too-large.png")).toBeNull();
  expect(await cardAvatar("https://avatars.example.test/dns-failure.png")).toBeNull();
});

it("rasterizes SVG logos to a bounded PNG without retaining active or remote content", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(
    Buffer.from(
      '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><script>alert(1)</script><image href="http://127.0.0.1/private.png"/><rect width="40" height="20" fill="#ff0000"/></svg>',
    ),
    200,
    { "content-type": "image/svg+xml" },
  );
  const image = await cardImage("https://logos.example.test/logo.svg", "logo");
  expect(image).toMatch(/^data:image\/png;base64,/);
  const bytes = Buffer.from(image!.split(",")[1], "base64");
  expect(await sharp(bytes).metadata()).toMatchObject({ width: 96, height: 96, format: "png" });
  const { data, info } = await sharp(bytes)
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  const center = (48 * info.width + 48) * info.channels;
  expect([...data.subarray(center, center + 4)]).toEqual([255, 0, 0, 255]);
  expect(get).toHaveBeenCalledTimes(1);
});

it("rejects corrupt and oversized decoded logo images", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(
    Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="100000" height="100000"/>'),
    200,
    { "content-type": "image/svg+xml" },
  );
  expect(await cardImage("https://logos.example.test/huge.svg", "logo")).toBeNull();
  reply(Buffer.from("not an SVG"), 200, { "content-type": "image/svg+xml" });
  expect(await cardImage("https://logos.example.test/bad.svg", "logo")).toBeNull();
  reply(Buffer.alloc(0), 200, { "content-type": "image/svg+xml" });
  expect(await cardImage("https://logos.example.test/empty.svg", "logo")).toBeNull();
});
