import { DevCardThemeArt } from "@/components/dev-card-theme-art";
import { cardThemeTokens } from "@devfeed/theme/dev-card";
import { ImageResponse } from "next/og";
import { publicDevCard } from "@/lib/server/public-dev-card";
import { devCardData } from "@/lib/dev-card";
import { devCardStatIcons } from "@/components/dev-card-stat-icons";

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
  const tokens = cardThemeTokens(card.theme, card.accent);
  const surface = tokens["--card"] ?? "#102b29";
  const foreground = tokens["--card-foreground"] ?? "#f5fffc";
  const accent = tokens["--chart-1"] ?? "#b8f56b";
  const secondary = tokens["--secondary"] ?? "#234840";
  const image = new ImageResponse(
    <div
      style={{
        display: "flex",
        position: "relative",
        width: "100%",
        height: "100%",
        background: surface,
        padding: 38,
        color: foreground,
        fontFamily: "sans-serif",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          border: `2px solid ${tokens["--border"] ?? "#37615a"}`,
          borderRadius: card.theme === "terminal" ? 8 : 28,
          padding: 38,
          background:
            card.theme === "aurora" ? `linear-gradient(135deg, ${secondary}, ${surface})` : surface,
        }}
      >
        {card.theme !== "classic" && (
          <div style={{ display: "flex", position: "absolute", right: 40, top: 45, opacity: 0.24 }}>
            <svg width="350" height="280" viewBox="270 20 270 244">
              <defs>
                <linearGradient id="social-card-color" x1="0" y1="0" x2="1" y2="1">
                  <stop stopColor={accent} />
                  <stop offset="1" stopColor={tokens["--chart-5"] ?? accent} />
                </linearGradient>
              </defs>
              {DevCardThemeArt({ theme: card.theme, id: "social-card", color: accent })}
            </svg>
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 110,
              height: 110,
              borderRadius: 24,
              background: accent,
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
            <div
              style={{
                display: "flex",
                fontSize: 24,
                color: card.theme === "minimal" ? foreground : accent,
                marginTop: 8,
              }}
            >
              @{card.username}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", fontSize: 24, marginTop: 24, lineHeight: 1.4 }}>
          {card.bio}
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 24, flexWrap: "wrap" }}>
          {card.technologies.map(({ name }) => (
            <div
              key={name}
              style={{
                display: "flex",
                padding: "10px 18px",
                borderRadius: 12,
                background: secondary,
                fontSize: 23,
              }}
            >
              {name}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 54, marginTop: "auto", paddingTop: 22 }}>
          {card.stats.map((stat) => {
            const Icon = devCardStatIcons[stat.id];
            return (
              <div key={stat.id} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 38 }}>
                  <Icon
                    width={24}
                    height={24}
                    stroke={tokens["--muted-foreground"] ?? "#b7d0c7"}
                    strokeWidth={1.75}
                  />
                  {stat.value}
                </div>
                <div
                  style={{
                    display: "flex",
                    fontSize: 15,
                    color: tokens["--muted-foreground"] ?? "#b7d0c7",
                  }}
                >
                  {stat.label}
                </div>
              </div>
            );
          })}
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            marginTop: 24,
            fontSize: 22,
            color: card.theme === "minimal" ? foreground : accent,
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
