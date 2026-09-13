export const readerEase = "cubic-bezier(.2,.8,.2,1)";

/** Progressive enhancement: interactions never depend on animation support. */
export function animateReader(
  element: Element | null,
  frames: Keyframe[],
  options: KeyframeAnimationOptions = {},
) {
  if (!element?.animate || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches)
    return null;
  return element.animate(frames, { duration: 160, easing: readerEase, ...options });
}
