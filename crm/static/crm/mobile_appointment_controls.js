/* Presentation only: original CRM views retain all permissions, validation and draft/finalization logic. */
(() => {
  'use strict';
  document.body.classList.add('mac-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const nav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Citas') || [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (nav) { nav.classList.add('active'); nav.setAttribute('aria-current', 'page'); }
  const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const cards = [...document.querySelectorAll('[data-appointment-card]')];
  document.getElementById('appointmentControlSearch')?.addEventListener('input', event => {
    const query = normalize(event.target.value);
    cards.forEach(card => { card.hidden = !normalize(card.textContent).includes(query); });
    document.getElementById('appointmentControlSearchEmpty').hidden = cards.some(card => !card.hidden);
  });
  const form = document.getElementById('mobileAppointmentControlForm');
  if (!form) return;
  const sections = [...form.querySelectorAll('[data-appointment-control-section]')];
  const refresh = section => {
    const label = section.querySelector('[data-appointment-control-section-state]');
    if (!label) return;
    const marked = section.querySelectorAll('input[type=checkbox]:checked,input[type=radio]:checked').length;
    const written = [...section.querySelectorAll('textarea,input[type=text]')].some(input => input.value.trim());
    label.textContent = [marked ? `${marked} ${marked === 1 ? 'selección' : 'selecciones'}` : '', written ? 'Con texto escrito' : ''].filter(Boolean).join(' · ');
  };
  sections.forEach(section => { refresh(section); section.addEventListener('input', () => refresh(section)); section.addEventListener('change', () => refresh(section)); });
  form.querySelector('[data-appointment-control-expand]')?.addEventListener('click', () => sections.forEach(section => { section.open = true; }));
  form.querySelector('[data-appointment-control-collapse]')?.addEventListener('click', () => sections.forEach(section => { section.open = false; }));
  if (form.dataset.appointmentControlReadonly === '1') return;
  form.querySelectorAll('[data-appointment-control-single-choice]').forEach(group => group.addEventListener('change', event => {
    if (!event.target.matches('input[type=checkbox]') || !event.target.checked) return;
    group.querySelectorAll('input[type=checkbox]').forEach(input => { if (input !== event.target) input.checked = false; });
    const section = group.closest('[data-appointment-control-section]'); if (section) refresh(section);
  }));
  const care = form.querySelector('[name=cuidados_realizados][value=otro]');
  const careDetail = form.querySelector('[data-appointment-control-field=cuidado_otro]');
  if (care && careDetail) { const update = () => { careDetail.hidden = !care.checked && !careDetail.querySelector('.mi-error'); }; care.addEventListener('change', update); update(); }
  let dirty = form.dataset.appointmentControlUnsaved === '1', confirmedFinalization = false;
  const action = form.querySelector('[name=accion]');
  const resetFinalize = () => { confirmedFinalization = false; action.value = 'borrador'; };
  form.addEventListener('input', event => { if (!event.target.disabled) { dirty = true; resetFinalize(); } });
  form.addEventListener('change', event => { if (!event.target.disabled) { dirty = true; resetFinalize(); } });
  const openAncestors = field => {
    let ancestor = field.parentElement;
    while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; }
  };
  form.addEventListener('invalid', event => { openAncestors(event.target); resetFinalize(); }, { capture: true });
  form.querySelector('[data-appointment-control-draft]')?.addEventListener('click', resetFinalize);
  form.addEventListener('submit', event => {
    if (action.value === 'finalizar' && !confirmedFinalization) { event.preventDefault(); event.stopImmediatePropagation(); resetFinalize(); return; }
    confirmedFinalization = false;
  }, { capture: true });
  form.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    dirty = false;
    form.querySelectorAll('[data-appointment-control-finalize-trigger]').forEach(button => { button.disabled = true; });
  });
  const finalizeSheet = document.getElementById('appointmentControlFinalizeSheet');
  document.getElementById('confirmAppointmentControlFinalize')?.addEventListener('click', () => {
    if (form.dataset.submitting === 'true' || !finalizeSheet.open) return;
    finalizeSheet.close();
    action.value = 'finalizar'; confirmedFinalization = true; form.requestSubmit();
  });
  finalizeSheet?.addEventListener('close', () => { if (!confirmedFinalization && form.dataset.submitting !== 'true') resetFinalize(); });
  let pendingURL;
  const unsavedSheet = document.getElementById('appointmentControlUnsavedSheet');
  const warnBeforeLeave = event => {
    event.preventDefault();
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsavedSheet.showModal();
  };
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || event.ctrlKey || event.metaKey || event.shiftKey || link.target === '_blank') return;
    pendingURL = link.href; warnBeforeLeave(event);
  }));
  const dateForm = document.querySelector('.mac-date-form');
  dateForm?.addEventListener('submit', event => {
    if (!dirty) return;
    const destination = new URL(dateForm.action);
    destination.search = new URLSearchParams(new FormData(dateForm)).toString();
    pendingURL = destination.href; warnBeforeLeave(event);
  });
  document.getElementById('confirmAppointmentControlLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { resetFinalize(); form.querySelectorAll('[data-appointment-control-finalize-trigger]').forEach(button => { button.disabled = false; }); });
  const firstError = form.querySelector('.mac-field:has(.mi-error)');
  if (firstError) {
    const field = firstError.querySelector('input:not([type=hidden]):not(:disabled),select:not(:disabled),textarea:not(:disabled)');
    openAncestors(firstError);
    requestAnimationFrame(() => { firstError.scrollIntoView({ block: 'center' }); field?.focus({ preventScroll: true }); });
  }
})();
