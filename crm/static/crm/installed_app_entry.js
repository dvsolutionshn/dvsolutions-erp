/* Presentation-only entry for an installed app; the server authorizes its URL. */
(() => {
  'use strict';
  const installed = window.navigator.standalone === true ||
    (typeof window.matchMedia === 'function' && window.matchMedia('(display-mode: standalone)').matches);
  if (!installed) return;
  const source = document.getElementById('clinicalInstalledAppUrl');
  if (!source) return;
  let target;
  try {
    const value = JSON.parse(source.textContent || 'null');
    if (typeof value !== 'string' || !value) return;
    target = new URL(value, window.location.href);
  } catch (_error) {
    return;
  }
  if (target.origin !== window.location.origin || target.pathname === window.location.pathname) return;
  window.location.replace(target.href);
})();
