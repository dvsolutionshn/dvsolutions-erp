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
  const product = document.getElementById('transferProduct');
  const existence = form.querySelector('[name=existencia]'), quantity = form.querySelector('[name=cantidad]');
  const cards = [...document.querySelectorAll('[data-transfer-product]')];
  const lotOptions = [...existence.options].slice(1);
  const picker = document.getElementById('transferLotSheet'), confirmSheet = document.getElementById('transferConfirmSheet');
  const review = document.getElementById('transferReview'), confirm = document.getElementById('confirmTransfer');
  const stockError = document.getElementById('transferProductStockError');
  let selected = null, dirty = form.dataset.transferUnsaved === '1', confirmed = false, hasStock = false;
  const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
  const label = (card, selector) => card.querySelector(selector)?.textContent.trim() || '';
  const resetConfirmation = () => { confirmed = false; };
  const updateWarnings = (node, text) => { node.textContent = text; node.hidden = !text; };
  const selectedLot = () => existence.selectedOptions[0];
  const lotWarning = option => {
    if (!option?.value) return '';
    const warnings = [];
    if (option.dataset.expired === 'true') warnings.push('Caducado');
    else if (option.dataset.soon === 'true') warnings.push('Próximo a vencer');
    if (option.dataset.inactive === 'true') warnings.push('Lote inactivo');
    return warnings.length ? `${warnings.join(' · ')}. Revisa el lote seleccionado antes de confirmar.` : '';
  };
  const syncReview = () => { review.disabled = routePending || !hasStock; };
  const syncLotDetails = () => {
    const option = selectedLot();
    const available = Number(option?.dataset.quantity);
    if (option?.value && Number.isFinite(available) && available > 0) quantity.max = String(available);
    else quantity.removeAttribute('max');
    updateWarnings(document.getElementById('transferSelectedWarnings'), lotWarning(option));
    document.getElementById('transferLotError').hidden = true;
    resetConfirmation();
  };
  const selectCard = (card, userAction = false) => {
    selected = card; product.value = card.dataset.transferProduct;
    cards.forEach(item => { const current = item === card; item.classList.toggle('selected', current); item.setAttribute('aria-pressed', String(current)); });
    let visibleLots = 0;
    lotOptions.forEach(option => {
      const visible = option.dataset.productId === product.value;
      option.hidden = !visible; option.disabled = !visible;
      if (visible) visibleLots += 1;
    });
    if (!selectedLot()?.value || selectedLot().disabled) existence.value = '';
    existence.disabled = visibleLots === 0;
    if (visibleLots === 1 && !existence.value) existence.value = lotOptions.find(option => !option.disabled)?.value || '';
    hasStock = visibleLots > 0;
    document.getElementById('transferSelectedName').textContent = label(card, '[data-lot-name]');
    document.getElementById('transferSelectedLot').textContent = hasStock ? `${visibleLots} lote(s) disponible(s) para elegir.` : 'Sin existencia en la bodega origen.';
    const stock = document.getElementById('transferSelectedStock');
    stock.textContent = `Disponible en origen: ${label(card, '.mt-lot-stock strong')}`; stock.hidden = false;
    stockError.hidden = hasStock;
    syncLotDetails(); syncReview();
    if (userAction) { dirty = true; picker.close(); (hasStock ? existence : document.getElementById('transferChooseLot')).focus(); }
  };
  cards.forEach(card => card.addEventListener('click', () => selectCard(card, true)));
  existence.addEventListener('change', () => { dirty = true; syncLotDetails(); });
  const initial = cards.find(card => card.dataset.transferProduct === (product.value || selectedLot()?.dataset.productId));
  if (initial) selectCard(initial);
  else { lotOptions.forEach(option => { option.hidden = true; option.disabled = true; }); syncReview(); }
  const filterCards = () => {
    const query = normalize(document.getElementById('transferLotSearch').value);
    cards.forEach(card => { card.hidden = !normalize(card.textContent).includes(query); });
    document.getElementById('transferLotSearchEmpty').hidden = cards.some(card => !card.hidden);
  };
  document.getElementById('transferLotSearch').addEventListener('input', filterCards);
  const updateRoutePending = () => {
    syncDestination();
    const previousOrigin = form.querySelector('[name=bodega_origen]')?.value;
    const previousDestination = form.querySelector('[name=bodega_destino]')?.value;
    routePending = Boolean(origin && destination && (origin.value !== previousOrigin || destination.value !== previousDestination));
    const notice = document.getElementById('transferRoutePending'); if (notice) notice.hidden = !routePending;
    syncReview(); resetConfirmation();
  };
  origin?.addEventListener('change', updateRoutePending); destination?.addEventListener('change', updateRoutePending);
  updateRoutePending();
  const openConfirmation = () => {
    if (form.dataset.submitting === 'true' || routePending) return;
    if (!selected) { document.getElementById('transferLotError').hidden = false; document.getElementById('transferChooseLot').focus(); return; }
    if (!hasStock) { stockError.hidden = false; document.getElementById('transferChooseLot').focus(); return; }
    if (!existence.value) { document.getElementById('transferLotError').hidden = false; existence.focus(); return; }
    if (!form.reportValidity()) return;
    document.querySelector('[data-transfer-confirm-name]').textContent = label(selected, '[data-lot-name]');
    document.querySelector('[data-transfer-confirm-lot]').textContent = selectedLot().textContent.trim();
    document.querySelector('[data-transfer-confirm-quantity]').textContent = quantity.value;
    updateWarnings(document.querySelector('[data-transfer-confirm-warnings]'), lotWarning(selectedLot()));
    document.querySelectorAll('dialog[open]').forEach(sheet => sheet.close());
    resetConfirmation(); confirmSheet.showModal();
  };
  review.addEventListener('click', openConfirmation);
  form.addEventListener('input', event => { if (!event.target.disabled) { dirty = true; resetConfirmation(); } });
  form.addEventListener('change', event => { if (!event.target.disabled) { dirty = true; resetConfirmation(); } });
  form.addEventListener('invalid', resetConfirmation, { capture: true });
  form.addEventListener('submit', event => {
    if (!confirmed || routePending || !hasStock) { event.preventDefault(); event.stopImmediatePropagation(); resetConfirmation(); openConfirmation(); return; }
    confirmed = false;
  }, { capture: true });
  form.addEventListener('submit', event => { if (!event.defaultPrevented) { dirty = false; review.disabled = true; confirm.disabled = true; } });
  confirm.addEventListener('click', () => {
    if (!confirmSheet.open || form.dataset.submitting === 'true' || routePending || !hasStock || !form.reportValidity()) return;
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
  window.addEventListener('pageshow', () => { resetConfirmation(); syncReview(); confirm.disabled = false; });
})();
