/* Presentation only: the original PlanTratamientoPacienteForm validates and saves the plan. */
(() => {
  'use strict';
  document.body.classList.add('mpl-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const functionsNav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsNav) { functionsNav.classList.add('active'); functionsNav.setAttribute('aria-current', 'page'); }
  const form = document.getElementById('mobilePlanForm');
  if (!form) return;
  const text = form.elements.namedItem('texto');
  const review = document.getElementById('planReview'), confirm = document.getElementById('confirmPlanSave');
  const confirmSheet = document.getElementById('planConfirmSheet'), unsavedSheet = document.getElementById('planUnsavedSheet');
  if (!text || !confirmSheet || !confirm) return;
  let dirty = form.dataset.planUnsaved === '1', confirmed = false, pendingURL;
  const resetConfirmation = () => { confirmed = false; };
  form.addEventListener('input', () => { dirty = true; resetConfirmation(); });
  form.addEventListener('change', () => { dirty = true; resetConfirmation(); });
  form.addEventListener('invalid', event => {
    resetConfirmation();
    let ancestor = event.target.parentElement;
    while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; }
  }, { capture: true });
  const openConfirmation = () => {
    if (form.dataset.submitting === 'true' || !form.reportValidity()) return;
    document.getElementById('planConfirmText').textContent = text.value;
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close());
    resetConfirmation(); confirmSheet.showModal();
  };
  review?.addEventListener('click', openConfirmation);
  form.addEventListener('submit', event => {
    if (!confirmed) { event.preventDefault(); event.stopImmediatePropagation(); openConfirmation(); return; }
    confirmed = false;
  }, { capture: true });
  form.addEventListener('submit', event => {
    if (!event.defaultPrevented) { dirty = false; if (review) review.disabled = true; confirm.disabled = true; }
  });
  confirm.addEventListener('click', () => {
    if (!confirmSheet.open || form.dataset.submitting === 'true' || !form.reportValidity()) return;
    confirmSheet.close(); confirmed = true; form.requestSubmit();
  });
  confirmSheet.addEventListener('close', () => { if (form.dataset.submitting !== 'true') resetConfirmation(); });
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || !unsavedSheet || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href;
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsavedSheet.showModal();
  }));
  document.getElementById('confirmPlanLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { resetConfirmation(); if (review) review.disabled = false; confirm.disabled = false; });
  const firstError = form.querySelector('[data-plan-field]:has(.mi-error)');
  if (firstError) requestAnimationFrame(() => { firstError.scrollIntoView({ block: 'center' }); firstError.querySelector('textarea,input:not([type=hidden]),select')?.focus({ preventScroll: true }); });
})();
