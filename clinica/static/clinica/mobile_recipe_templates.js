/* Presentation only. The unchanged canonical medication editor and backend own template parsing and saves. */
(() => {
  'use strict';
  document.body.classList.add('mt-mobile-screen', 'mr-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const functionsNav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (functionsNav) { functionsNav.classList.add('active'); functionsNav.setAttribute('aria-current', 'page'); }
  const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const rows = [...document.querySelectorAll('[data-template-row]')];
  const search = document.querySelector('[data-template-search]');
  const filters = [...document.querySelectorAll('[data-template-state]')];
  let state = 'all';
  const update = () => {
    const query = normalize(search?.value);
    rows.forEach(row => { row.hidden = !normalize(row.dataset.templateText).includes(query) || (state !== 'all' && row.dataset.templateActive !== state); });
    const visible = rows.filter(row => !row.hidden).length;
    const count = document.querySelector('[data-template-visible-count]');
    if (count) count.textContent = visible === rows.length ? '' : `${visible} visibles`;
    const empty = document.querySelector('[data-template-filter-empty]');
    if (empty) empty.hidden = !rows.length || visible > 0;
  };
  search?.addEventListener('input', update);
  filters.forEach(button => button.addEventListener('click', () => {
    state = button.dataset.templateState;
    filters.forEach(item => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active)); });
    update();
  }));
  update();
})();
