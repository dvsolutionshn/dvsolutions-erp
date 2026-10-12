/* Presentation only; uploads, recipient authorization, signed links and transports use the existing backend. */
(() => {
  'use strict';
  document.body.classList.add('mm-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach((link) => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const functionsNav = [...document.querySelectorAll('.mi-bottom a')].find((link) => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsNav) { functionsNav.classList.add('active'); functionsNav.setAttribute('aria-current', 'page'); }
  const normalize = (value) => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const rows = [...document.querySelectorAll('[data-manual-row]')];
  const choices = [...document.querySelectorAll('[data-manual-choice]')];
  const search = document.querySelector('[data-manual-search-input]');
  const activeFilters = [...document.querySelectorAll('[data-manual-active-filter]')];
  const selectAll = document.querySelector('[data-manual-select-all]');
  const count = document.querySelector('[data-manual-selected-count]');
  const openComposer = document.querySelector('[data-manual-open-composer]');
  let activeFilter = 'all';
  const selected = () => choices.filter((input) => input.checked);
  const visibleChoices = () => choices.filter((input) => !input.closest('[data-manual-row]').hidden);
  const updateSelection = () => {
    const chosen = selected();
    if (count) count.textContent = chosen.length;
    const countLabel = document.querySelector('[data-manual-selected-label]');
    if (countLabel) countLabel.textContent = chosen.length === 1 ? 'seleccionado' : 'seleccionados';
    if (openComposer) openComposer.disabled = chosen.length === 0;
    if (selectAll) {
      const visible = visibleChoices();
      const checked = visible.filter((input) => input.checked).length;
      selectAll.checked = visible.length > 0 && checked === visible.length;
      selectAll.indeterminate = checked > 0 && checked < visible.length;
      selectAll.disabled = visible.length === 0;
    }
    document.querySelectorAll('[data-manual-selected-names]').forEach((container) => {
      container.replaceChildren();
      chosen.forEach((input) => { const label = document.createElement('span'); label.textContent = input.dataset.manualTitle; container.append(label); });
    });
  };
  const filterRows = () => {
    const query = normalize(search?.value);
    rows.forEach((row) => { row.hidden = !normalize(row.dataset.manualSearch).includes(query) || (activeFilter !== 'all' && row.dataset.manualActive !== activeFilter); });
    const noMatch = document.querySelector('[data-manual-filter-empty]');
    if (noMatch) noMatch.hidden = !rows.length || rows.some((row) => !row.hidden);
    updateSelection();
  };
  search?.addEventListener('input', filterRows);
  activeFilters.forEach((button) => button.addEventListener('click', () => {
    activeFilter = button.dataset.manualActiveFilter;
    activeFilters.forEach((item) => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active)); });
    filterRows();
  }));
  choices.forEach((input) => input.addEventListener('change', updateSelection));
  selectAll?.addEventListener('change', () => { visibleChoices().forEach((input) => { input.checked = selectAll.checked; }); updateSelection(); });
  filterRows();
  const upload = document.querySelector('#mobileManualForm input[type="file"][name="archivo"]');
  const uploadState = document.querySelector('[data-manual-file-state]');
  if (upload && uploadState) upload.addEventListener('change', () => {
    const file = upload.files[0];
    uploadState.textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toLocaleString('es-HN', { maximumFractionDigits: 2 })} MB seleccionado` : '';
    uploadState.hidden = !file;
  });
  const form = document.getElementById('mobileManualSendForm');
  const sheet = document.getElementById('manualSendSheet');
  if (!form || !sheet) return;
  const patientId = document.getElementById('manualPatientId');
  const channel = document.getElementById('manualSendChannel');
  const patientSearch = document.getElementById('manualPatientSearch');
  const results = document.getElementById('manualPatientResults');
  const patientCard = document.querySelector('[data-manual-patient-card]');
  const patientName = document.querySelector('[data-manual-patient-name]');
  const patientDetails = document.querySelector('[data-manual-patient-details]');
  const channelOptions = document.querySelector('[data-manual-channel-options]');
  const contactWarning = document.querySelector('[data-manual-contact-warning]');
  const errorBox = document.querySelector('[data-manual-send-error]');
  const chooseStep = document.querySelector('[data-manual-send-step="choose"]');
  const confirmStep = document.querySelector('[data-manual-send-step="confirm"]');
  const preset = document.getElementById('mobileManualPatient');
  let patient = (preset ? JSON.parse(preset.textContent || 'null') : null) || null;
  let timer;
  let controller;
  const showChoose = () => { chooseStep.hidden = false; confirmStep.hidden = true; errorBox.hidden = true; };
  const syncPatient = () => {
    const hasPatient = Boolean(patientId.value);
    patientCard.hidden = !hasPatient;
    patientName.textContent = patient?.nombre || 'Paciente seleccionado';
    patientDetails.textContent = patient ? [patient.expediente ? `Exp. ${patient.expediente}` : '', patient.telefono || 'Sin teléfono registrado', patient.correo || 'Sin correo registrado'].filter(Boolean).join(' · ') : 'Los datos del contacto no están disponibles para este perfil.';
    channelOptions.hidden = !hasPatient;
    const warnings = [];
    if (patient && !patient.telefono) warnings.push('No tiene teléfono o WhatsApp registrado.');
    if (patient && !patient.correo) warnings.push('No tiene correo registrado.');
    contactWarning.textContent = warnings.join(' ');
    contactWarning.hidden = !hasPatient || warnings.length === 0;
    document.querySelectorAll('[data-manual-channel]').forEach((button) => {
      button.disabled = !hasPatient || (patient !== null && !(button.dataset.manualChannel === 'correo' ? patient.correo : patient.telefono));
    });
  };
  if (patient?.id) patientId.value = patient.id;
  syncPatient();
  openComposer?.addEventListener('click', () => {
    if (!selected().length) return;
    updateSelection(); showChoose(); sheet.showModal();
  });
  document.querySelectorAll('[data-manual-channel]').forEach((button) => button.addEventListener('click', () => {
    if (!selected().length || !patientId.value) return;
    channel.value = button.dataset.manualChannel;
    const titles = { whatsapp_manual: 'WhatsApp con enlaces', whatsapp: 'WhatsApp automático', correo: 'Correo electrónico' };
    const help = { whatsapp_manual: 'Se abrirá WhatsApp con los enlaces seguros de los documentos. Revisa el mensaje y confirma el envío en WhatsApp. El registro quedará como preparado manualmente.', whatsapp: 'Se enviarán los documentos mediante la configuración de WhatsApp de esta empresa.', correo: 'Se adjuntarán los PDF al correo registrado del paciente.' };
    document.querySelector('[data-manual-confirm-channel]').textContent = titles[channel.value];
    document.querySelector('[data-manual-confirm-patient]').textContent = patient?.nombre || 'Paciente seleccionado';
    document.querySelector('[data-manual-confirm-recipient]').textContent = patient ? (channel.value === 'correo' ? patient.correo : patient.telefono) : 'El sistema utilizará el contacto registrado; este perfil no puede consultarlo.';
    document.querySelector('[data-manual-confirm-help]').textContent = help[channel.value];
    document.getElementById('confirmManualSend').textContent = channel.value === 'whatsapp_manual' ? 'Abrir WhatsApp' : 'Confirmar envío';
    chooseStep.hidden = true; confirmStep.hidden = false; errorBox.hidden = true;
  }));
  document.querySelector('[data-manual-confirm-back]').addEventListener('click', showChoose);
  form.addEventListener('submit', (event) => {
    if (!confirmStep.hidden && selected().length && patientId.value && ['whatsapp_manual', 'whatsapp', 'correo'].includes(channel.value)) return;
    event.preventDefault(); event.stopImmediatePropagation(); errorBox.textContent = 'Selecciona los manuales, el paciente y el método de envío antes de confirmar.'; errorBox.hidden = false;
  }, { capture: true });
  if (patientSearch && results) {
    const searchState = (text) => { const note = document.createElement('div'); note.className = 'mm-search-state'; note.textContent = text; results.replaceChildren(note); patientSearch.setAttribute('aria-expanded', 'false'); };
    patientSearch.addEventListener('input', () => {
      patient = null; patientId.value = ''; channel.value = ''; syncPatient(); showChoose();
      clearTimeout(timer); controller?.abort();
      const query = patientSearch.value.trim();
      if (query.length < 2) { searchState('Escribe al menos 2 caracteres para buscar.'); return; }
      timer = setTimeout(async () => {
        controller = new AbortController(); const signal = controller.signal; searchState('Buscando pacientes…');
        try {
          const url = new URL(patientSearch.dataset.url, location.origin); url.searchParams.set('q', query);
          const response = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, signal });
          if (!response.ok) throw new Error('search');
          const data = await response.json(); if (signal.aborted) return;
          results.replaceChildren();
          if (!data.results?.length) { searchState('No se encontraron pacientes.'); return; }
          data.results.forEach((item) => {
            const option = document.createElement('button'); option.type = 'button'; option.setAttribute('role', 'option');
            const copy = document.createElement('span'); const name = document.createElement('strong'); name.textContent = item.nombre;
            const details = document.createElement('small'); details.textContent = [item.expediente, item.documento, item.telefono, item.correo].filter(Boolean).join(' · ');
            const arrow = document.createElement('b'); arrow.textContent = '›'; arrow.setAttribute('aria-hidden', 'true'); copy.append(name, details); option.append(copy, arrow);
            option.addEventListener('click', () => { patient = item; patientId.value = item.id; results.replaceChildren(); patientSearch.setAttribute('aria-expanded', 'false'); syncPatient(); });
            results.append(option);
          });
          patientSearch.setAttribute('aria-expanded', 'true');
        } catch (error) { if (error.name !== 'AbortError') searchState('No fue posible buscar pacientes. Intenta nuevamente.'); }
      }, 250);
    });
  }
  if (document.querySelector('[data-manual-autostart]') && selected().length) { showChoose(); sheet.showModal(); }
})();
