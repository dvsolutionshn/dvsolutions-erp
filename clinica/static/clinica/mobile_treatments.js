/* Presentation only. Original TratamientoPacienteForm remains the only clinical validator. */
(() => {
  'use strict';
  document.body.classList.add('mtr-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const nav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (nav) { nav.classList.add('active'); nav.setAttribute('aria-current', 'page'); }
  const form = document.getElementById('mobileTreatmentForm'); if (!form) return;
  const sections = [...form.querySelectorAll('[data-treatment-section]')];
  const refresh = section => {
    const state = section.querySelector('[data-treatment-section-state]');
    const written = [...section.querySelectorAll('input:not([type=hidden]),textarea')].some(input => input.value.trim());
    const chosen = [...section.querySelectorAll('select')].filter(select => select.value).length;
    state.textContent = [chosen ? `${chosen} ${chosen === 1 ? 'opción elegida' : 'opciones elegidas'}` : '', written ? 'Con datos escritos' : ''].filter(Boolean).join(' · ');
  };
  sections.forEach(section => { refresh(section); section.addEventListener('input', () => refresh(section)); section.addEventListener('change', () => refresh(section)); });
  form.querySelector('[data-treatment-expand]')?.addEventListener('click', () => sections.forEach(section => { section.open = true; }));
  form.querySelector('[data-treatment-collapse]')?.addEventListener('click', () => sections.forEach(section => { section.open = false; }));
  let dirty = form.dataset.treatmentUnsaved === '1', confirmed = false, activeSelect;
  const resetConfirmation = () => { confirmed = false; };
  const openAncestors = field => { let parent = field.parentElement; while (parent && parent !== form) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; } };
  form.addEventListener('input', event => { if (!event.target.disabled) { dirty = true; resetConfirmation(); } });
  form.addEventListener('change', event => { if (!event.target.disabled) { dirty = true; resetConfirmation(); } });
  form.addEventListener('invalid', event => { openAncestors(event.target); resetConfirmation(); }, { capture: true });
  const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const optionsSheet = document.getElementById('treatmentOptionsSheet'), search = document.getElementById('treatmentOptionsSearch'), results = document.getElementById('treatmentOptionsResults'), hint = document.getElementById('treatmentOptionsHint');
  const renderOptions = () => {
    if (!activeSelect) return;
    const query = normalize(search.value), matches = [...activeSelect.options].filter(option => !option.disabled && normalize(option.textContent).includes(query));
    results.replaceChildren(); hint.hidden = matches.length <= 100;
    hint.textContent = `Hay ${matches.length} opciones. Escribe más para afinar la búsqueda; se muestran las primeras 100.`;
    matches.slice(0, 100).forEach(option => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = option.textContent; button.classList.toggle('selected', activeSelect.value === option.value);
      button.addEventListener('click', () => { activeSelect.value = option.value; activeSelect.dispatchEvent(new Event('change', { bubbles: true })); optionsSheet.close(); activeSelect.focus(); }); results.append(button);
    });
    if (!matches.length) { const empty = document.createElement('p'); empty.textContent = 'No hay opciones que coincidan con la búsqueda.'; results.append(empty); }
  };
  form.querySelectorAll('[data-treatment-choose]').forEach(button => {
    const select = document.getElementById(button.dataset.treatmentChoose); if (!select || select.tagName !== 'SELECT' || select.disabled) return;
    button.hidden = false;
    button.addEventListener('click', () => { activeSelect = select; search.value = ''; document.getElementById('treatmentOptionsTitle').textContent = select.closest('[data-treatment-field]').querySelector('label').textContent.replace(/\s*\*\s*$/, '').trim(); renderOptions(); document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); optionsSheet.showModal(); search.focus(); });
  });
  search.addEventListener('input', renderOptions);
  const confirmSheet = document.getElementById('treatmentConfirmSheet'), confirm = document.getElementById('confirmTreatment'), review = document.getElementById('treatmentReview');
  const field = name => form.querySelector(`[name="${name}"]`);
  const choiceLabel = name => field(name)?.selectedOptions?.[0]?.textContent || '';
  const openConfirmation = () => {
    if (!confirmSheet || form.dataset.submitting === 'true' || !form.reportValidity()) return;
    document.querySelector('[data-treatment-confirm-patient]').textContent = choiceLabel('paciente');
    document.querySelector('[data-treatment-confirm-name]').textContent = field('nombre').value;
    document.querySelector('[data-treatment-confirm-state]').textContent = choiceLabel('estado');
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); resetConfirmation(); confirmSheet.showModal();
  };
  review?.addEventListener('click', openConfirmation);
  form.addEventListener('submit', event => { if (!confirmed) { event.preventDefault(); event.stopImmediatePropagation(); openConfirmation(); return; } confirmed = false; }, { capture: true });
  form.addEventListener('submit', event => { if (!event.defaultPrevented) { dirty = false; if (review) review.disabled = true; if (confirm) confirm.disabled = true; } });
  confirm?.addEventListener('click', () => { if (!confirmSheet.open || form.dataset.submitting === 'true' || !form.reportValidity()) return; confirmSheet.close(); confirmed = true; form.requestSubmit(); });
  confirmSheet?.addEventListener('close', () => { if (form.dataset.submitting !== 'true') resetConfirmation(); });
  const unsaved = document.getElementById('treatmentUnsavedSheet'); let pendingURL;
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || !unsaved || event.ctrlKey || event.metaKey || event.shiftKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href; document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsaved.showModal();
  }));
  document.getElementById('confirmTreatmentLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { resetConfirmation(); if (review) review.disabled = false; if (confirm) confirm.disabled = false; });
  const firstError = form.querySelector('[data-treatment-field]:has(.mi-error)');
  if (firstError) { openAncestors(firstError); const target = firstError.querySelector('input:not([type=hidden]),select,textarea'); requestAnimationFrame(() => { firstError.scrollIntoView({ block: 'center' }); target?.focus({ preventScroll: true }); }); }
})();
