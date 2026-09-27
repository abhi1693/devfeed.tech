import { expect, it, vi } from "vitest";
vi.mock("@/lib/server/card-image", () => ({ cardImage: vi.fn() }));
import { cardImage } from "@/lib/server/card-image";
import { cardLogos } from "@/lib/server/card-logos";

it("deduplicates logo downloads and bounds concurrent work", async () => {
  let active = 0;
  let peak = 0;
  vi.mocked(cardImage).mockImplementation(async (url, kind, timeout) => {
    expect(kind).toBe("logo");
    expect(timeout).toBeLessThanOrEqual(5000);
    peak = Math.max(peak, ++active);
    await new Promise((resolve) => setTimeout(resolve, 1));
    active--;
    return url === "missing" ? null : "data:image/png;base64,logo";
  });
  const result = await cardLogos([null, "a", "b", "a", "c", "d", "e", "missing"]);
  expect(cardImage).toHaveBeenCalledTimes(6);
  expect(peak).toBe(4);
  expect(result.get("a")).toContain("data:image/png");
  expect(result.get("missing")).toBeNull();
});
