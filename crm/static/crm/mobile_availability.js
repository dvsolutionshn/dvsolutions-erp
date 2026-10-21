/* Presentation only. Availability validation and conflicts belong to the original form. */
(() => {
  'use strict';
  document.body.classList.add('mav-screen');
  const caption = document.querySelector('.mi-brand small');
  if (caption) caption.textContent = 'Empresa actual';
  const form = document.getElementById('mobileAvailabilityForm');
  if (!form) return;
  const confirmSheet = document.getElementById('mobileAvailabilityConfirmSheet');
  const unsavedSheet = document.getElementById('mobileAvailabilityUnsavedSheet');
  const values = document.getElementById('mobileAvailabilityConfirmValues');
  const status = document.getElementById('mobileAvailabilityConfirmStatus');
  const confirmation = document.getElementById('mobileAvailabilityConfirmation');
  const confirmButton = document.getElementById('confirmMobileAvailability');
  const reviewButton = document.getElementById('mobileAvailabilityReview');
  const leaveButton = document.getElementById('leaveMobileAvailability');
  const conflictFlag = document.getElementById('mobileAvailabilityConflictFlag');
  const changedNotice = document.getElementById('mobileAvailabilityChangedNotice');
  const fullDay = form.querySelector('[name="dia_completo"]');
  const fullDayNote = document.getElementById('mobileAvailabilityFullDayNote');
  const timeNames = ['hora_inicio', 'hora_inicio_periodo', 'hora_fin', 'hora_fin_periodo'];
  const timeControls = timeNames.map(name => form.querySelector(`[name="${name}"]`));
  const expectedName = form.dataset.availabilityConfirmationName || '';
  const serverHasConflicts = form.dataset.availabilityConflicts === 'true';
  let dirty = form.dataset.availabilityBound === 'true';
  let leaving = false;
  let submitting = false;
  let authorized = false;
  let pendingLeave = null;
  let reviewed = '';
  let reviewedConfirmation = false;

  function openAncestors(control) {
    let ancestor = control?.parentElement;
    while (ancestor && ancestor !== form) {
      if (ancestor.tagName === 'DETAILS') ancestor.open = true;
      ancestor = ancestor.parentElement;
    }
  }
  function toggleFullDay() {
    const disabled = Boolean(fullDay?.checked);
    timeControls.forEach(control => { if (control) control.disabled = disabled; });
    if (fullDayNote) fullDayNote.hidden = !disabled;
  }
  function snapshot() {
    return JSON.stringify([...new FormData(form).entries()].filter(([name]) => name !== 'confirmar_conflictos'));
  }
  toggleFullDay();
  if (conflictFlag) conflictFlag.disabled = true;
  const initialConflictSnapshot = snapshot();
  function canConfirmOriginalConflicts() { return serverHasConflicts && snapshot() === initialConflictSnapshot; }
  function revokeConfirmation() { authorized = false; if (conflictFlag) conflictFlag.disabled = true; }
  function updateChangedNotice() { changedNotice.hidden = !serverHasConflicts || canConfirmOriginalConflicts(); }
  function updateConfirmation() { confirmButton.disabled = !expectedName || confirmation.value.trim() !== expectedName.trim() || submitting; }
  function addValue(label, text) {
    const row = document.createElement('div'), term = document.createElement('dt'), description = document.createElement('dd');
    term.textContent = label;
    description.textContent = text;
    row.append(term, description);
    values.append(row);
  }
  function displayField(wrapper, data) {
    const name = wrapper.dataset.fieldName;
    const control = [...wrapper.querySelectorAll('input:not([type=hidden]),select,textarea')].find(item => item.name === name);
    if (!control) return 'Sin valor';
    if (control.disabled && timeNames.includes(name)) return 'No se envía: días completos';
    if (control.type === 'checkbox') return data.has(name) ? 'Sí' : 'No';
    if (control.tagName === 'SELECT') return [...control.selectedOptions].map(option => option.textContent.trim()).join('\n') || 'Sin selección';
    return String(data.get(name) ?? '') || 'Sin valor';
  }
  function openReview() {
    revokeConfirmation();
    if (!form.reportValidity()) return;
    if (unsavedSheet.open) unsavedSheet.close();
    const data = new FormData(form);
    values.replaceChildren();
    addValue('Agenda donde se registra el bloqueo', expectedName);
    addValue('Empresa desde la que está operando', form.dataset.availabilitySourceName || '');
    form.querySelectorAll('[data-availability-field]').forEach(wrapper => addValue(wrapper.dataset.fieldLabel || wrapper.dataset.fieldName, displayField(wrapper, data)));
    reviewed = snapshot();
    reviewedConfirmation = canConfirmOriginalConflicts();
    status.replaceChildren();
    const title = document.createElement('strong'), copy = document.createElement('p');
    title.textContent = reviewedConfirmation ? 'Confirmar las citas afectadas ya revisadas' : 'Comprobar el horario antes de guardar';
    copy.textContent = reviewedConfirmation
      ? 'Se confirmará este bloqueo con los mismos datos de la revisión. Ninguna de las citas mostradas se cancelará ni modificará.'
      : 'El sistema volverá a validar estos datos. Si el horario afecta citas, las mostrará para una nueva confirmación. Las citas no se cancelan ni modifican.';
    status.append(title, copy);
    confirmButton.textContent = reviewedConfirmation ? 'Confirmar bloqueo sin modificar citas' : 'Enviar para comprobar el horario';
    confirmation.value = '';
    updateConfirmation();
    if (!confirmSheet.open) confirmSheet.showModal();
    confirmation.focus();
  }
  function changed() {
    dirty = true;
    revokeConfirmation();
    toggleFullDay();
    updateChangedNotice();
  }
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);
  form.addEventListener('invalid', event => openAncestors(event.target), true);
  form.querySelectorAll('[data-availability-field-errors]').forEach(error => openAncestors(error));
  const firstError = form.querySelector('.has-error input:not([type=hidden]),.has-error select,.has-error textarea');
  if (firstError) requestAnimationFrame(() => firstError.focus({preventScroll: false}));
  form.addEventListener('submit', event => {
    if (submitting || form.dataset.submitting === 'true') { event.preventDefault(); event.stopImmediatePropagation(); return; }
    const fresh = authorized && confirmSheet.open && expectedName && confirmation.value.trim() === expectedName.trim() && reviewed === snapshot();
    if (!fresh) { event.preventDefault(); event.stopImmediatePropagation(); openReview(); return; }
    if (conflictFlag) conflictFlag.disabled = !(reviewedConfirmation && canConfirmOriginalConflicts());
    authorized = false;
  }, true);
  form.addEventListener('submit', event => {
    if (!event.defaultPrevented) { dirty = false; submitting = true; confirmButton.disabled = true; }
  });
  confirmation.addEventListener('input', updateConfirmation);
  confirmation.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); if (!confirmButton.disabled) confirmButton.click(); }
  });
  confirmButton.addEventListener('click', () => {
    if (submitting || !confirmSheet.open) return;
    if (reviewed !== snapshot()) { openReview(); return; }
    if (confirmation.value.trim() !== expectedName.trim()) { updateConfirmation(); confirmation.focus(); return; }
    if (!form.reportValidity()) return;
    authorized = true;
    form.requestSubmit(reviewButton);
  });
  confirmSheet.addEventListener('close', () => {
    if (!submitting) { revokeConfirmation(); confirmation.value = ''; updateConfirmation(); }
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || !dirty || leaving || submitting || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target === '_blank' || link.hasAttribute('download')) return;
    event.preventDefault();
    pendingLeave = new URL(link.href, location.href).href;
    if (confirmSheet.open) confirmSheet.close();
    if (!unsavedSheet.open) unsavedSheet.showModal();
  });
  leaveButton.addEventListener('click', () => { if (!pendingLeave) return; leaving = true; location.assign(pendingLeave); });
  unsavedSheet.addEventListener('close', () => { if (!leaving) pendingLeave = null; });
  window.addEventListener('beforeunload', event => { if (dirty && !leaving && !submitting) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => {
    submitting = false; leaving = false; pendingLeave = null;
    revokeConfirmation();
    if (confirmSheet.open) confirmSheet.close();
    if (unsavedSheet.open) unsavedSheet.close();
    updateConfirmation(); updateChangedNotice();
  });
  updateChangedNotice();
})();
