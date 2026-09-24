import { ImageResponse } from "next/og";
import { publicDevCard } from "@/lib/server/public-dev-card";
import { devCardData } from "@/lib/dev-card";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ username: string }> },
) {
  const profile = await publicDevCard((await params).username);
  if (!profile)
    return new Response("Card unavailable", {
      status: 404,
      headers: { "Cache-Control": "no-store" },
    });
  const card = devCardData(profile, { name: profile.username ?? "DevFeed reader" });
  const image = new ImageResponse(
    <div
      style={{
        display: "flex",
        width: "100%",
        height: "100%",
        background: "#071a1a",
        padding: 38,
        color: "#f5fffc",
        fontFamily: "sans-serif",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          border: "2px solid #37615a",
          borderRadius: 28,
          padding: 38,
          background: "#102b29",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 110,
              height: 110,
              borderRadius: 24,
              background: "#b8f56b",
              color: "#102b29",
              fontSize: 46,
            }}
          >
            {card.initials}
          </div>
          <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
            <div
              style={{
                display: "flex",
                fontSize: card.name.length > 35 ? 32 : 46,
                fontWeight: 700,
              }}
            >
              {card.name}
            </div>
            <div style={{ display: "flex", fontSize: 24, color: "#b8f56b", marginTop: 8 }}>
              @{card.username}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", fontSize: 24, marginTop: 24, lineHeight: 1.4 }}>
          {card.bio}
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 24, flexWrap: "wrap" }}>
          {card.technologies.map((name) => (
            <div
              key={name}
              style={{
                display: "flex",
                padding: "10px 18px",
                borderRadius: 12,
                background: "#234840",
                fontSize: 23,
              }}
            >
              {name}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 54, marginTop: "auto", paddingTop: 22 }}>
          {card.stats.map((stat) => (
            <div key={stat.label} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", fontSize: 38 }}>{stat.value}</div>
              <div style={{ display: "flex", fontSize: 15, color: "#b7d0c7" }}>{stat.label}</div>
            </div>
          ))}
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: 24,
            fontSize: 22,
            color: "#b8f56b",
          }}
        >
          <span>devfeed.</span>
          <span>My stack, on one card. Create yours.</span>
        </div>
      </div>
    </div>,
    { width: 1200, height: 630, headers: { "Cache-Control": "no-store" } },
  );
  return new Response(await image.arrayBuffer(), {
    headers: { "Content-Type": "image/png", "Cache-Control": "no-store" },
  });
}
