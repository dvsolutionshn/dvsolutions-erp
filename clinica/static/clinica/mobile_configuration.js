/* Clinical catalog presentation only: the existing Forms/views validate and save. */
(() => {
  'use strict';
  document.body.classList.add('mcfg-mobile-screen');
  const functionsNav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsNav) {
    document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
    functionsNav.classList.add('active'); functionsNav.setAttribute('aria-current', 'page');
  }
  const safeHex = value => /^#[0-9a-fA-F]{6}$/.test(value);
  document.querySelectorAll('[data-configuration-color]').forEach(swatch => {
    if (safeHex(swatch.dataset.configurationColor)) swatch.style.backgroundColor = swatch.dataset.configurationColor;
  });
  document.querySelectorAll('[data-configuration-state-filter]').forEach(button => button.addEventListener('click', () => {
    const state = button.dataset.configurationStateFilter;
    document.querySelectorAll('[data-configuration-state-filter]').forEach(item => {
      const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active));
    });
    const cards = [...document.querySelectorAll('[data-configuration-state]')];
    cards.forEach(card => { card.hidden = state !== 'todos' && card.dataset.configurationState !== state; });
    const empty = document.getElementById('configurationFilterEmpty');
    if (empty) empty.hidden = cards.some(card => !card.hidden);
  }));

  const form = document.getElementById('mobileConfigurationForm');
  const createSheet = form?.closest('dialog');
  const confirmSheet = document.getElementById('configurationConfirmSheet');
  const unsavedSheet = document.getElementById('configurationUnsavedSheet');
  const deleteSheet = document.getElementById('configurationDeleteSheet');
  const summary = document.getElementById('configurationConfirmValues');
  const review = document.getElementById('configurationReview');
  const confirm = document.getElementById('confirmConfigurationSave');
  let dirty = form?.dataset.configurationUnsaved === '1', confirmed = false, reviewedPayload, pendingAction, leaving = false;
  const closeSheets = () => document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close());
  const resetConfirmation = () => { confirmed = false; };
  const revealEditor = () => {
    if (createSheet && !createSheet.open) { closeSheets(); createSheet.showModal(); }
  };
  const openAncestors = target => {
    let ancestor = target.parentElement;
    while (ancestor && ancestor !== form) { if (ancestor.tagName === 'DETAILS') ancestor.open = true; ancestor = ancestor.parentElement; }
    revealEditor();
  };
  const askToLeave = action => {
    if (!dirty || !unsavedSheet) { action(); return; }
    pendingAction = action; closeSheets(); unsavedSheet.showModal();
  };
  if (form && confirmSheet && confirm && summary) {
    const payload = () => JSON.stringify([...new FormData(form).entries()]);
    const colorField = form.elements.namedItem('color_calendario');
    const colorPreview = document.getElementById('configurationFormColorPreview');
    const colorText = document.getElementById('configurationFormColorText');
    const refreshColor = () => {
      if (!colorField || !colorPreview || !colorText) return;
      colorPreview.style.backgroundColor = safeHex(colorField.value) ? colorField.value : '';
      colorText.textContent = safeHex(colorField.value) ? `Vista previa: ${colorField.value}` : 'Sin vista previa. El valor original se conserva sin cambios.';
    };
    refreshColor(); colorField?.addEventListener('input', refreshColor);
    const openConfirmation = () => {
      if (form.dataset.submitting === 'true' || !form.reportValidity()) return;
      resetConfirmation(); reviewedPayload = payload(); summary.replaceChildren();
      form.querySelectorAll('[data-configuration-field]').forEach(field => {
        const control = field.querySelector('input:not([type=hidden]),select,textarea');
        if (!control || control.disabled) return;
        const row = document.createElement('div'), label = document.createElement('dt'), value = document.createElement('dd');
        label.textContent = field.dataset.configurationLabel;
        value.textContent = control.type === 'checkbox' ? (control.checked ? 'Sí' : 'No')
          : control.tagName === 'SELECT' ? control.selectedOptions[0]?.textContent.trim() || 'Sin selección' : control.value || 'Sin dato';
        row.append(label, value); summary.append(row);
      });
      closeSheets(); confirmSheet.showModal();
    };
    form.addEventListener('input', () => { dirty = true; resetConfirmation(); });
    form.addEventListener('change', () => { dirty = true; resetConfirmation(); });
    form.addEventListener('invalid', event => { resetConfirmation(); openAncestors(event.target); }, { capture: true });
    form.addEventListener('submit', event => {
      if (form.dataset.submitting === 'true') { event.preventDefault(); event.stopImmediatePropagation(); return; }
      if (!confirmed || payload() !== reviewedPayload) { event.preventDefault(); event.stopImmediatePropagation(); openConfirmation(); return; }
      resetConfirmation();
    }, { capture: true });
    form.addEventListener('submit', event => {
      if (event.defaultPrevented || form.dataset.submitting !== 'true') return;
      dirty = false; if (review) review.disabled = true; confirm.disabled = true;
    });
    confirm.addEventListener('click', () => {
      if (!confirmSheet.open || form.dataset.submitting === 'true' || !form.reportValidity()) return;
      if (!reviewedPayload || payload() !== reviewedPayload) { openConfirmation(); return; }
      confirmSheet.close(); confirmed = true; form.requestSubmit(review);
    });
    confirmSheet.addEventListener('close', () => {
      if (form.dataset.submitting === 'true') return;
      resetConfirmation();
      if (!confirmSheet.open && !unsavedSheet?.open && !deleteSheet?.open && !leaving) revealEditor();
    });
    const searchForm = document.getElementById('mobileConfigurationSearchForm');
    searchForm?.addEventListener('submit', event => {
      if (!dirty) return;
      event.preventDefault();
      const target = new URL(searchForm.action, location.href);
      target.search = new URLSearchParams([...new FormData(searchForm).entries()]).toString();
      askToLeave(() => { leaving = true; location.assign(target.href); });
    });
    document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
      if (!dirty || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target === '_blank') return;
      event.preventDefault(); askToLeave(() => { leaving = true; location.assign(link.href); });
    }));
    window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('pageshow', () => { resetConfirmation(); leaving = false; if (review) review.disabled = false; confirm.disabled = false; });
    const firstError = form.querySelector('[data-configuration-field]:has(.mi-error)');
    if (firstError) {
      openAncestors(firstError);
      requestAnimationFrame(() => { firstError.scrollIntoView({ block: 'center' }); firstError.querySelector('input:not([type=hidden]),textarea,select')?.focus({ preventScroll: true }); });
    } else if (form.querySelector('#configurationFormErrors')) requestAnimationFrame(() => form.querySelector('#configurationFormErrors').focus({ preventScroll: true }));
  }
  document.getElementById('confirmConfigurationLeave')?.addEventListener('click', () => {
    if (!pendingAction) return;
    const action = pendingAction; pendingAction = undefined; dirty = false; leaving = true; resetConfirmation(); closeSheets(); action();
  });
  unsavedSheet?.addEventListener('close', () => {
    if (!unsavedSheet.open && dirty && !leaving && !confirmSheet?.open) revealEditor();
  });

  const deleteForm = document.getElementById('mobileConfigurationDeleteForm');
  const deleteWord = document.getElementById('configurationDeleteWord');
  const deleteButton = document.getElementById('confirmConfigurationDelete');
  let deleteTarget;
  const updateDeleteValidity = () => {
    if (!deleteWord || !deleteButton) return;
    const valid = deleteWord.value === 'ELIMINAR';
    deleteWord.setCustomValidity(valid ? '' : 'Escribe ELIMINAR exactamente para confirmar.');
    deleteButton.disabled = !valid || deleteForm?.dataset.submitting === 'true';
  };
  const openDelete = button => {
    if (!deleteSheet || !deleteForm || deleteForm.dataset.submitting === 'true') return;
    leaving = false;
    deleteTarget = { url: button.dataset.configurationDelete, name: button.dataset.configurationDeleteName };
    deleteForm.setAttribute('action', deleteTarget.url);
    document.getElementById('configurationDeleteName').textContent = deleteTarget.name;
    deleteWord.value = ''; updateDeleteValidity(); closeSheets(); deleteSheet.showModal(); deleteWord.focus();
  };
  document.querySelectorAll('[data-configuration-delete]').forEach(button => button.addEventListener('click', () => askToLeave(() => openDelete(button))));
  deleteWord?.addEventListener('input', updateDeleteValidity);
  deleteForm?.addEventListener('submit', event => {
    if (!deleteSheet.open || !deleteTarget || deleteForm.getAttribute('action') !== deleteTarget.url
        || deleteWord.value !== 'ELIMINAR' || deleteForm.dataset.submitting === 'true') {
      event.preventDefault(); event.stopImmediatePropagation(); updateDeleteValidity(); return;
    }
    dirty = false;
  }, { capture: true });
  deleteSheet?.addEventListener('close', () => {
    if (deleteForm.dataset.submitting === 'true') return;
    leaving = false;
    deleteWord.value = ''; deleteTarget = undefined; updateDeleteValidity();
  });
  window.addEventListener('pageshow', () => { if (deleteWord) { deleteWord.value = ''; deleteTarget = undefined; updateDeleteValidity(); } });
})();
