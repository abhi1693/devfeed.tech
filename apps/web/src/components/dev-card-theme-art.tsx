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
            <g key={row} className={`dev-card-circuit dev-card-circuit-${row}`}>
              <path
                d={`M280 ${64 + row * 38}h${70 + (row % 2) * 50}l24 20h100`}
                opacity={0.3 + row * 0.12}
              />
              <path
                className="dev-card-signal"
                visibility="hidden"
                d={`M280 ${64 + row * 38}h${70 + (row % 2) * 50}l24 20h100`}
                pathLength="100"
                strokeWidth="4"
                strokeLinecap="round"
                strokeDasharray="7 93"
              />
            </g>
          ))}
          <path d="m300 218 14 10-14 10" strokeWidth="4" />
          <path className="dev-card-cursor" d="M328 238h30" strokeWidth="4" />
        </g>
      )}
      {theme === "aurora" && (
        <g aria-hidden="true" fill="none" stroke={`url(#${id}-color)`}>
          {[0, 1, 2, 3].map((row) => (
            <path
              key={row}
              className={`dev-card-wave dev-card-wave-${row}`}
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
          {[0, 1, 2].map((ring) => (
            <circle
              key={ring}
              className={`dev-card-ripple dev-card-ripple-${ring}`}
              visibility="hidden"
              cx="424"
              cy="110"
              r="100"
              strokeWidth="1.5"
            />
          ))}
          <path d="M290 224h208" strokeWidth="5" />
          <path d="M290 238h112" strokeWidth="2" opacity=".4" />
        </g>
      )}
    </g>
  );
}
