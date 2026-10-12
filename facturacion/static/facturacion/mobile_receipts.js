/* Presentation only. ReciboPagoForm changes receipt document fields, not the payment. */
(() => {
  'use strict';
  document.body.classList.add('mrc-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const functionsNav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsNav) { functionsNav.classList.add('active'); functionsNav.setAttribute('aria-current', 'page'); }
  const form = document.getElementById('mobileReceiptForm'); if (!form) return;
  const review = document.getElementById('receiptReview'), confirm = document.getElementById('confirmReceiptSave');
  const confirmSheet = document.getElementById('receiptConfirmSheet'), unsavedSheet = document.getElementById('receiptUnsavedSheet');
  if (!confirmSheet || !confirm) return;
  let dirty = form.dataset.receiptUnsaved === '1', confirmed = false, pendingURL, reviewValues;
  const resetConfirmation = () => { confirmed = false; };
  const openAncestors = target => { let ancestor = target.parentElement; while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; } };
  form.addEventListener('input', () => { dirty = true; resetConfirmation(); });
  form.addEventListener('change', () => { dirty = true; resetConfirmation(); });
  form.addEventListener('invalid', event => { resetConfirmation(); openAncestors(event.target); }, { capture: true });
  const openConfirmation = () => {
    if (form.dataset.submitting === 'true' || !form.reportValidity()) return;
    reviewValues = Object.fromEntries(['fecha', 'referencia', 'concepto'].map(name => [name, form.elements.namedItem(name).value]));
    Object.entries(reviewValues).forEach(([name, value]) => { document.querySelector(`[data-receipt-confirm="${name}"]`).textContent = value || (name === 'referencia' ? 'Sin referencia' : name === 'concepto' ? 'Sin concepto' : 'Sin fecha'); });
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); resetConfirmation(); confirmSheet.showModal();
  };
  review?.addEventListener('click', openConfirmation);
  form.addEventListener('submit', event => {
    if (!confirmed) { event.preventDefault(); event.stopImmediatePropagation(); openConfirmation(); return; }
    confirmed = false;
  }, { capture: true });
  form.addEventListener('submit', event => { if (!event.defaultPrevented) { dirty = false; if (review) review.disabled = true; confirm.disabled = true; } });
  confirm.addEventListener('click', () => {
    if (!confirmSheet.open || form.dataset.submitting === 'true' || !form.reportValidity()) return;
    if (!reviewValues || Object.entries(reviewValues).some(([name, value]) => form.elements.namedItem(name).value !== value)) { openConfirmation(); return; }
    confirmSheet.close(); confirmed = true; form.requestSubmit();
  });
  confirmSheet.addEventListener('close', () => { if (form.dataset.submitting !== 'true') resetConfirmation(); });
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || !unsavedSheet || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href; document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsavedSheet.showModal();
  }));
  document.getElementById('confirmReceiptLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { resetConfirmation(); if (review) review.disabled = false; confirm.disabled = false; });
  const firstError = form.querySelector('[data-receipt-field]:has(.mi-error)');
  if (firstError) { openAncestors(firstError); requestAnimationFrame(() => { firstError.scrollIntoView({ block: 'center' }); firstError.querySelector('input:not([type=hidden]),textarea,select')?.focus({ preventScroll: true }); }); }
})();
