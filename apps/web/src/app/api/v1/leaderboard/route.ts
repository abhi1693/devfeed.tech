import { userApiOrigin } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const response = await fetch(`${userApiOrigin()}/v1/user/leaderboard`, {
      credentials: "omit",
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(10000)]),
    });
    if (!response.ok) throw new Error("Leaderboard unavailable");
    return Response.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json(
      { detail: "Leaderboard unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
