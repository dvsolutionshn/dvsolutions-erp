/* UI state only: the original clinical forms and validators own all medical choices and saves. */
(() => {
  'use strict';
  document.body.classList.add('mh-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const nav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (nav) { nav.classList.add('active'); nav.setAttribute('aria-current', 'page'); }
  const form = document.getElementById('mobileHistoryForm');
  if (!form) return;
  form.addEventListener('invalid', event => {
    let ancestor = event.target.parentElement;
    while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; }
  }, { capture: true });
  const sections = [...form.querySelectorAll('[data-history-section]')];
  const refreshSummary = section => {
    const summary = section.querySelector('[data-history-section-state]');
    if (!summary || ['registro', 'contenido', 'estado'].includes(section.dataset.historySection)) return;
    const marked = [...section.querySelectorAll('input[type=checkbox]:checked,input[type=radio]:checked')].filter(input => input.value).length;
    const hasText = [...section.querySelectorAll('textarea')].some(input => input.value.trim());
    summary.textContent = [marked ? `${marked} ${marked === 1 ? 'selección' : 'selecciones'}` : '', hasText ? 'Con detalle escrito' : ''].filter(Boolean).join(' · ');
  };
  sections.forEach(section => { refreshSummary(section); section.addEventListener('input', () => refreshSummary(section)); section.addEventListener('change', () => refreshSummary(section)); });
  form.querySelector('[data-history-expand]')?.addEventListener('click', () => sections.forEach(section => { section.open = true; }));
  form.querySelector('[data-history-collapse]')?.addEventListener('click', () => sections.forEach(section => { if (!['registro', 'contenido', 'estado'].includes(section.dataset.historySection)) section.open = false; }));
  const namedInputs = name => [...form.querySelectorAll('input')].filter(input => input.name === name);
  const other = form.querySelector('[data-history-pattern-other]');
  if (other) {
    const pattern = namedInputs(form.dataset.historyPatternName);
    const refresh = () => { other.hidden = !pattern.some(input => input.checked && input.value === 'otro'); };
    pattern.forEach(input => input.addEventListener('change', refresh)); refresh();
  }
  const gradeRoot = form.querySelector('[data-history-grades]');
  if (gradeRoot) {
    const hiddenScale = namedInputs(form.dataset.historyScaleName).find(input => input.type === 'hidden');
    const sex = namedInputs(form.dataset.historySexName);
    // The canonical blank choice is a placeholder, not a valid confirmation.
    // Keep its original option in the DOM but let native required validation
    // focus a real choice instead of treating the default blank radio as valid.
    sex.filter(input => !input.value).forEach(input => { input.checked = false; input.disabled = true; });
    const cards = [...gradeRoot.querySelectorAll('[data-history-grade-card]')];
    const originalDisabled = new Map(cards.map(card => { const radio = card.querySelector('input'); return [radio, radio.disabled]; }));
    const showScale = (scale, clearGrade) => {
      cards.forEach(card => {
        const radio = card.querySelector('input');
        const images = [...card.querySelectorAll('[data-history-grade-scale]')];
        images.forEach(image => { image.hidden = image.dataset.historyGradeScale !== scale; });
        const visible = images.some(image => !image.hidden);
        card.hidden = !visible; radio.disabled = originalDisabled.get(radio) || !visible;
        if (clearGrade) radio.checked = false;
      });
      gradeRoot.querySelector('[data-history-sex-pending]').hidden = Boolean(scale);
      gradeRoot.querySelector('[data-history-scale-label]').textContent = scale === 'hamilton_norwood' ? 'Escala Hamilton-Norwood' : scale === 'ludwig' ? 'Escala Ludwig' : '';
      sections.forEach(refreshSummary);
    };
    showScale(gradeRoot.dataset.initialScale || hiddenScale?.value || '', false);
    sex.forEach(input => input.addEventListener('change', () => {
      const scale = input.value === 'femenino' ? 'ludwig' : input.value === 'masculino' ? 'hamilton_norwood' : '';
      if (hiddenScale) hiddenScale.value = scale;
      showScale(scale, true);
    }));
  }
  const lockWarning = form.querySelector('[data-history-final-lock]');
  const state = form.querySelector('[name=estado]');
  if (lockWarning && state) { const update = () => { lockWarning.hidden = state.value !== 'finalizada'; }; state.addEventListener('change', update); update(); }
  let dirty = form.dataset.historyUnsaved === '1';
  form.addEventListener('input', event => { if (!event.target.disabled) dirty = true; });
  form.addEventListener('change', event => { if (!event.target.disabled) dirty = true; });
  form.addEventListener('submit', () => { dirty = false; });
  const leaveSheet = document.getElementById('historyUnsavedSheet');
  let pendingURL;
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || event.ctrlKey || event.metaKey || event.shiftKey || link.target === '_blank') return;
    event.preventDefault(); pendingURL = link.href;
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); leaveSheet.showModal();
  }));
  document.getElementById('confirmHistoryLeave')?.addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
})();
