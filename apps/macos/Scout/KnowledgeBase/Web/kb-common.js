// Shared bridge for Scout's bundled knowledge-base pages (kb-map, kb-reader).
// Swift → page: window.scoutReceive({type, ...}).  Page → Swift: scout.post(type, payload).
(function () {
  'use strict';
  const handlers = {};

  window.scout = {
    post(type, payload) {
      const message = Object.assign({ type }, payload || {});
      try {
        window.webkit.messageHandlers.scout.postMessage(message);
      } catch (e) {
        // Opened outside the app (a browser, while developing): log instead.
        console.log('[scout.post]', message);
      }
    },
    on(type, fn) { handlers[type] = fn; },
    reducedMotion() {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    },
    /// A CSS custom property from the theme Swift injected (`--paper` → "rgb(…)").
    token(name) {
      return getComputedStyle(document.documentElement).getPropertyValue('--' + name).trim();
    },
    escape(text) {
      return String(text).replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));
    },
  };

  window.scoutReceive = function (message) {
    const handler = handlers[message && message.type];
    if (!handler) return;
    try { handler(message); } catch (e) { reportError(e); }
  };

  function reportError(e) {
    scout.post('jsError', {
      message: String((e && e.message) || e),
      source: (e && e.filename) || '',
      line: (e && e.lineno) || 0,
    });
  }
  window.addEventListener('error', reportError);
  window.addEventListener('unhandledrejection', (e) => reportError(e.reason));

  // Signal readiness once the page's own scripts have registered handlers.
  window.addEventListener('load', () => scout.post('ready'));
})();
