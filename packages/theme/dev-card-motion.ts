// Seeded values keep server renders, previews, and exports consistent while
// giving neighboring dots unrelated colors, delays, and cycle lengths.
function dotNoise(index: number, salt: number) {
  let value = Math.imul(index + 1, 374761393) ^ Math.imul(salt, 668265263);
  value = Math.imul(value ^ (value >>> 13), 1274126177);
  return ((value ^ (value >>> 16)) >>> 0) / 4294967296;
}

export const classicCardDots = Array.from({ length: 275 }, (_, index) => ({
  x: 30 + (index % 25) * 20,
  y: 30 + Math.floor(index / 25) * 20,
  color: Math.floor(dotNoise(index, 1) * 3),
  opacity: dotNoise(index, 2) > 0.65 ? [0.3, 0.6, 1][Math.floor(dotNoise(index, 3) * 3)] : 0.06,
  duration: `${(3.4 + dotNoise(index, 4) * 4.8).toFixed(3)}s`,
  delay: `${(-dotNoise(index, 5) * 12).toFixed(3)}s`,
}));

/** Embedded in SVGs so motion also works without page styles or JavaScript. */
export const cardMotionCss = `
@keyframes devfeed-card-dot {
  0%, 12%, 58%, 100% { opacity: 0; }
  32% { opacity: .9; }
}
@keyframes devfeed-card-signal {
  from { stroke-dashoffset: 7; }
  to { stroke-dashoffset: -100; }
}
@keyframes devfeed-card-cursor {
  0%, 45%, 100% { opacity: 1; }
  55%, 90% { opacity: .2; }
}
@keyframes devfeed-card-drift {
  from { transform: translate(-12px, 8px) rotate(-3deg); }
  to { transform: translate(14px, -10px) rotate(3deg); }
}
@keyframes devfeed-card-ripple {
  0% { transform: scale(.55); opacity: 0; }
  20% { opacity: .65; }
  75% { opacity: .3; }
  100% { transform: scale(1.25); opacity: 0; }
}
.dev-card-artwork[data-card-motion="animated"][data-card-theme="classic"] .dev-card-dot {
  animation-name: devfeed-card-dot;
  animation-timing-function: ease-in-out;
  animation-iteration-count: infinite;
}
.dev-card-artwork[data-card-motion="animated"] .dev-card-signal {
  visibility: visible;
  animation: devfeed-card-signal 3.6s linear infinite;
}
.dev-card-artwork[data-card-motion="animated"] .dev-card-circuit-1 .dev-card-signal { animation-delay: -1.4s; }
.dev-card-artwork[data-card-motion="animated"] .dev-card-circuit-2 .dev-card-signal { animation-delay: -2.8s; }
.dev-card-artwork[data-card-motion="animated"] .dev-card-circuit-3 .dev-card-signal { animation-delay: -.7s; }
.dev-card-artwork[data-card-motion="animated"] .dev-card-circuit-4 .dev-card-signal { animation-delay: -2.1s; }
.dev-card-artwork[data-card-motion="animated"] .dev-card-cursor {
  animation: devfeed-card-cursor 1.8s ease-in-out infinite;
}
.dev-card-artwork[data-card-motion="animated"] .dev-card-wave {
  transform-origin: 400px 140px;
  animation: devfeed-card-drift 9s ease-in-out infinite alternate;
}
.dev-card-artwork[data-card-motion="animated"] .dev-card-wave-1 { animation-duration: 12s; animation-delay: -5s; }
.dev-card-artwork[data-card-motion="animated"] .dev-card-wave-2 { animation-duration: 10s; animation-delay: -8s; animation-direction: alternate-reverse; }
.dev-card-artwork[data-card-motion="animated"] .dev-card-wave-3 { animation-duration: 14s; animation-delay: -3s; }
.dev-card-artwork[data-card-motion="animated"] .dev-card-ripple {
  visibility: visible;
  transform-origin: 424px 110px;
  animation: devfeed-card-ripple 7.2s linear infinite;
}
.dev-card-artwork[data-card-motion="animated"] .dev-card-ripple-1 { animation-delay: -2.4s; }
.dev-card-artwork[data-card-motion="animated"] .dev-card-ripple-2 { animation-delay: -4.8s; }
@media (prefers-reduced-motion: reduce) {
  .dev-card-artwork[data-card-motion="animated"] .dev-card-motion-layer,
  .dev-card-artwork[data-card-motion="animated"] .dev-card-motion-layer * {
    animation: none !important;
  }
  .dev-card-artwork[data-card-motion="animated"] .dev-card-signal,
  .dev-card-artwork[data-card-motion="animated"] .dev-card-ripple {
    visibility: hidden;
  }
}
`;
