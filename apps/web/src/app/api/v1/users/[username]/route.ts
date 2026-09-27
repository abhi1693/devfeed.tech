import { publicDevCard, publicReadingActivity } from "@/lib/server/public-dev-card";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ username: string }> }) {
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
  const includeActivity = new URL(request.url).searchParams.get("include_activity") !== "false";
  const activity = includeActivity
    ? await publicReadingActivity(profile.username ?? username)
    : undefined;
  return Response.json(includeActivity ? { profile, activity } : { profile }, {
    headers: { "Cache-Control": "private, no-store" },
  });
}
