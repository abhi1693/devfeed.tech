import { EventEmitter } from "node:events";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("node:dns/promises", () => ({ resolve4: vi.fn() }));
vi.mock("node:https", () => ({ get: vi.fn() }));
vi.mock("node:http", () => ({ get: vi.fn() }));
import { resolve4 } from "node:dns/promises";
import { get } from "node:https";
import { get as httpGet } from "node:http";
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
afterEach(() => {
  vi.resetAllMocks();
  vi.useRealTimers();
});

it.each([
  "ftp://avatars.example.test/photo.png",
  "https://user@avatars.example.test/photo.png",
  "https://:password@avatars.example.test/photo.png",
  "https://avatars.example.test:8080/photo.png",
])("rejects unsafe image URLs before resolving or connecting: %s", async (url) => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(png);
  expect(await cardAvatar(url)).toBeNull();
  expect(resolve4).not.toHaveBeenCalled();
  expect(get).not.toHaveBeenCalled();
  expect(httpGet).not.toHaveBeenCalled();
});

it.each(
  [[], ["93.184.215.14", "127.0.0.1"], ["::1"], ["invalid"]].map((addresses) => ({ addresses })),
)("rejects an empty or partly unsafe DNS answer: %j", async ({ addresses }) => {
  vi.mocked(resolve4).mockResolvedValue(addresses as never);
  expect(
    await cardAvatar(`https://avatars.example.test/dns-${addresses.join("-")}.png`),
  ).toBeNull();
  expect(get).not.toHaveBeenCalled();
});

it("validates public IP literals without resolving them again", async () => {
  reply(png);
  expect(await cardAvatar("https://93.184.215.14/literal.png")).toContain(
    "data:image/webp;base64,",
  );
  expect(resolve4).not.toHaveBeenCalled();
});

it.each(["jpeg", "gif"] as const)("accepts a valid %s avatar", async (format) => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(await sharp(png).toFormat(format).toBuffer());
  expect(await cardAvatar(`https://avatars.example.test/photo.${format}`)).toContain(
    "data:image/webp;base64,",
  );
});

it("stops redirect loops after three hops", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  for (let index = 0; index < 5; index++) reply(Buffer.alloc(0), 302, { location: "/loop.png" });
  expect(await cardAvatar("https://avatars.example.test/redirect-loop.png")).toBeNull();
  expect(get).toHaveBeenCalledTimes(4);
});

it("does not follow redirects without a target or accept an error response", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(Buffer.alloc(0), 302);
  expect(await cardAvatar("https://avatars.example.test/no-location.png")).toBeNull();
  reply(png, 500);
  expect(await cardAvatar("https://avatars.example.test/server-error.png")).toBeNull();
  expect(get).toHaveBeenCalledTimes(2);
});

it("aborts a stalled image and clears its timeout after a successful response", async () => {
  vi.useFakeTimers();
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  const aborted = vi.fn();
  vi.mocked(get).mockImplementationOnce(((_url: unknown, options: { signal: AbortSignal }) => {
    options.signal.addEventListener("abort", aborted);
    return new EventEmitter();
  }) as typeof get);
  const pending = cardImage("https://avatars.example.test/stalled.png", "avatar", 25);
  await vi.advanceTimersByTimeAsync(25);
  expect(await pending).toBeNull();
  expect(aborted).toHaveBeenCalledTimes(1);
  reply(png);
  const success = cardImage("https://avatars.example.test/fast.png", "avatar", 1000);
  await vi.advanceTimersByTimeAsync(0);
  expect(await success).toContain("data:image/webp;base64,");
  expect(vi.getTimerCount()).toBe(0);
});

it("embeds raster bytes and pins the connection while preserving the TLS and Host names", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  reply(png);
  const image = await cardAvatar("https://avatars.example.test/photo.png");
  expect(typeof image).toBe("string");
  expect(image).toMatch(/^data:image\/webp;base64,/);
  expect(await cardAvatar("https://avatars.example.test/photo.png")).toBe(image);
  const bytes = Buffer.from(image!.split(",")[1], "base64");
  expect(await sharp(bytes).metadata()).toMatchObject({ width: 256, height: 256, format: "webp" });
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
    "data:image/webp;base64,",
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

it("embeds managed WebP logos unchanged without reprocessing", async () => {
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  const body = await sharp(png).resize(96, 96).webp({ lossless: true }).toBuffer();
  reply(body, 200, { "content-type": "image/webp" });
  const image = await cardImage("https://logos.example.test/96.webp", "logo");
  expect(image).toBe(`data:image/webp;base64,${body.toString("base64")}`);
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

it.each(["png", "svg"])(
  "embeds the saved %s original when variants are unavailable",
  async (format) => {
    vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
    const body =
      format === "png"
        ? png
        : Buffer.from(
            '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="48" viewBox="0 0 96 48"><path fill="red" d="M0 0h96v48H0z"/></svg>',
          );
    const mime = format === "png" ? "image/png" : "image/svg+xml";
    reply(body, 200, { "content-type": mime });
    const image = await cardImage(
      `https://logos.example.test/originals/topic-logos/v1/hash.${format}`,
      "logo",
    );
    expect(image).toBe(`data:${mime};base64,${body.toString("base64")}`);
    const exported = await sharp(
      Buffer.from(
        `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><image width="96" height="96" href="${image}"/></svg>`,
      ),
    )
      .ensureAlpha()
      .raw()
      .toBuffer();
    expect(exported[(48 * 96 + 48) * 4 + 3]).toBe(255);
  },
);
