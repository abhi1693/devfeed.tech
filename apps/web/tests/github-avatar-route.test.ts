import { beforeEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("@/lib/server/card-image", () => ({ cardImage: vi.fn() }));
import { cardImage } from "@/lib/server/card-image";
import { GET } from "@/app/api/avatars/github/[id]/[size]/route";

function request(id = "5083532", size = "128", query = "?v=4", headers = {}) {
  return GET(
    new Request(`https://devfeed.test/api/avatars/github/${id}/${size}${query}`, { headers }),
    {
      params: Promise.resolve({ id, size }),
    },
  );
}
beforeEach(() => vi.resetAllMocks());
it.each([
  ["../private", "128", ""],
  ["user", "128", ""],
  ["123", "10000", ""],
  ["123", "0128", ""],
  ["123", "128", "?url=http://127.0.0.1"],
  ["123", "128", "?v=https://evil.test"],
  ["123", "128", "?v=1&v=2"],
])(
  "rejects invalid identity, size and queries before fetching: %s/%s%s",
  async (id, size, query) => {
    const response = await request(id, size, query);
    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(cardImage).not.toHaveBeenCalled();
  },
);
it("serves cacheable WebP independently of cookies and supports ETag revalidation", async () => {
  const bytes = Buffer.from("webp fixture");
  vi.mocked(cardImage).mockResolvedValue(`data:image/webp;base64,${bytes.toString("base64")}`);
  const response = await request(undefined, undefined, undefined, {
    Cookie: "private-session=ignored",
  });
  expect(cardImage).toHaveBeenCalledWith(
    "https://avatars.githubusercontent.com/u/5083532?s=128&v=4",
    "avatar",
    5000,
    128,
  );
  expect(response.headers.get("content-type")).toBe("image/webp");
  expect(response.headers.get("cache-control")).toBe("public, max-age=3600");
  expect(response.headers.has("set-cookie")).toBe(false);
  expect(Buffer.from(await response.arrayBuffer())).toEqual(bytes);
  const unchanged = await request(undefined, undefined, undefined, {
    "If-None-Match": response.headers.get("etag")!,
  });
  expect(unchanged.status).toBe(304);
  expect(
    (
      await request(undefined, undefined, undefined, {
        "If-None-Match": `W/${response.headers.get("etag")}`,
      })
    ).status,
  ).toBe(304);
  expect(await unchanged.text()).toBe("");
  vi.mocked(cardImage).mockResolvedValue("data:image/webp;base64,Y2hhbmdlZA==");
  expect((await request()).headers.get("etag")).not.toBe(response.headers.get("etag"));
});
it("does not cache unavailable, broken or rejected upstream images", async () => {
  vi.mocked(cardImage).mockResolvedValue(null);
  const response = await request();
  expect(response.status).toBe(404);
  expect(response.headers.get("cache-control")).toBe("no-store");
});
