/* Recipe business rules and medication-line serialization remain in the canonical web form/editor. */
(() => {
  'use strict';
  document.body.classList.add('mr-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach((link) => {
    link.classList.remove('active');
    link.removeAttribute('aria-current');
  });
  const patientsNav = [...document.querySelectorAll('.mi-bottom a')].find((link) => link.querySelector('span')?.textContent.trim() === 'Pacientes');
  if (patientsNav) { patientsNav.classList.add('active'); patientsNav.setAttribute('aria-current', 'page'); }
  const pdfCard = document.querySelector('[data-recipe-pdf]');
  if (pdfCard) pdfCard.addEventListener('toggle', () => {
    if (!pdfCard.open) return;
    const frame = pdfCard.querySelector('[data-recipe-pdf-frame]');
    if (!frame.hasAttribute('src')) frame.src = frame.dataset.pdfSrc;
    frame.hidden = false;
    pdfCard.querySelector('.mr-pdf-placeholder').hidden = true;
  });
  const form = document.getElementById('mobileRecipeForm');
  if (!form) return;
  // Same saved-template field assignment as the web, with a mobile confirmation before replacing entered text.
  const templateSelect = document.getElementById('recipeTemplateSelect');
  const applyButton = document.getElementById('applyRecipeTemplate');
  const templateData = document.getElementById('rxTemplatesData');
  const templateSheet = document.getElementById('recipeTemplateConfirm');
  if (templateSelect && applyButton && templateData) {
    const templates = JSON.parse(templateData.textContent || '[]');
    let pending;
    const fields = ['diagnostico', 'indicaciones', 'observaciones'].map((name) => form.elements.namedItem(name));
    const applyTemplate = (template) => {
      fields[0].value = template.diagnostico || '';
      fields[1].value = template.indicaciones_generales || '';
      fields[2].value = template.observaciones || '';
      window.recipeMedicationEditor?.setLines(template.detalles || []);
      const status = document.getElementById('recipeTemplateStatus');
      status.textContent = 'Plantilla cargada. Puedes ajustar la receta antes de guardarla.';
      status.hidden = false;
      if (templateSheet.open) templateSheet.close();
      pending = null;
    };
    applyButton.addEventListener('click', () => {
      pending = templates.find((template) => String(template.id) === templateSelect.value);
      if (!pending) { templateSelect.focus(); return; }
      const hasWork = fields.some((input) => input.value.trim()) || Boolean(window.recipeMedicationEditor?.getLines().length);
      if (hasWork) templateSheet.showModal();
      else applyTemplate(pending);
    });
    document.getElementById('confirmRecipeTemplate').addEventListener('click', () => { if (pending) applyTemplate(pending); });
  }
  const manualOptions = document.getElementById('recipeManualOptions');
  const manualSelected = document.getElementById('recipeManualSelected');
  const clearManuals = document.getElementById('clearRecipeManuals');
  if (manualOptions && manualSelected) {
    const inputs = [...manualOptions.querySelectorAll('input[type="checkbox"]')];
    const manualLabel = (input) => input.closest('label')?.textContent.trim() || 'Manual PDF';
    const renderManuals = () => {
      manualSelected.replaceChildren();
      inputs.filter((input) => input.checked).forEach((input) => {
        const chip = document.createElement('span'); chip.className = 'mr-manual-chip';
        const label = document.createElement('span'); label.textContent = manualLabel(input);
        const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', `Quitar ${label.textContent}`);
        remove.addEventListener('click', () => { input.checked = false; renderManuals(); });
        chip.append(label, remove); manualSelected.append(chip);
      });
    };
    inputs.forEach((input) => input.addEventListener('change', renderManuals));
    clearManuals?.addEventListener('click', () => { inputs.forEach((input) => { input.checked = false; }); renderManuals(); });
    renderManuals();
  }
})();
