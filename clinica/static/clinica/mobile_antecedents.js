/* UI confirmation only; the original Form and audited update own all saved content. */
(() => {
  'use strict';
  document.body.classList.add('ma-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const nav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (nav) { nav.classList.add('active'); nav.setAttribute('aria-current', 'page'); }
  const form = document.getElementById('mobileAntecedentsForm');
  if (!form) return;
  const text = form.querySelector('[name="antecedente_adicional-contenido"]');
  const clearSheet = document.getElementById('antecedentsClearSheet');
  const confirmation = document.getElementById('antecedentsClearConfirmation');
  const clearButton = document.getElementById('confirmAntecedentsClear');
  let dirty = form.dataset.antecedentsUnsaved === '1', clearApproved = false;
  let hadText = form.dataset.antecedentsHasCurrentText === '1' || Boolean(text.value.trim());
  const showClear = () => {
    clearApproved = false; confirmation.value = ''; clearButton.disabled = true;
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); clearSheet.showModal();
  };
  document.getElementById('openAntecedentsClear')?.addEventListener('click', showClear);
  confirmation?.addEventListener('input', () => { clearButton.disabled = confirmation.value !== 'VACIAR'; });
  form.addEventListener('input', event => { if (!event.target.disabled) { dirty = true; clearApproved = false; if (event.target === text && text.value.trim()) hadText = true; } });
  form.addEventListener('change', event => { if (!event.target.disabled) { dirty = true; clearApproved = false; } });
  form.addEventListener('invalid', event => {
    let ancestor = event.target.parentElement;
    while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; }
    clearApproved = false;
  }, { capture: true });
  form.addEventListener('submit', event => {
    if (hadText && !text.value.trim() && !clearApproved) { event.preventDefault(); event.stopImmediatePropagation(); showClear(); return; }
    clearApproved = false;
  }, { capture: true });
  form.addEventListener('submit', event => { if (!event.defaultPrevented) dirty = false; });
  clearButton?.addEventListener('click', () => {
    if (confirmation.value !== 'VACIAR' || form.dataset.submitting === 'true') return;
    clearSheet.close(); text.value = ''; dirty = true;
    if (!form.reportValidity()) return;
    clearApproved = true; form.requestSubmit();
  });
  const unsavedSheet = document.getElementById('antecedentsUnsavedSheet');
  let pendingURL;
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || event.ctrlKey || event.metaKey || event.shiftKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href;
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsavedSheet.showModal();
  }));
  document.getElementById('confirmAntecedentsLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { clearApproved = false; });
})();
