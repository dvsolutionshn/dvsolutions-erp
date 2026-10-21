/* Presentation only: the original EmpresaControlForm owns all settings and validation. */
(() => {
  'use strict';
  document.body.classList.add('mco-screen');
  const brandCaption = document.querySelector('.mi-brand small');
  if (brandCaption) brandCaption.textContent = 'Superadministración';
  const form = document.getElementById('mobileCompanyForm');
  if (!form) return;
  const confirmSheet = document.getElementById('mobileCompanyConfirmSheet');
  const unsavedSheet = document.getElementById('mobileCompanyUnsavedSheet');
  const values = document.getElementById('mobileCompanyConfirmValues');
  const confirmation = document.getElementById('mobileCompanyConfirmation');
  const confirmButton = document.getElementById('confirmMobileCompany');
  const saveButton = document.getElementById('mobileCompanyReview');
  const leaveButton = document.getElementById('leaveMobileCompany');
  const expectedName = form.dataset.companyConfirmationName || '';
  let dirty = form.dataset.companyBound === 'true';
  let leaving = false;
  let authorized = false;
  let submitting = false;
  let pendingLeave = null;
  let reviewed = '';

  function openAncestors(control) {
    let ancestor = control?.parentElement;
    while (ancestor && ancestor !== form) {
      if (ancestor.tagName === 'DETAILS') ancestor.open = true;
      ancestor = ancestor.parentElement;
    }
  }
  function snapshot() {
    return JSON.stringify([...new FormData(form).entries()].map(([name, value]) => [name,
      value instanceof File ? {name: value.name, size: value.size, type: value.type, lastModified: value.name ? value.lastModified : null} : value,
    ]));
  }
  function controlLabel(control) {
    return [...(control.labels || [])].map(label => label.textContent.trim()).join(' · ');
  }
  function displayField(wrapper, data) {
    const name = wrapper.dataset.fieldName;
    const controls = [...wrapper.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(control => control.name === name && !control.disabled);
    const control = controls[0];
    if (!control) return 'Sin valor';
    if (control.type === 'file') {
      const file = control.files[0];
      const clear = data.has(`${name}-clear`);
      if (file) return `Archivo nuevo: ${file.name}\nTamaño: ${file.size} bytes\nTipo: ${file.type || 'No indicado'}${clear ? '\nRetirar logo actual: Sí (se aplicará la validación original)' : ''}`;
      return clear ? 'Retirar logo actual: Sí' : 'Sin archivo nuevo · El logo actual permanece sin cambios';
    }
    if (control.type === 'radio') {
      const selected = controls.find(item => item.checked);
      return selected ? controlLabel(selected) || selected.value : 'Sin selección';
    }
    if (control.type === 'checkbox') {
      if (controls.length > 1 || wrapper.classList.contains('mco-choices')) {
        return controls.filter(item => item.checked).map(item => controlLabel(item) || item.value).join('\n') || 'Sin selección';
      }
      return data.has(name) ? 'Sí' : 'No';
    }
    if (control.tagName === 'SELECT') return [...control.selectedOptions].map(option => option.textContent.trim()).join('\n') || 'Sin selección';
    return String(data.get(name) ?? '') || 'Sin valor';
  }
  function addValue(label, text) {
    const row = document.createElement('div');
    const term = document.createElement('dt');
    const description = document.createElement('dd');
    term.textContent = label;
    description.textContent = text;
    row.append(term, description);
    values.append(row);
  }
  function updateConfirmation() {
    confirmButton.disabled = !expectedName || confirmation.value.trim() !== expectedName.trim() || submitting;
  }
  function openReview() {
    if (!form.reportValidity()) return;
    if (unsavedSheet.open) unsavedSheet.close();
    const data = new FormData(form);
    values.replaceChildren();
    addValue('Empresa que se actualizará', `${expectedName}\nEnlace actual: ${form.dataset.companyOriginalSlug || ''}`);
    form.querySelectorAll('[data-company-field]').forEach(wrapper => addValue(wrapper.dataset.fieldLabel || wrapper.dataset.fieldName, displayField(wrapper, data)));
    reviewed = snapshot();
    authorized = false;
    confirmation.value = '';
    updateConfirmation();
    if (!confirmSheet.open) confirmSheet.showModal();
    confirmation.focus();
  }
  function changed() { dirty = true; authorized = false; }
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);
  form.addEventListener('invalid', event => openAncestors(event.target), true);
  form.querySelectorAll('[data-company-field-errors]').forEach(error => openAncestors(error));
  const firstError = form.querySelector('.has-error input:not([type=hidden]),.has-error select,.has-error textarea');
  if (firstError) requestAnimationFrame(() => firstError.focus({preventScroll: false}));

  form.addEventListener('submit', event => {
    if (submitting || form.dataset.submitting === 'true') {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    const fresh = authorized && confirmSheet.open && expectedName &&
      confirmation.value.trim() === expectedName.trim() && reviewed === snapshot();
    if (!fresh) {
      event.preventDefault();
      event.stopImmediatePropagation();
      openReview();
      return;
    }
    authorized = false;
  }, true);
  form.addEventListener('submit', event => {
    if (!event.defaultPrevented) {
      dirty = false;
      submitting = true;
      confirmButton.disabled = true;
    }
  });
  confirmation.addEventListener('input', updateConfirmation);
  confirmation.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (!confirmButton.disabled) confirmButton.click();
    }
  });
  confirmButton.addEventListener('click', () => {
    if (submitting || !confirmSheet.open) return;
    if (reviewed !== snapshot()) { openReview(); return; }
    if (confirmation.value.trim() !== expectedName.trim()) { updateConfirmation(); confirmation.focus(); return; }
    if (!form.reportValidity()) return;
    authorized = true;
    form.requestSubmit(saveButton);
  });
  confirmSheet.addEventListener('close', () => {
    authorized = false;
    if (!submitting) { confirmation.value = ''; updateConfirmation(); }
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || !dirty || leaving || submitting || event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target === '_blank' || link.hasAttribute('download')) return;
    const destination = new URL(link.href, location.href);
    if (destination.href === location.href) return;
    event.preventDefault();
    pendingLeave = destination.href;
    if (confirmSheet.open) confirmSheet.close();
    if (!unsavedSheet.open) unsavedSheet.showModal();
  });
  leaveButton.addEventListener('click', () => {
    if (!pendingLeave) return;
    leaving = true;
    location.assign(pendingLeave);
  });
  unsavedSheet.addEventListener('close', () => { if (!leaving) pendingLeave = null; });
  window.addEventListener('beforeunload', event => {
    if (dirty && !leaving && !submitting) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('pageshow', () => {
    submitting = false;
    leaving = false;
    authorized = false;
    pendingLeave = null;
    if (confirmSheet.open) confirmSheet.close();
    if (unsavedSheet.open) unsavedSheet.close();
    updateConfirmation();
  });
})();
