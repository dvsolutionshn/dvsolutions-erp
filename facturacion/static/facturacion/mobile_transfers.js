/* UI only. The original transfer view validates inventory, warehouses and every posted quantity. */
(() => {
  'use strict';
  document.body.classList.add('mt-mobile-screen');
  document.querySelectorAll('.mi-bottom a.active').forEach(link => { link.classList.remove('active'); link.removeAttribute('aria-current'); });
  const nav = [...document.querySelectorAll('.mi-bottom a')].find(link => link.querySelector('span')?.textContent.trim() === 'Funciones');
  if (nav) { nav.classList.add('active'); nav.setAttribute('aria-current', 'page'); }
  const routeForm = document.getElementById('mobileTransferRouteForm');
  const origin = document.getElementById('transferOrigin'), destination = document.getElementById('transferDestination');
  const form = document.getElementById('mobileTransferForm');
  let routePending = false;
  const syncDestination = () => {
    if (!origin || !destination) return;
    [...destination.options].forEach(option => { option.disabled = Boolean(option.value && option.value === origin.value); });
    if (origin.value && destination.value === origin.value) destination.value = '';
  };
  syncDestination();
  if (!form) { origin?.addEventListener('change', syncDestination); return; }
  const existence = form.querySelector('[name=existencia]'), quantity = form.querySelector('[name=cantidad]');
  const cards = [...document.querySelectorAll('[data-transfer-lot]')];
  const picker = document.getElementById('transferLotSheet'), confirmSheet = document.getElementById('transferConfirmSheet');
  const review = document.getElementById('transferReview'), confirm = document.getElementById('confirmTransfer');
  let selected = null, dirty = form.dataset.transferUnsaved === '1', confirmed = false, activeFilter = 'all';
  const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const label = (card, selector) => card.querySelector(selector)?.textContent.trim() || '';
  const resetConfirmation = () => { confirmed = false; };
  const warningText = card => {
    const labels = [...card.querySelectorAll('.mt-badge.warning,.mt-badge.danger')].map(badge => badge.textContent.trim());
    return labels.length ? `${labels.join(' · ')}. Revisa el lote seleccionado antes de confirmar. Se mantienen las reglas actuales de inventario.` : '';
  };
  const updateWarnings = (node, text) => { node.textContent = text; node.hidden = !text; };
  const selectCard = (card, userAction = false) => {
    selected = card; existence.value = card.dataset.transferLot;
    cards.forEach(item => { const current = item === card; item.classList.toggle('selected', current); item.setAttribute('aria-pressed', String(current)); });
    document.getElementById('transferSelectedName').textContent = label(card, '[data-lot-name]');
    document.getElementById('transferSelectedLot').textContent = `Lote ${label(card, '[data-lot-number]')} · Vence ${label(card, '[data-lot-expiration]')}`;
    const stock = document.getElementById('transferSelectedStock');
    stock.textContent = `Disponible en origen: ${label(card, '.mt-lot-stock strong')}`; stock.hidden = false;
    const available = Number(card.dataset.transferQuantity);
    if (Number.isFinite(available) && available > 0) quantity.max = String(available); else quantity.removeAttribute('max');
    updateWarnings(document.getElementById('transferSelectedWarnings'), warningText(card));
    document.getElementById('transferLotError').hidden = true;
    resetConfirmation();
    if (userAction) { dirty = true; picker.close(); quantity.focus(); }
  };
  cards.forEach(card => card.addEventListener('click', () => selectCard(card, true)));
  const initial = cards.find(card => card.dataset.transferLot === existence.value);
  if (initial) selectCard(initial);
  const filterCards = () => {
    const query = normalize(document.getElementById('transferLotSearch').value);
    cards.forEach(card => {
      const flags = { soon: card.dataset.transferSoon === 'true', expired: card.dataset.transferExpired === 'true', inactive: card.dataset.transferInactive === 'true' };
      card.hidden = !normalize(card.textContent).includes(query) || (activeFilter !== 'all' && !flags[activeFilter]);
    });
    document.getElementById('transferLotSearchEmpty').hidden = cards.some(card => !card.hidden);
  };
  document.getElementById('transferLotSearch').addEventListener('input', filterCards);
  document.querySelectorAll('[data-transfer-filter]').forEach(button => button.addEventListener('click', () => {
    activeFilter = button.dataset.transferFilter;
    document.querySelectorAll('[data-transfer-filter]').forEach(item => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active)); });
    filterCards();
  }));
  const updateRoutePending = () => {
    syncDestination();
    const previousOrigin = form.querySelector('[name=bodega_origen]')?.value;
    const previousDestination = form.querySelector('[name=bodega_destino]')?.value;
    routePending = Boolean(origin && destination && (origin.value !== previousOrigin || destination.value !== previousDestination));
    const notice = document.getElementById('transferRoutePending'); if (notice) notice.hidden = !routePending;
    review.disabled = routePending; resetConfirmation();
  };
  origin?.addEventListener('change', updateRoutePending); destination?.addEventListener('change', updateRoutePending);
  updateRoutePending();
  const openConfirmation = () => {
    if (form.dataset.submitting === 'true' || routePending) return;
    if (!selected) { document.getElementById('transferLotError').hidden = false; document.getElementById('transferChooseLot').focus(); return; }
    if (!form.reportValidity()) return;
    document.querySelector('[data-transfer-confirm-name]').textContent = label(selected, '[data-lot-name]');
    document.querySelector('[data-transfer-confirm-lot]').textContent = label(selected, '[data-lot-number]');
    document.querySelector('[data-transfer-confirm-quantity]').textContent = quantity.value;
    updateWarnings(document.querySelector('[data-transfer-confirm-warnings]'), warningText(selected));
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close());
    resetConfirmation(); confirmSheet.showModal();
  };
  review.addEventListener('click', openConfirmation);
  form.addEventListener('input', event => { if (!event.target.disabled) { dirty = true; resetConfirmation(); } });
  form.addEventListener('change', event => { if (!event.target.disabled) { dirty = true; resetConfirmation(); } });
  form.addEventListener('invalid', resetConfirmation, { capture: true });
  form.addEventListener('submit', event => {
    if (!confirmed || routePending) { event.preventDefault(); event.stopImmediatePropagation(); resetConfirmation(); openConfirmation(); return; }
    confirmed = false;
  }, { capture: true });
  form.addEventListener('submit', event => { if (!event.defaultPrevented) { dirty = false; review.disabled = true; confirm.disabled = true; } });
  confirm.addEventListener('click', () => {
    if (!confirmSheet.open || form.dataset.submitting === 'true' || routePending || !form.reportValidity()) return;
    confirmSheet.close(); confirmed = true; form.requestSubmit();
  });
  confirmSheet.addEventListener('close', () => { if (form.dataset.submitting !== 'true') resetConfirmation(); });
  const unsaved = document.getElementById('transferUnsavedSheet'); let pendingURL;
  const warnBeforeLeave = event => { event.preventDefault(); document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close()); unsaved.showModal(); };
  document.querySelectorAll('a[href]').forEach(link => link.addEventListener('click', event => {
    if (!dirty || event.ctrlKey || event.metaKey || event.shiftKey || link.target === '_blank') return;
    pendingURL = link.href; warnBeforeLeave(event);
  }));
  routeForm?.addEventListener('submit', event => {
    if (!dirty) return;
    const target = new URL(routeForm.action); target.search = new URLSearchParams(new FormData(routeForm)).toString(); pendingURL = target.href; warnBeforeLeave(event);
  });
  document.getElementById('confirmTransferLeave').addEventListener('click', () => { dirty = false; location.assign(pendingURL); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { resetConfirmation(); review.disabled = routePending; confirm.disabled = false; });
})();
