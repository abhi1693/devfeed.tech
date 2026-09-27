import type { CardTheme } from "@devfeed/theme/dev-card";

export function DevCardThemeArt({
  theme,
  id,
  color = "var(--chart-1)",
}: {
  theme: CardTheme;
  id: string;
  color?: string;
}) {
  return (
    <g>
      {theme === "terminal" && (
        <g aria-hidden="true" fill="none" stroke={color} strokeWidth="2">
          {[0, 1, 2, 3, 4].map((row) => (
            <path
              key={row}
              d={`M280 ${64 + row * 38}h${70 + (row % 2) * 50}l24 20h100`}
              opacity={0.3 + row * 0.12}
            />
          ))}
          <path d="m300 218 14 10-14 10m28 0h30" strokeWidth="4" />
        </g>
      )}
      {theme === "aurora" && (
        <g aria-hidden="true" fill="none" stroke={`url(#${id}-color)`}>
          {[0, 1, 2, 3].map((row) => (
            <path
              key={row}
              d={`M200 ${310 - row * 45}C320 ${-80 + row * 25} 420 ${370 - row * 35} 600 ${-40 + row * 25}`}
              strokeWidth={55 - row * 8}
              opacity={0.18 + row * 0.14}
            />
          ))}
        </g>
      )}
      {theme === "minimal" && (
        <g aria-hidden="true" fill="none" stroke={color}>
          <circle cx="424" cy="110" r="100" strokeWidth="2" />
          <circle cx="424" cy="110" r="74" strokeWidth="1" opacity=".4" />
          <path d="M290 224h208" strokeWidth="5" />
          <path d="M290 238h112" strokeWidth="2" opacity=".4" />
        </g>
      )}
    </g>
  );
}
