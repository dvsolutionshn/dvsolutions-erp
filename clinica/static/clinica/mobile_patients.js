(() => {
  'use strict';
  document.body.classList.add('mp-mobile-screen');
  // The original model defaults may include seconds; keep their value valid in native mobile widgets.
  document.querySelectorAll('.mp-form input[type="datetime-local"]').forEach((input) => { input.step = '1'; });
  document.querySelectorAll('.mi-bottom a.active').forEach((link) => {
    link.classList.remove('active');
    link.removeAttribute('aria-current');
  });
  const patientsNav = [...document.querySelectorAll('.mi-bottom a')].find((link) => link.querySelector('span')?.textContent.trim() === 'Pacientes');
  if (patientsNav) {
    patientsNav.classList.add('active');
    patientsNav.setAttribute('aria-current', 'page');
  }
  const patientForm = document.getElementById('mobilePatientForm');
  if (!patientForm) return;
  // Presentation only: all field validation and patient synchronization remain on the original form.
  const categoryInputs = [...patientForm.querySelectorAll('[name="motivo_categoria"]')];
  const procedureGroups = [...patientForm.querySelectorAll('[data-procedure-group]')];
  const updateProcedureGroups = () => {
    const selected = new Set(categoryInputs.filter((input) => input.checked).map((input) => input.value));
    let visible = 0;
    procedureGroups.forEach((group) => {
      const hasSavedSelection = Boolean(group.querySelector('input:checked'));
      const show = selected.has(group.dataset.procedureGroup) || hasSavedSelection;
      group.hidden = !show;
      if (show) visible += 1;
      if (hasSavedSelection) group.open = true;
    });
    const hint = patientForm.querySelector('[data-procedure-empty]');
    if (hint) hint.hidden = visible > 0;
  };
  categoryInputs.forEach((input) => input.addEventListener('change', updateProcedureGroups));
  updateProcedureGroups();
  const dependents = [
    ['informante', 'informante_detalle', (value) => value !== 'yo_mismo'],
    ['referido_por', 'referido_por_detalle', (value) => value === 'referencia'],
  ];
  dependents.forEach(([sourceName, targetName, isVisible]) => {
    const source = patientForm.elements.namedItem(sourceName);
    const field = patientForm.querySelector(`[data-patient-field="${targetName}"]`);
    if (!source || !field) return;
    const update = () => { field.hidden = !isVisible(source.value) && !field.querySelector('.mi-error'); };
    source.addEventListener('change', update);
    update();
  });
  const fileInput = patientForm.querySelector('input[name="foto_perfil"][type="file"]');
  const preview = patientForm.querySelector('[data-patient-photo-preview]');
  let previewUrl;
  if (fileInput && preview) fileInput.addEventListener('change', () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = fileInput.files[0];
    if (!file || !file.type.startsWith('image/')) {
      preview.hidden = true;
      preview.removeAttribute('src');
      return;
    }
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    preview.hidden = false;
  });
})();
