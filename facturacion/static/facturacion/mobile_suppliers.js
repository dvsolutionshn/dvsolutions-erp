/* Supplier presentation only. ProveedorForm and its existing view own validation/save. */
(() => {
  'use strict';
  document.body.classList.add('msu-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const functionsNav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsNav) { functionsNav.classList.add('active'); functionsNav.setAttribute('aria-current', 'page'); }

  const form = document.getElementById('mobileSupplierForm');
  if (!form) return;
  const confirmSheet = document.getElementById('supplierConfirmSheet');
  const unsavedSheet = document.getElementById('supplierUnsavedSheet');
  const review = document.getElementById('supplierReview');
  const confirm = document.getElementById('confirmSupplierSave');
  const summary = document.getElementById('supplierConfirmValues');
  if (!confirmSheet || !confirm || !summary) return;
  let dirty = form.dataset.supplierUnsaved === '1', confirmed = false, reviewPayload, pendingURL;
  const resetConfirmation = () => { confirmed = false; };
  const payload = () => JSON.stringify([...new FormData(form).entries()]);
  const openAncestors = target => {
    let ancestor = target.parentElement;
    while (ancestor && ancestor !== form) {
      if (ancestor.tagName === 'DETAILS') ancestor.open = true;
      ancestor = ancestor.parentElement;
    }
  };
  const fieldValue = control => {
    if (control.type === 'checkbox') return control.checked ? 'Sí, activo' : 'No, inactivo';
    if (control.tagName === 'SELECT') return control.selectedOptions[0]?.textContent.trim() || 'Sin selección';
    return control.value || 'Sin dato';
  };
  const openConfirmation = () => {
    if (form.dataset.submitting === 'true' || !form.reportValidity()) return;
    resetConfirmation();
    reviewPayload = payload();
    summary.replaceChildren();
    form.querySelectorAll('[data-supplier-field]').forEach(field => {
      const control = field.querySelector('input:not([type=hidden]),select,textarea');
      if (!control || control.disabled) return;
      const row = document.createElement('div'), term = document.createElement('dt'), value = document.createElement('dd');
      term.textContent = field.dataset.supplierLabel;
      value.textContent = fieldValue(control);
      row.append(term, value); summary.append(row);
    });
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close());
    confirmSheet.showModal();
  };
  form.addEventListener('input', () => { dirty = true; resetConfirmation(); });
  form.addEventListener('change', () => { dirty = true; resetConfirmation(); });
  form.addEventListener('invalid', event => { resetConfirmation(); openAncestors(event.target); }, { capture: true });
  form.addEventListener('submit', event => {
    if (form.dataset.submitting === 'true') { event.preventDefault(); event.stopImmediatePropagation(); return; }
    if (!confirmed || payload() !== reviewPayload) {
      event.preventDefault(); event.stopImmediatePropagation(); openConfirmation(); return;
    }
    resetConfirmation();
  }, { capture: true });
  form.addEventListener('submit', event => {
    if (event.defaultPrevented || form.dataset.submitting !== 'true') return;
    dirty = false; if (review) review.disabled = true; confirm.disabled = true;
  });
  confirm.addEventListener('click', () => {
    if (!confirmSheet.open || form.dataset.submitting === 'true' || !form.reportValidity()) return;
    if (!reviewPayload || payload() !== reviewPayload) { openConfirmation(); return; }
    confirmSheet.close(); confirmed = true; form.requestSubmit(review);
  });
  confirmSheet.addEventListener('close', () => { if (form.dataset.submitting !== 'true') resetConfirmation(); });
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || !unsavedSheet || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href;
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsavedSheet.showModal();
  }));
  document.getElementById('confirmSupplierLeave')?.addEventListener('click', () => {
    if (!pendingURL) return;
    dirty = false; resetConfirmation(); location.assign(pendingURL);
  });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { resetConfirmation(); if (review) review.disabled = false; confirm.disabled = false; });
  const firstError = form.querySelector('[data-supplier-field]:has(.mi-error)');
  if (firstError) {
    openAncestors(firstError);
    requestAnimationFrame(() => { firstError.scrollIntoView({ block: 'center' }); firstError.querySelector('input:not([type=hidden]),textarea,select')?.focus({ preventScroll: true }); });
  } else if (form.querySelector('#supplierFormErrors')) {
    requestAnimationFrame(() => form.querySelector('#supplierFormErrors').focus({ preventScroll: true }));
  }
})();
