let sequence = 0;

// Keep observations in Node so a navigation cannot erase already observed calls.
export async function observeAnimations(page, action) {
  const name = `__devfeedMotion${++sequence}`;
  const animations = [];
  let active = true;
  await page.exposeFunction(name, (target) => {
    if (active) animations.push(target);
  });
  const install = (binding) => {
    const state = { original: Element.prototype.animate, pending: [] };
    window[`${binding}State`] = state;
    Element.prototype.animate = function (...args) {
      state.pending.push(
        window[binding](typeof this.className === "string" ? this.className : this.tagName),
      );
      return state.original.apply(this, args);
    };
  };
  // Playwright bindings and the initializer both survive document replacement.
  await page.addInitScript(install, name);
  await page.evaluate(install, name);
  try {
    await action();
    await page.evaluate(async (binding) => {
      await Promise.all(window[`${binding}State`].pending);
    }, name);
    return animations;
  } finally {
    active = false;
    await page.evaluate((binding) => {
      Element.prototype.animate = window[`${binding}State`].original;
    }, name);
  }
}
