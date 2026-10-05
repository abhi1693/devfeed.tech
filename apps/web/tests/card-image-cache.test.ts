import { EventEmitter } from "node:events";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import sharp from "sharp";

vi.mock("server-only", () => ({}));
vi.mock("node:dns/promises", () => ({ resolve4: vi.fn() }));
vi.mock("node:https", () => ({ get: vi.fn() }));
vi.mock("node:http", () => ({ get: vi.fn() }));
import { resolve4 } from "node:dns/promises";
import { get } from "node:https";

let images: typeof import("@/lib/server/card-image");
let png: Buffer;
let now: number;
const url = (name: string) => `https://logos.example.test/${name}.png`;
beforeAll(async () => {
  png = await sharp({
    create: { width: 32, height: 32, channels: 4, background: "red" },
  })
    .png()
    .toBuffer();
});
beforeEach(async () => {
  vi.resetModules();
  images = await import("@/lib/server/card-image");
  now = 1_000_000;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
});
afterEach(() => {
  vi.resetAllMocks();
  vi.restoreAllMocks();
});

function replies(body = png) {
  vi.mocked(get).mockImplementation(((
    _url: unknown,
    _options: unknown,
    callback: (response: unknown) => void,
  ) => {
    const response = Object.assign(new EventEmitter(), {
      statusCode: 200,
      headers: { "content-type": "image/png" },
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

it("shares in-flight requests and keeps sanitized cache reads free of network work", async () => {
  replies();
  expect(images.cachedCardImage(url("shared"), "logo")).toBeNull();
  expect(get).not.toHaveBeenCalled();
  const [first, second] = await Promise.all([
    images.cardImage(url("shared"), "logo"),
    images.cardImage(url("shared"), "logo"),
  ]);
  expect(first).toBe(`data:image/png;base64,${png.toString("base64")}`);
  expect(second).toBe(first);
  expect(images.cachedCardImage(url("shared"), "logo")).toBe(first);
  expect(get).toHaveBeenCalledTimes(1);
  now += 30 * 60 * 1000 - 1;
  expect(await images.cardImage(url("shared"), "logo")).toBe(first);
  expect(get).toHaveBeenCalledTimes(1);
  now += 1;
  expect(images.cachedCardImage(url("shared"), "logo")).toBeNull();
  expect(await images.cardImage(url("shared"), "logo")).toBe(first);
  expect(get).toHaveBeenCalledTimes(2);
});

it("retries failures after fifteen seconds and clears the completed in-flight request", async () => {
  vi.mocked(resolve4).mockRejectedValue(new Error("Temporary DNS failure"));
  expect(await images.cardImage(url("retry"), "logo")).toBeNull();
  now += 14_999;
  expect(await images.cardImage(url("retry"), "logo")).toBeNull();
  expect(resolve4).toHaveBeenCalledTimes(1);
  now += 1;
  vi.mocked(resolve4).mockResolvedValue(["93.184.215.14"] as never);
  replies();
  expect(await images.cardImage(url("retry"), "logo")).toContain("data:image/png;base64,");
  expect(resolve4).toHaveBeenCalledTimes(2);
});

it("keeps logo and avatar representations separate and ignores empty requests", async () => {
  replies();
  const logo = await images.cardImage(url("kinds"), "logo");
  const avatar = await images.cardImage(url("kinds"), "avatar");
  expect(logo).toContain("data:image/png;base64,");
  expect(avatar).toContain("data:image/webp;base64,");
  expect(images.cachedCardImage(url("kinds"), "logo")).toBe(logo);
  expect(images.cachedCardImage(url("kinds"), "avatar")).toBe(avatar);
  for (const empty of [null, undefined, ""]) {
    expect(images.cachedCardImage(empty, "logo")).toBeNull();
    expect(await images.cardImage(empty, "logo")).toBeNull();
  }
  expect(await images.cardImage(url("no-time"), "logo", 0)).toBeNull();
  expect(await images.cardImage(url("no-time"), "logo", -1)).toBeNull();
  expect(get).toHaveBeenCalledTimes(2);
});

it("evicts the least recently used image when the entry limit is reached", async () => {
  replies();
  for (let index = 0; index < 96; index++) await images.cardImage(url(`entry-${index}`), "logo");
  expect(images.cachedCardImage(url("entry-0"), "logo")).toBeTruthy();
  await images.cardImage(url("overflow"), "logo");
  expect(images.cachedCardImage(url("entry-1"), "logo")).toBeNull();
  expect(images.cachedCardImage(url("entry-0"), "logo")).toBeTruthy();
  expect(images.cachedCardImage(url("entry-95"), "logo")).toBeTruthy();
});

it("bounds cached bytes even when there are fewer than ninety-six images", async () => {
  replies(Buffer.concat([png, Buffer.alloc(1024 * 1024)]));
  for (let index = 0; index < 6; index++) await images.cardImage(url(`large-${index}`), "logo");
  expect(images.cachedCardImage(url("large-0"), "logo")).toBeNull();
  expect(images.cachedCardImage(url("large-5"), "logo")).toBeTruthy();
});
