/* Presentation only: amounts, retentions, commissions and accounting use the original payment handlers. */
(() => {
  'use strict';
  document.body.classList.add('mpy-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const functionsNav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsNav) { functionsNav.classList.add('active'); functionsNav.setAttribute('aria-current', 'page'); }
  const form = document.getElementById('mobilePaymentForm'); if (!form) return;
  const review = document.getElementById('paymentReview'), confirm = document.getElementById('confirmPaymentSave');
  const confirmSheet = document.getElementById('paymentConfirmSheet'), unsavedSheet = document.getElementById('paymentUnsavedSheet');
  if (!confirmSheet || !confirm) return;
  let dirty = form.dataset.paymentBound === '1', confirmed = false, pendingURL, reviewedPayload;
  const resetConfirmation = () => { confirmed = false; };
  const branches = [...form.querySelectorAll('[data-payment-branch]')];
  const modeButtons = [...form.querySelectorAll('[data-payment-mode]')];
  const setMode = (mode, userChange = false) => {
    if (!['simple', 'mixto'].includes(mode)) return;
    branches.forEach(branch => { branch.disabled = branch.dataset.paymentBranch !== mode; branch.hidden = branch.disabled; });
    modeButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.paymentMode === mode)));
    form.dataset.paymentMode = mode;
    if (userChange) { dirty = true; resetConfirmation(); }
  };
  modeButtons.forEach(button => button.addEventListener('click', () => setMode(button.dataset.paymentMode, true)));
  setMode(form.dataset.paymentMode);
  // Preserve a rejected or historical selection without offering any additional catalogue.
  // Keeping its submitted ID prevents it silently becoming a different default account.
  form.querySelectorAll('select[data-payment-requested-value]').forEach(select => {
    const raw = select.dataset.paymentRequestedValue;
    const warning = select.closest('.mi-field')?.querySelector('[data-payment-unavailable]');
    if (raw && ![...select.options].some(option => option.value === raw)) {
      const option = document.createElement('option'); option.value = raw; option.textContent = `Selección no disponible: #${raw}`; option.dataset.paymentUnavailable = '1'; option.selected = true; select.append(option);
    }
    const updateWarning = () => {
      const unavailable = select.selectedOptions[0]?.dataset.paymentUnavailable === '1';
      if (warning) warning.hidden = !unavailable;
      select.setCustomValidity(unavailable ? 'Esta selección no está disponible. Elige una opción autorizada o ajusta el campo explícitamente.' : '');
    };
    select.addEventListener('change', updateWarning); updateWarning();
  });
  const openAncestors = target => { let ancestor = target.parentElement; while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; } };
  form.addEventListener('input', () => { dirty = true; resetConfirmation(); });
  form.addEventListener('change', () => { dirty = true; resetConfirmation(); });
  form.addEventListener('invalid', event => { openAncestors(event.target); resetConfirmation(); }, { capture: true });
  const payload = () => [...new FormData(form).entries()].map(([name, value]) => [name, String(value)]);
  const fingerprint = values => JSON.stringify(values);
  const openConfirmation = () => {
    if (form.dataset.submitting === 'true' || !form.reportValidity()) return;
    const values = payload(); reviewedPayload = fingerprint(values);
    const summary = document.getElementById('paymentConfirmFields'); summary.replaceChildren();
    values.filter(([name]) => !['app', 'csrfmiddlewaretoken'].includes(name)).forEach(([name, value]) => {
      const input = form.elements.namedItem(name);
      const label = input?.dataset.paymentLabel || input?.closest('.mi-field')?.querySelector('label')?.textContent.trim() || name;
      const group = document.createElement('div'), term = document.createElement('dt'), description = document.createElement('dd');
      term.textContent = label;
      if (input?.tagName === 'SELECT' && value) description.textContent = `${input.selectedOptions[0]?.textContent || value} · ID ${value}`;
      else if (input?.type === 'checkbox') description.textContent = `${input.checked ? 'Activado' : 'Desactivado'} (${value})`;
      else description.textContent = value || 'Sin valor (se envía vacío)';
      group.append(term, description); summary.append(group);
    });
    document.getElementById('paymentConfirmMode').textContent = form.dataset.paymentMode === 'mixto' ? 'Se enviará únicamente el modo mixto.' : 'Se enviará únicamente el modo simple.';
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); resetConfirmation(); confirmSheet.showModal();
  };
  review?.addEventListener('click', openConfirmation);
  form.addEventListener('submit', event => { if (!confirmed) { event.preventDefault(); event.stopImmediatePropagation(); openConfirmation(); return; } confirmed = false; }, { capture: true });
  form.addEventListener('submit', event => { if (!event.defaultPrevented) { dirty = false; if (review) review.disabled = true; confirm.disabled = true; } });
  confirm.addEventListener('click', () => {
    if (!confirmSheet.open || form.dataset.submitting === 'true' || !form.reportValidity()) return;
    if (reviewedPayload !== fingerprint(payload())) { openConfirmation(); return; }
    confirmSheet.close(); confirmed = true; form.requestSubmit();
  });
  confirmSheet.addEventListener('close', () => { if (form.dataset.submitting !== 'true') resetConfirmation(); });
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || !unsavedSheet || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href; document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsavedSheet.showModal();
  }));
  document.getElementById('confirmPaymentLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { resetConfirmation(); if (review) review.disabled = false; confirm.disabled = false; });
  const firstError = form.querySelector('.mi-field:has(.mi-error)');
  if (firstError) { openAncestors(firstError); requestAnimationFrame(() => { firstError.scrollIntoView({ block: 'center' }); firstError.querySelector('input:not([type=hidden]),textarea,select')?.focus({ preventScroll: true }); }); }
})();
