/* Presentation only. The existing server assigns the role for the active company. */
(() => {
  'use strict';
  document.body.classList.add('mus-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => {
    link.classList.remove('active');
    link.removeAttribute('aria-current');
  });
  const functionsLink = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsLink) {
    functionsLink.classList.add('active');
    functionsLink.setAttribute('aria-current', 'page');
  }

  const form = document.getElementById('mobileUserPermissionForm');
  if (!form) return;
  const select = document.getElementById('mobileUserRole');
  const confirmSheet = document.getElementById('mobileUserConfirmSheet');
  const unsavedSheet = document.getElementById('mobileUserUnsavedSheet');
  const values = document.getElementById('mobileUserConfirmValues');
  const confirmation = document.getElementById('mobileUserRoleConfirmation');
  const confirmationLabel = document.getElementById('mobileUserRoleExpected');
  const confirmButton = document.getElementById('confirmMobileUserRole');
  const saveButton = document.getElementById('mobileUserReview');
  const leaveButton = document.getElementById('leaveMobileUserRole');
  const clearWarning = document.getElementById('mobileUserClearWarning');
  let dirty = form.dataset.userRoleBound === 'true';
  let leaving = false;
  let authorized = false;
  let submitting = false;
  let pendingLeave = null;
  let reviewed = '';
  let expectedText = '';

  function selectedRole() {
    const option = select.selectedOptions[0];
    return {
      valid: Boolean(option && !option.disabled && !option.hasAttribute('data-role-placeholder')),
      name: option?.dataset.roleName || option?.textContent.trim() || '',
      clear: Boolean(option?.hasAttribute('data-role-clear')),
      inactive: Boolean(option?.hasAttribute('data-role-inactive')),
    };
  }
  function validateSelection() {
    const role = selectedRole();
    // The explicit empty option is a legitimate canonical "Sin rol" choice.
    // The separate disabled placeholder must never submit or clear a role.
    select.required = !role.clear;
    select.setCustomValidity(role.valid ? '' : 'Seleccione explícitamente un rol o la opción Sin rol.');
  }
  function snapshot() {
    return JSON.stringify({
      body: [...new FormData(form).entries()],
      selectedIndex: select.selectedIndex,
      role: selectedRole(),
      user: form.dataset.userName,
      company: form.dataset.companyName,
      independentAdmin: form.dataset.userIndependentAdmin,
    });
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
    confirmButton.disabled = confirmation.value.trim() !== expectedText || !expectedText || submitting;
  }
  function openReview() {
    validateSelection();
    if (!form.reportValidity()) return;
    if (unsavedSheet.open) unsavedSheet.close();
    const role = selectedRole();
    values.replaceChildren();
    addValue('Empresa · Asignación local', form.dataset.companyName || '');
    addValue('Usuario', form.dataset.userName || '');
    addValue('Cuenta', form.dataset.userAccount || '');
    addValue('Nuevo rol', role.clear ? 'Sin rol asignado' : role.name);
    if (role.inactive) addValue('Estado del rol', 'Inactivo · Este rol no concede permisos');
    if (form.dataset.userIndependentAdmin === 'true') addValue('Autorización independiente', 'Administrador de empresa · Se conserva');
    clearWarning.hidden = !role.clear;
    expectedText = role.clear ? 'SIN ROL' : role.name;
    confirmationLabel.textContent = expectedText;
    confirmation.value = '';
    reviewed = snapshot();
    authorized = false;
    updateConfirmation();
    if (!confirmSheet.open) confirmSheet.showModal();
    confirmation.focus();
  }

  validateSelection();
  select.addEventListener('change', () => {
    dirty = true;
    authorized = false;
    validateSelection();
  });
  form.addEventListener('input', () => { dirty = true; authorized = false; });
  form.addEventListener('invalid', event => {
    let ancestor = event.target.parentElement;
    while (ancestor && ancestor !== form) {
      if (ancestor.tagName === 'DETAILS') ancestor.open = true;
      ancestor = ancestor.parentElement;
    }
  }, true);
  form.addEventListener('submit', event => {
    validateSelection();
    if (submitting || form.dataset.submitting === 'true') {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    const fresh = authorized && confirmSheet.open && selectedRole().valid &&
      confirmation.value.trim() === expectedText && snapshot() === reviewed;
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
    if (submitting || !confirmSheet.open || !selectedRole().valid) return;
    if (reviewed !== snapshot()) { openReview(); return; }
    if (confirmation.value.trim() !== expectedText) { updateConfirmation(); confirmation.focus(); return; }
    validateSelection();
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
    validateSelection();
    updateConfirmation();
  });
})();
