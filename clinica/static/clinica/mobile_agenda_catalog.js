/* Presentation only. The three original catalog handlers validate and save. */
(() => {
  'use strict';
  document.body.classList.add('magc-screen');
  const root = document.getElementById('mobileAgendaCatalog');
  if (!root) return;
  const companyName = root.dataset.catalogCompany || '';
  const states = [...document.querySelectorAll('[data-catalog-editor]')].map(form => ({
    form, sheet: form.closest('dialog'), dirty: form.dataset.catalogBound === 'true',
    submitting: false, authorized: false, reviewed: '', recordId: form.dataset.catalogRecordId || '',
  }));
  const create = states.find(state => state.form.dataset.catalogEditor === 'crear');
  const edit = states.find(state => state.form.dataset.catalogEditor === 'editar');
  const deleteSheet = document.getElementById('agendaCatalogDeleteSheet');
  const deleteForm = document.getElementById('mobileAgendaCatalogDeleteForm');
  const deleteWord = document.getElementById('agendaCatalogDeleteWord');
  const deleteButton = document.getElementById('confirmAgendaCatalogDelete');
  let pendingDiscard = null, leaving = false, deleteTarget = null, deleteSubmitting = false;
  const dirtyStates = () => states.filter(state => state.dirty);
  const busy = () => deleteSubmitting || states.some(state => state.submitting);
  function closeSheets() { document.querySelectorAll('.magc-sheet[open]').forEach(sheet => sheet.close()); }
  function showSheet(sheet) { if (!sheet) return; closeSheets(); sheet.showModal(); }
  function viewEditor(state) {
    state.form.hidden = false;
    state.sheet.querySelector('[data-catalog-review-panel]').hidden = true;
    state.sheet.querySelector('[data-catalog-leave-panel]').hidden = true;
    state.authorized = false;
  }
  function revealEditor(state) { viewEditor(state); if (!state.sheet.open) showSheet(state.sheet); }
  function payload(state) { return JSON.stringify({action: state.form.getAttribute('action'), entries: [...new FormData(state.form).entries()]}); }
  function updateTyped(state) {
    const word = state.sheet.querySelector('[data-catalog-confirm-name]');
    state.sheet.querySelector('[data-catalog-confirm-save]').disabled = !companyName || word.value.trim() !== companyName.trim() || state.submitting;
  }
  function addSummary(container, label, text) {
    const row = document.createElement('div'), term = document.createElement('dt'), value = document.createElement('dd');
    term.textContent = label; value.textContent = text; row.append(term, value); container.append(row);
  }
  function review(state) {
    state.authorized = false;
    if (busy() || !state.form.hasAttribute('action') || !state.form.reportValidity()) return;
    const data = new FormData(state.form), summary = state.sheet.querySelector('[data-catalog-review-values]');
    summary.replaceChildren();
    addSummary(summary, 'Empresa del catálogo local', companyName);
    addSummary(summary, 'Nombre del tratamiento', String(data.get('nombre') ?? ''));
    if (state === edit) addSummary(summary, 'Activo', data.has('activo') ? 'Sí' : 'No');
    if (states.some(item => item !== state && item.dirty)) addSummary(summary, 'Otro formulario sin guardar', 'Guardar esta opción dejará la pantalla y descartará el borrador del otro formulario. Puede volver sin guardar para conservarlo.');
    state.reviewed = payload(state);
    state.form.hidden = true;
    state.sheet.querySelector('[data-catalog-leave-panel]').hidden = true;
    state.sheet.querySelector('[data-catalog-review-panel]').hidden = false;
    state.sheet.querySelector('[data-catalog-confirm-name]').value = '';
    updateTyped(state);
    if (!state.sheet.open) showSheet(state.sheet);
    state.sheet.querySelector('[data-catalog-confirm-name]').focus();
  }
  function requestDiscard(action, affected = dirtyStates(), preferredSheet = null, message = '') {
    if (!affected.length) { action(); return; }
    const sheet = preferredSheet || affected[0].sheet;
    pendingDiscard = {action, affected, sheet};
    if (!sheet.open) showSheet(sheet);
    states.forEach(state => { if (state.sheet === sheet) { state.authorized = false; state.form.hidden = true; state.sheet.querySelector('[data-catalog-review-panel]').hidden = true; } });
    if (sheet === deleteSheet) deleteForm.hidden = true;
    const panel = sheet.querySelector('[data-catalog-leave-panel]'), copy = panel.querySelector('p');
    if (!panel.dataset.initialCopy) panel.dataset.initialCopy = copy.textContent;
    copy.textContent = message || panel.dataset.initialCopy;
    panel.hidden = false;
  }
  states.forEach(state => {
    const confirmation = state.sheet.querySelector('[data-catalog-confirm-name]');
    const confirm = state.sheet.querySelector('[data-catalog-confirm-save]');
    state.form.addEventListener('input', () => { state.dirty = true; state.authorized = false; });
    state.form.addEventListener('change', () => { state.dirty = true; state.authorized = false; });
    state.form.addEventListener('invalid', () => revealEditor(state), true);
    state.form.addEventListener('submit', event => {
      if (busy() || state.form.dataset.submitting === 'true') { event.preventDefault(); event.stopImmediatePropagation(); return; }
      const fresh = state.authorized && state.sheet.open && !state.sheet.querySelector('[data-catalog-review-panel]').hidden
        && state.sheet.querySelector('[data-catalog-leave-panel]').hidden
        && companyName && confirmation.value.trim() === companyName.trim() && state.reviewed === payload(state);
      if (!fresh) { event.preventDefault(); event.stopImmediatePropagation(); review(state); return; }
      const otherDrafts = dirtyStates().filter(item => item !== state);
      if (otherDrafts.length) {
        event.preventDefault(); event.stopImmediatePropagation(); state.authorized = false;
        requestDiscard(() => review(state), otherDrafts, state.sheet,
          'Guardar esta opción dejará la pantalla y descartará el borrador del otro formulario. La opción actual se conserva para una nueva revisión. Puede cancelar para conservar ambos borradores.');
        return;
      }
      state.authorized = false;
    }, true);
    state.form.addEventListener('submit', event => {
      if (!event.defaultPrevented) { state.submitting = true; states.forEach(item => { item.dirty = false; }); confirm.disabled = true; }
    });
    confirmation.addEventListener('input', () => updateTyped(state));
    confirmation.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); if (!confirm.disabled) confirm.click(); } });
    confirm.addEventListener('click', () => {
      if (busy() || !state.sheet.open || state.sheet.querySelector('[data-catalog-review-panel]').hidden) return;
      if (state.reviewed !== payload(state)) { review(state); return; }
      if (confirmation.value.trim() !== companyName.trim()) { updateTyped(state); return; }
      if (!state.form.reportValidity()) return;
      state.authorized = true; state.form.requestSubmit(state.form.querySelector('[data-catalog-review]'));
    });
    state.sheet.querySelector('[data-catalog-back-editor]').addEventListener('click', () => viewEditor(state));
    state.sheet.addEventListener('close', () => {
      if (state.submitting) return;
      state.authorized = false; confirmation.value = ''; updateTyped(state);
      if (pendingDiscard?.sheet === state.sheet) pendingDiscard = null;
      viewEditor(state);
    });
  });
  document.getElementById('openAgendaCatalogCreate')?.addEventListener('click', () => { if (create && !busy()) revealEditor(create); });
  document.querySelectorAll('[data-catalog-edit-url]').forEach(button => button.addEventListener('click', () => {
    if (!edit || busy()) return;
    const populate = () => {
      edit.form.setAttribute('action', button.dataset.catalogEditUrl);
      edit.recordId = button.dataset.catalogId;
      edit.form.dataset.catalogRecordId = edit.recordId;
      edit.form.elements.namedItem('nombre').value = button.dataset.catalogName;
      edit.form.elements.namedItem('activo').checked = button.dataset.catalogActive === 'true';
      document.getElementById('agendaCatalogOriginalName').textContent = button.dataset.catalogName;
      edit.form.querySelector('.magc-errors')?.remove();
      edit.dirty = false; revealEditor(edit);
    };
    if (edit.recordId === button.dataset.catalogId && edit.form.hasAttribute('action')) revealEditor(edit);
    else requestDiscard(populate, edit.dirty ? [edit] : []);
  }));
  function updateDelete() {
    if (!deleteWord) return;
    const valid = deleteWord.value === 'ELIMINAR';
    deleteWord.setCustomValidity(valid ? '' : 'Escriba ELIMINAR exactamente para confirmar.');
    deleteButton.disabled = !valid || deleteSubmitting;
  }
  document.querySelectorAll('[data-catalog-delete-url]').forEach(button => button.addEventListener('click', () => {
    if (!deleteSheet || busy()) return;
    leaving = false;
    showSheet(deleteSheet);
    deleteTarget = {url: button.dataset.catalogDeleteUrl, id: button.dataset.catalogId};
    deleteForm.setAttribute('action', deleteTarget.url); deleteForm.hidden = false;
    deleteSheet.querySelector('[data-catalog-leave-panel]').hidden = true;
    document.getElementById('agendaCatalogDeleteName').textContent = button.dataset.catalogName;
    deleteWord.value = ''; updateDelete(); deleteWord.focus();
  }));
  deleteWord?.addEventListener('input', updateDelete);
  deleteForm?.addEventListener('submit', event => {
    if (busy() || !deleteSheet.open || !deleteTarget || deleteForm.getAttribute('action') !== deleteTarget.url || deleteWord.value !== 'ELIMINAR') {
      event.preventDefault(); event.stopImmediatePropagation(); updateDelete(); return;
    }
    if (dirtyStates().length) {
      event.preventDefault(); event.stopImmediatePropagation();
      requestDiscard(() => { deleteForm.hidden = false; deleteSheet.querySelector('[data-catalog-leave-panel]').hidden = true; deleteForm.requestSubmit(deleteButton); }, dirtyStates(), deleteSheet);
    }
  }, true);
  deleteForm?.addEventListener('submit', event => { if (!event.defaultPrevented) deleteSubmitting = true; });
  deleteSheet?.addEventListener('close', () => {
    if (deleteSubmitting) return;
    leaving = false; deleteTarget = null; deleteWord.value = ''; updateDelete(); deleteForm.removeAttribute('action'); deleteForm.hidden = false;
    deleteSheet.querySelector('[data-catalog-leave-panel]').hidden = true;
    if (pendingDiscard?.sheet === deleteSheet) pendingDiscard = null;
  });
  document.querySelectorAll('[data-catalog-keep]').forEach(button => button.addEventListener('click', () => {
    const sheet = button.closest('dialog'); pendingDiscard = null; sheet.querySelector('[data-catalog-leave-panel]').hidden = true;
    const state = states.find(item => item.sheet === sheet);
    if (state) viewEditor(state); else if (sheet === deleteSheet) deleteForm.hidden = false;
  }));
  document.querySelectorAll('[data-catalog-leave]').forEach(button => button.addEventListener('click', () => {
    if (!pendingDiscard || busy()) return;
    const {action, affected, sheet} = pendingDiscard; pendingDiscard = null;
    affected.forEach(state => { state.dirty = false; state.authorized = false; });
    sheet.querySelector('[data-catalog-leave-panel]').hidden = true;
    action();
  }));
  document.getElementById('mobileAgendaCatalogSearchForm')?.addEventListener('submit', event => {
    if (!dirtyStates().length || busy()) return;
    event.preventDefault(); const url = new URL(event.currentTarget.action, location.href);
    url.search = new URLSearchParams([...new FormData(event.currentTarget).entries()]).toString();
    requestDiscard(() => { leaving = true; location.assign(url.href); });
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || !dirtyStates().length || busy() || leaving || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target === '_blank') return;
    event.preventDefault(); requestDiscard(() => { leaving = true; location.assign(link.href); });
  });
  window.addEventListener('beforeunload', event => { if (dirtyStates().length && !busy() && !leaving) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => {
    leaving = false; deleteSubmitting = false; pendingDiscard = null;
    states.forEach(state => { state.submitting = false; state.authorized = false; state.sheet.querySelector('[data-catalog-confirm-name]').value = ''; updateTyped(state); });
    if (deleteWord) { deleteWord.value = ''; updateDelete(); }
  });
  const bound = states.find(state => state.form.dataset.catalogBound === 'true');
  if (bound) requestAnimationFrame(() => { revealEditor(bound); (bound.form.querySelector('.magc-errors') || bound.form.elements.namedItem('nombre')).focus(); });
})();
