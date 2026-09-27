export const cardThemes = [
  { id: "classic", name: "Classic", description: "The original colorful mosaic" },
  { id: "terminal", name: "Terminal", description: "Dark canvas with crisp circuit lines" },
  { id: "aurora", name: "Aurora", description: "Sweeping color on a midnight canvas" },
  { id: "minimal", name: "Minimal", description: "Warm paper and clean geometric lines" },
] as const;
export const cardAccents = [
  { id: "default", name: "Theme default", color: "#646464" },
  { id: "teal", name: "Teal", color: "#45c6ac" },
  { id: "violet", name: "Violet", color: "#aa96ef" },
  { id: "blue", name: "Blue", color: "#65b9ec" },
  { id: "amber", name: "Amber", color: "#edb45e" },
  { id: "rose", name: "Rose", color: "#f08bb5" },
] as const;
export type CardTheme = (typeof cardThemes)[number]["id"];
export type CardAccent = (typeof cardAccents)[number]["id"];

/** Shared by SVG artwork, PNG downloads, and social previews. */
export function cardThemeTokens(theme: CardTheme = "classic", accent: CardAccent = "default") {
  const tokens: Record<string, string> = {};
  if (theme !== "classic") {
    const light = theme === "minimal";
    Object.assign(tokens, {
      "--card": light ? "#fcfaf5" : theme === "terminal" ? "#101b19" : "#16162e",
      "--card-foreground": light ? "#292820" : "#f2f5fa",
      "--secondary": light ? "#eeeade" : theme === "terminal" ? "#1d302b" : "#262640",
      "--secondary-foreground": light ? "#292820" : "#f2f5fa",
      "--muted-foreground": light ? "#686354" : "#b4becb",
      "--border": light ? "#d9d3c4" : "#42475b",
      "--logo-background": "#ffffff",
      "--logo-foreground": "#262626",
      "--font-family-sans": "Arial, Helvetica, sans-serif",
      "--chart-1": light ? "#168572" : theme === "terminal" ? "#45c6ac" : "#aa96ef",
      "--chart-5": light ? "#b26b13" : theme === "terminal" ? "#65b9ec" : "#f08bb5",
      "--chart-6": light ? "#8762be" : "#65b9ec",
    });
  }
  if (accent !== "default") {
    const selected = cardAccents.find((item) => item.id === accent);
    if (selected) tokens["--chart-1"] = selected.color;
  }
  return tokens;
}
