/** Embedded in SVGs so motion also works without page styles or JavaScript. */
export const cardMotionCss = `
@keyframes devfeed-card-slide {
  from { transform: translateX(0); }
  to { transform: translateX(420px); }
}
@keyframes devfeed-card-flow {
  from { stroke-dashoffset: 0; }
  to { stroke-dashoffset: -184; }
}
@keyframes devfeed-card-drift {
  from { transform: translate(0, 0); }
  to { transform: translate(6px, -9px); }
}
@keyframes devfeed-card-breathe {
  from { transform: scale(.95); }
  to { transform: scale(1.04); }
}
.dev-card-artwork[data-card-motion="animated"][data-card-theme="classic"] .dev-card-motion-layer {
  animation: devfeed-card-slide 20s linear infinite;
}
.dev-card-artwork[data-card-motion="animated"][data-card-theme="terminal"] .dev-card-motion-layer path {
  stroke-dasharray: 80 12;
  animation: devfeed-card-flow 12s linear infinite;
}
.dev-card-artwork[data-card-motion="animated"][data-card-theme="aurora"] .dev-card-motion-layer {
  animation: devfeed-card-drift 8s ease-in-out infinite alternate;
}
.dev-card-artwork[data-card-motion="animated"][data-card-theme="minimal"] .dev-card-motion-layer circle {
  transform-origin: 424px 110px;
  animation: devfeed-card-breathe 7s ease-in-out infinite alternate;
}
@media (prefers-reduced-motion: reduce) {
  .dev-card-artwork[data-card-motion="animated"] .dev-card-motion-layer,
  .dev-card-artwork[data-card-motion="animated"] .dev-card-motion-layer * {
    animation: none !important;
    stroke-dasharray: none !important;
  }
}
`;
