/* UI only: canonical Forms/models retain all session rules, choices and saving. */
(() => {
  'use strict';
  document.body.classList.add('mc-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const nav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (nav) { nav.classList.add('active'); nav.setAttribute('aria-current', 'page'); }
  document.querySelector('[data-control-program-select]')?.addEventListener('change', event => { if (event.target.value) location.assign(event.target.value); });
  const form = document.getElementById('mobileControlForm');
  if (!form) return;
  const sections = [...form.querySelectorAll('[data-control-section]')];
  const refresh = section => {
    const label = section.querySelector('[data-control-section-state]');
    if (!label) return;
    const marked = section.querySelectorAll('input[type=checkbox]:checked,input[type=radio]:checked').length;
    const written = [...section.querySelectorAll('textarea,input[type=text]')].some(input => input.value.trim());
    label.textContent = [marked ? `${marked} ${marked === 1 ? 'selección' : 'selecciones'}` : '', written ? 'Con texto escrito' : ''].filter(Boolean).join(' · ');
  };
  sections.forEach(section => { refresh(section); section.addEventListener('input', () => refresh(section)); section.addEventListener('change', () => refresh(section)); });
  form.querySelector('[data-control-expand]')?.addEventListener('click', () => sections.forEach(section => { section.open = true; }));
  form.querySelector('[data-control-collapse]')?.addEventListener('click', () => sections.forEach(section => { section.open = false; }));
  if (form.dataset.controlReadonly === '1') return;
  form.querySelectorAll('[data-control-single-choice]').forEach(group => group.addEventListener('change', event => {
    if (!event.target.matches('input[type=checkbox]') || !event.target.checked) return;
    group.querySelectorAll('input[type=checkbox]').forEach(input => { if (input !== event.target) input.checked = false; });
    const section = group.closest('[data-control-section]'); if (section) refresh(section);
  }));
  const care = form.querySelector('[name=cuidados_realizados][value=otro]');
  const careDetail = form.querySelector('[data-control-field=cuidado_otro]');
  if (care && careDetail) { const update = () => { careDetail.hidden = !care.checked && !careDetail.querySelector('.mi-error'); }; care.addEventListener('change', update); update(); }
  let dirty = form.dataset.controlUnsaved === '1', confirmedFinalization = false;
  const action = form.querySelector('[name=accion]');
  const resetFinalize = () => { confirmedFinalization = false; action.value = 'borrador'; };
  form.addEventListener('input', event => { if (!event.target.disabled) dirty = true; });
  form.addEventListener('change', event => { if (!event.target.disabled) dirty = true; });
  form.addEventListener('invalid', event => {
    let ancestor = event.target.parentElement;
    while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; }
    resetFinalize();
  }, { capture: true });
  form.querySelector('[data-control-draft]')?.addEventListener('click', resetFinalize);
  form.addEventListener('submit', event => {
    if (action.value === 'finalizar' && !confirmedFinalization) { event.preventDefault(); event.stopImmediatePropagation(); resetFinalize(); return; }
    confirmedFinalization = false;
  }, { capture: true });
  form.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    dirty = false; form.querySelectorAll('[data-control-finalize-trigger]').forEach(button => { button.disabled = true; });
  });
  const finalizeSheet = document.getElementById('controlFinalizeSheet');
  document.getElementById('confirmControlFinalize')?.addEventListener('click', () => {
    finalizeSheet.close();
    if (form.dataset.submitting === 'true' || !form.reportValidity()) { resetFinalize(); return; }
    action.value = 'finalizar'; confirmedFinalization = true; form.requestSubmit();
  });
  finalizeSheet?.addEventListener('close', () => { if (!confirmedFinalization && form.dataset.submitting !== 'true') resetFinalize(); });
  let pendingURL;
  const unsavedSheet = document.getElementById('controlUnsavedSheet');
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || event.ctrlKey || event.metaKey || event.shiftKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href;
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsavedSheet.showModal();
  }));
  document.getElementById('confirmControlLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { resetFinalize(); form.querySelectorAll('[data-control-finalize-trigger]').forEach(button => { button.disabled = false; }); });
})();
