/* Display only: the canonical forms, permissions, uploads and protected file views remain authoritative. */
(() => {
  'use strict';
  document.body.classList.add('ma-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const functionsNav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsNav) { functionsNav.classList.add('active'); functionsNav.setAttribute('aria-current', 'page'); }
  const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const rows = [...document.querySelectorAll('[data-annex-row]')];
  const search = document.querySelector('[data-annex-search-input]');
  const filters = [...document.querySelectorAll('[data-annex-format-filter],[data-annex-state-filter]')];
  let format = 'todos';
  let state = '';
  const filterRows = () => {
    const query = normalize(search?.value);
    rows.forEach(row => { row.hidden = !normalize(row.dataset.annexSearch).includes(query) || (format !== 'todos' && row.dataset.annexFormat !== format) || (state && row.dataset.annexState !== state); });
    const visible = rows.filter(row => !row.hidden).length;
    const empty = document.querySelector('[data-annex-filter-empty]');
    if (empty) empty.hidden = !rows.length || visible > 0;
    const count = document.querySelector('[data-annex-visible-count]');
    if (count) count.textContent = visible === rows.length ? '' : `${visible} visibles`;
  };
  search?.addEventListener('input', filterRows);
  filters.forEach(button => button.addEventListener('click', () => {
    format = button.dataset.annexFormatFilter || 'todos'; state = button.dataset.annexStateFilter || '';
    filters.forEach(item => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active)); });
    filterRows();
  }));
  filterRows();
  const mediaSheet = document.getElementById('annexMediaSheet');
  if (mediaSheet) {
    const image = document.getElementById('annexPreviewImage');
    const video = document.getElementById('annexPreviewVideo');
    document.querySelectorAll('[data-annex-preview]').forEach(button => button.addEventListener('click', () => {
      video.pause(); video.removeAttribute('src'); video.hidden = true;
      image.removeAttribute('src'); image.hidden = true;
      const url = button.dataset.annexPreview;
      document.getElementById('annexMediaTitle').textContent = button.dataset.annexTitle;
      document.getElementById('annexPreviewOpen').href = url;
      if (button.dataset.annexMedia === 'video') { video.src = url; video.hidden = false; }
      else { image.src = url; image.alt = button.dataset.annexTitle; image.hidden = false; }
      mediaSheet.showModal();
    }));
    mediaSheet.addEventListener('close', () => { video.pause(); video.removeAttribute('src'); video.load(); image.removeAttribute('src'); });
  }
  const form = document.getElementById('mobileAnnexForm');
  if (!form) return;
  form.querySelectorAll('input[type=datetime-local]').forEach(input => { input.step = '1'; });
  form.querySelectorAll('input[type=file]').forEach(input => {
    const status = [...form.querySelectorAll('[data-annex-file-state]')].find(node => node.dataset.annexFileState === input.id);
    if (!status) return;
    const update = () => {
      const file = input.files[0]; status.hidden = !file;
      status.querySelector('[data-annex-file-name]').textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toLocaleString('es-HN', { maximumFractionDigits: 2 })} MB seleccionado` : '';
    };
    input.addEventListener('change', update);
    status.querySelector('[data-annex-clear-file]').addEventListener('click', () => { input.value = ''; update(); });
  });
})();
