/** Isolate Google's standard widget and its inline styles from the reader document. */
export function preferredSourceWidgetPolicy(nonce: string) {
  return [
    "default-src 'none'",
    `script-src 'nonce-${nonce}' https://news.google.com/swg/js/v1/publisher.js`,
    "script-src-attr 'none'",
    "style-src 'unsafe-inline'",
    "img-src https://www.gstatic.com/images/branding/productlogos/googleg_gradient/v1/24px.svg",
    "frame-src https://news.google.com/swg/ui/v1/serviceiframe https://news.google.com/swg/ui/v1/toastiframe",
    "connect-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'self'",
  ].join("; ");
}

const bootstrap = `(() => {
  const status = document.getElementById('status');
  const retry = document.getElementById('retry');
  let observedToast = null;
  let reportedHeight = 0;
  const resize = new ResizeObserver(() => reportSize());
  function reportSize() {
    const toast = document.querySelector('iframe.publisher-toast');
    if (toast !== observedToast) {
      resize.disconnect();
      observedToast = toast;
      if (toast) resize.observe(toast);
    }
    const toastHeight = toast ? Math.ceil(toast.getBoundingClientRect().height) : 0;
    const height = Math.min(400, 52 + (toastHeight > 0 ? toastHeight + 12 : 0));
    if (height === reportedHeight) return;
    reportedHeight = height;
    if (parent !== window) parent.postMessage(
      {type: 'devfeed:preferred-source-size', height}, location.origin);
  }
  new MutationObserver(reportSize).observe(document.body, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'hidden']
  });
  reportSize();
  retry.addEventListener('click', () => location.reload());
  document.addEventListener('click', (event) => {
    if (!event.isTrusted || event.button !== 0) return;
    const path = event.composedPath();
    const button = path.find((node) => node instanceof Element &&
      node.localName === 'publisher-md-outlined-button');
    if (!button || button.getAttribute('aria-disabled') === 'true' ||
      button.hasAttribute('disabled') || button.hasAttribute('soft-disabled')) return;
    if (!path.some((node) => node instanceof Element &&
      node.hasAttribute('google-add-preferred-source-btn'))) return;
    if (parent !== window) parent.postMessage(
      {type: 'devfeed:preferred-source-click'}, location.origin);
  }, true);
  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://news.google.com/swg/js/v1/publisher.js';
  script.nonce = document.currentScript.nonce;
  script.addEventListener('load', () => { status.hidden = true; });
  script.addEventListener('error', () => {
    status.textContent = 'Google’s button could not load.';
    retry.hidden = false;
  });
  document.head.appendChild(script);
})();`;

export function preferredSourceWidgetHtml(nonce: string, theme: "light" | "dark") {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Add DevFeed as a preferred source on Google</title>
<style>html{color-scheme:${theme}}html,body{margin:0;background:transparent}body{min-height:52px;display:grid;justify-items:end;align-content:start;color:${theme === "dark" ? "#e8eaed" : "#3c4043"};font:12px system-ui;text-align:center}#control{width:min(100%,240px);min-height:52px;display:grid;place-items:center;align-content:center;gap:4px}.publisher-toast{color-scheme:normal}p{margin:0}button{font:inherit}button[hidden],p[hidden]{display:none}</style>
</head><body>
<div id="control"><div google-add-preferred-source-btn data-theme="${theme}"></div>
<p id="status" role="status">Loading Google’s button…</p><button id="retry" type="button" hidden>Retry</button>
</div>
<script nonce="${nonce}">${bootstrap}</script>
</body></html>`;
}
