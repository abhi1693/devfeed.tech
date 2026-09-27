import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/lib/server/public-dev-card", () => ({
  publicDevCard: vi.fn(),
  publicReadingActivity: vi.fn(),
}));

import { GET } from "@/app/api/v1/users/[username]/route";
import { publicDevCard, publicReadingActivity } from "@/lib/server/public-dev-card";

const profile = { username: "reader", display_name: "Reader", avatar_url: null };

beforeEach(() => vi.clearAllMocks());

it("skips reading activity for a card preview request", async () => {
  vi.mocked(publicDevCard).mockResolvedValue(profile);
  const response = await GET(
    new Request("https://devfeed.test/api/v1/users/reader?include_activity=false"),
    { params: Promise.resolve({ username: "reader" }) },
  );

  expect(await response.json()).toEqual({ profile });
  expect(publicReadingActivity).not.toHaveBeenCalled();
});

it("keeps reading activity in the public profile response by default", async () => {
  vi.mocked(publicDevCard).mockResolvedValue(profile);
  vi.mocked(publicReadingActivity).mockResolvedValue({ year: 2026, timezone: "UTC", days: [] });
  const response = await GET(new Request("https://devfeed.test/api/v1/users/reader"), {
    params: Promise.resolve({ username: "reader" }),
  });

  expect(await response.json()).toEqual({
    profile,
    activity: { year: 2026, timezone: "UTC", days: [] },
  });
  expect(publicReadingActivity).toHaveBeenCalledWith("reader");
});
