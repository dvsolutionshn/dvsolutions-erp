/* Presentation only. Original prefixed forms own all clinical choices, validation and saving. */
(() => {
  'use strict';
  document.body.classList.add('ms-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const nav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (nav) { nav.classList.add('active'); nav.setAttribute('aria-current', 'page'); }
  const form = document.getElementById('mobileSpecialHistoryForm');
  if (!form) return;
  form.addEventListener('invalid', event => {
    let ancestor = event.target.parentElement;
    while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; }
  }, { capture: true });
  const sections = [...form.querySelectorAll('[data-special-history-section]')];
  const refreshSummary = section => {
    const summary = section.querySelector('[data-special-history-section-state]');
    if (!summary || ['registro', 'estado'].includes(section.dataset.specialHistorySection)) return;
    const written = [...section.querySelectorAll('textarea,input:not([type=hidden]):not([type=radio]):not([type=checkbox])')].filter(input => input.value.trim()).length;
    if (section.dataset.specialHistorySection.startsWith('funcion_')) {
      const select = section.querySelector('select');
      summary.textContent = [select?.selectedOptions[0]?.textContent.trim() || '', written ? 'Con detalle escrito' : ''].filter(Boolean).join(' · ');
    } else summary.textContent = written ? `${written} ${written === 1 ? 'campo con texto' : 'campos con texto'}` : '';
  };
  sections.forEach(section => { refreshSummary(section); section.addEventListener('input', () => refreshSummary(section)); section.addEventListener('change', () => refreshSummary(section)); });
  form.querySelector('[data-special-history-expand]')?.addEventListener('click', () => sections.forEach(section => { section.open = true; }));
  form.querySelector('[data-special-history-collapse]')?.addEventListener('click', () => sections.forEach(section => { if (!['registro', 'estado'].includes(section.dataset.specialHistorySection)) section.open = false; }));
  let dirty = form.dataset.specialHistoryUnsaved === '1';
  form.addEventListener('input', event => { if (!event.target.disabled) dirty = true; });
  form.addEventListener('change', event => { if (!event.target.disabled) dirty = true; });
  form.addEventListener('submit', event => { if (!event.defaultPrevented) dirty = false; });
  const leaveSheet = document.getElementById('specialHistoryUnsavedSheet');
  let pendingURL;
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || event.ctrlKey || event.metaKey || event.shiftKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href;
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); leaveSheet.showModal();
  }));
  document.getElementById('confirmSpecialHistoryLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
})();
