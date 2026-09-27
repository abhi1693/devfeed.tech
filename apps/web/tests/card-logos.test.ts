import { expect, it, vi } from "vitest";
vi.mock("@/lib/server/card-image", () => ({ cardImage: vi.fn(), cachedCardImage: vi.fn() }));
import { cachedCardImage, cardImage } from "@/lib/server/card-image";
import { cachedCardLogos, warmCardLogos } from "@/lib/server/card-logos";

it("returns cached logos synchronously and warms missing images with bounded concurrency", async () => {
  let active = 0;
  let peak = 0;
  vi.mocked(cachedCardImage).mockImplementation((url) =>
    url === "cached" ? "data:image/png;base64,cached" : null,
  );
  vi.mocked(cardImage).mockImplementation(async (url, kind, timeout) => {
    expect(kind).toBe("logo");
    expect(timeout).toBeLessThanOrEqual(5000);
    peak = Math.max(peak, ++active);
    await new Promise((resolve) => setTimeout(resolve, 1));
    active--;
    return url === "missing" ? null : "data:image/png;base64,logo";
  });
  const urls = [null, "cached", "a", "b", "a", "c", "d", "e", "missing"];
  const result = cachedCardLogos(urls);
  expect(result.get("cached")).toContain("data:image/png");
  expect(cardImage).not.toHaveBeenCalled();
  await warmCardLogos(urls);
  expect(cardImage).toHaveBeenCalledTimes(6);
  expect(peak).toBe(4);
  expect(result.get("a")).toBeNull();
});
