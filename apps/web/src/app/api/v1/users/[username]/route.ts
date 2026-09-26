import { publicDevCard, publicReadingActivity } from "@/lib/server/public-dev-card";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ username: string }> },
) {
  const { username } = await params;
  const profile = await publicDevCard(username);
  if (!profile)
    return Response.json(
      { detail: "Profile not found" },
      {
        status: 404,
        headers: { "Cache-Control": "private, no-store" },
      },
    );
  const activity = await publicReadingActivity(profile.username ?? username);
  return Response.json(
    { profile, activity },
    { headers: { "Cache-Control": "private, no-store" } },
  );
}
