/* Presentation only: saving and fiscal validation use the existing invoice views. */
(function () {
  "use strict";

  const roundMoney = value => Math.round((value + Number.EPSILON) * 100) / 100;
  function calculateLinePreview(quantity, price, discountPercent, taxPercent, includesTax) {
    const raw = roundMoney(quantity * price);
    const discounted = roundMoney(raw - roundMoney(raw * discountPercent / 100));
    if (includesTax && taxPercent) {
      const divisor = 1 + taxPercent / 100;
      const gross = roundMoney(raw / divisor);
      const subtotal = roundMoney(discounted / divisor);
      return { gross, discount: roundMoney(gross - subtotal), subtotal, tax: roundMoney(discounted - subtotal), total: discounted };
    }
    const tax = roundMoney(discounted * taxPercent / 100);
    return { gross: raw, discount: roundMoney(raw - discounted), subtotal: discounted, tax, total: roundMoney(discounted + tax) };
  }
  window.ClinicInvoiceUI = { calculateLinePreview };

  const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  const readPayload = id => {
    const node = document.getElementById(id);
    if (!node) return [];
    try { return JSON.parse(node.textContent); } catch (_error) { return []; }
  };
  function openSheet(id) {
    const sheet = document.getElementById(id);
    if (!sheet) return;
    document.querySelectorAll("dialog.mi-sheet[open]").forEach(current => current.close());
    sheet.showModal();
  }
  document.querySelectorAll("[data-open-sheet]").forEach(button => button.addEventListener("click", () => openSheet(button.dataset.openSheet)));
  document.querySelectorAll("[data-close-sheet]").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  document.querySelectorAll("dialog.mi-sheet").forEach(sheet => sheet.addEventListener("click", event => {
    if (event.target !== sheet) return;
    const bounds = sheet.getBoundingClientRect();
    if (event.clientY < bounds.top || event.clientY > bounds.bottom || event.clientX < bounds.left || event.clientX > bounds.right) sheet.close();
  }));
  document.querySelectorAll("form[data-submit-once]").forEach(form => form.addEventListener("submit", event => {
    if (event.defaultPrevented) return;
    if (form.dataset.submitting === "true") { event.preventDefault(); return; }
    form.dataset.submitting = "true";
    form.querySelectorAll('button[type="submit"]').forEach(button => {
      button.dataset.originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = "Guardando…";
    });
  }));
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form[data-submit-once]").forEach(form => {
      delete form.dataset.submitting;
      form.querySelectorAll('button[type="submit"]').forEach(button => {
        button.disabled = false;
        if (button.dataset.originalLabel) button.textContent = button.dataset.originalLabel;
      });
    });
  });

  const editor = document.getElementById("mobileInvoiceEditor");
  if (!editor) return;
  const products = readPayload("mobileInvoiceProducts");
  const taxes = new Map(readPayload("mobileInvoiceTaxes").map(tax => [String(tax.id), Number(tax.porcentaje || 0)]));
  const lineModes = new Map(readPayload("mobileInvoiceLineTaxModes").map(line => [String(line.id), Boolean(line.price_includes_tax)]));
  const list = document.getElementById("invoiceLineList");
  const template = document.getElementById("invoiceEmptyLine");
  const prefix = editor.dataset.formsetPrefix;
  const totalForms = editor.querySelector(`[name="${prefix}-TOTAL_FORMS"]`);
  const productSheet = document.getElementById("invoiceProductSheet");
  const productSearch = document.getElementById("invoiceProductSearch");
  const productResults = document.getElementById("invoiceProductResults");
  const currencySelect = editor.querySelector('[name="moneda"]');
  const field = (card, suffix) => card.querySelector(`[name$="-${suffix}"]`);
  const numeric = input => Number(String(input && input.value || 0).replace(",", ".")) || 0;
  const money = value => `${currencySelect && currencySelect.value === "USD" ? "$ " : "L. "}${Number(value).toLocaleString("es-HN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  let activeCard = null;

  function refreshTotals() {
    const totals = { gross: 0, discount: 0, subtotal: 0, tax: 0, total: 0 };
    let count = 0;
    list.querySelectorAll("[data-invoice-line]").forEach(card => {
      const deleted = Boolean(field(card, "DELETE") && field(card, "DELETE").checked);
      card.classList.toggle("is-deleted", deleted);
      if (deleted) return;
      count += 1;
      card.querySelector("[data-line-number]").textContent = `Producto / servicio ${count}`;
      const values = calculateLinePreview(numeric(field(card, "cantidad")), numeric(field(card, "precio_unitario")), numeric(field(card, "descuento_porcentaje")), taxes.get(String(field(card, "impuesto").value)) || 0, card.dataset.taxIncluded === "true");
      card.querySelector("[data-line-total]").textContent = money(values.total);
      Object.keys(totals).forEach(key => { totals[key] += values[key]; });
    });
    document.getElementById("invoiceLineCount").textContent = `${count} ${count === 1 ? "línea" : "líneas"}`;
    Object.entries({ invoicePreviewGross: "gross", invoicePreviewDiscount: "discount", invoicePreviewSubtotal: "subtotal", invoicePreviewTax: "tax", invoicePreviewTotal: "total" }).forEach(([id, key]) => { document.getElementById(id).textContent = money(roundMoney(totals[key])); });
  }
  function updateProductName(card) {
    const select = field(card, "producto");
    const manual = field(card, "descripcion_manual");
    const selected = select && select.selectedOptions[0];
    card.querySelector("[data-product-name]").textContent = select && select.value && selected ? selected.textContent : manual && manual.value || "Seleccionar producto…";
  }
  function initializeCard(card, isNew) {
    const id = field(card, "id");
    if (id && lineModes.has(String(id.value))) card.dataset.taxIncluded = String(lineModes.get(String(id.value)));
    else if (isNew || !id || !id.value) card.dataset.taxIncluded = editor.dataset.taxIncluded;
    ["cantidad", "precio_unitario", "descuento_porcentaje"].forEach(name => {
      const input = field(card, name);
      if (input) input.setAttribute("inputmode", "decimal");
    });
    updateProductName(card);
  }
  function renderProducts() {
    const query = normalize(productSearch.value);
    const selected = products.filter(product => normalize(`${product.nombre} ${product.codigo || ""}`).includes(query));
    productResults.replaceChildren();
    if (!selected.length) {
      const empty = document.createElement("div"); empty.className = "mi-product-empty"; empty.textContent = "No se encontraron productos o servicios."; productResults.append(empty); return;
    }
    selected.slice(0, 80).forEach(product => {
      const button = document.createElement("button"); button.type = "button";
      const label = document.createElement("strong"); label.textContent = product.nombre;
      const price = document.createElement("span"); price.textContent = money(product.precio);
      button.append(label, price);
      button.addEventListener("click", () => {
        if (!activeCard) return;
        const selectedCard = activeCard;
        const select = field(selectedCard, "producto");
        const changed = select.value !== String(product.id);
        select.value = String(product.id);
        if (changed) {
          field(selectedCard, "precio_unitario").value = product.precio;
          field(selectedCard, "impuesto").value = product.impuesto_id == null ? "" : String(product.impuesto_id);
          if (field(selectedCard, "descripcion_manual")) field(selectedCard, "descripcion_manual").value = "";
        }
        if (!field(selectedCard, "id").value) selectedCard.dataset.taxIncluded = String(Boolean(product.price_includes_tax));
        if (!field(selectedCard, "cantidad").value) field(selectedCard, "cantidad").value = "1.00";
        updateProductName(selectedCard); refreshTotals(); productSheet.close();
        field(selectedCard, "cantidad").focus();
      });
      productResults.append(button);
    });
    if (selected.length > 80) { const hint = document.createElement("p"); hint.className = "mi-summary-note"; hint.textContent = "Escribe el nombre o código para encontrar más resultados."; productResults.append(hint); }
  }
  function chooseProduct(card) {
    activeCard = card; productSearch.value = ""; renderProducts(); openSheet("invoiceProductSheet"); productSearch.focus();
  }
  editor.classList.add("enhanced");
  list.querySelectorAll("[data-invoice-line]").forEach(card => initializeCard(card, false));
  list.addEventListener("click", event => {
    const card = event.target.closest("[data-invoice-line]");
    if (!card) return;
    if (event.target.closest("[data-choose-product]")) chooseProduct(card);
    if (event.target.closest("[data-remove-line]")) {
      field(card, "DELETE").checked = true;
      card.querySelectorAll("input,select,textarea").forEach(input => { input.required = false; });
      refreshTotals();
    }
  });
  editor.addEventListener("input", refreshTotals);
  editor.addEventListener("change", refreshTotals);
  document.getElementById("invoiceAddLine").addEventListener("click", () => {
    const index = Number(totalForms.value);
    const holder = document.createElement("template"); holder.innerHTML = template.innerHTML.replace(/__prefix__/g, String(index));
    const card = holder.content.querySelector("[data-invoice-line]");
    list.append(holder.content); totalForms.value = String(index + 1); initializeCard(card, true); refreshTotals(); chooseProduct(card);
  });
  productSearch.addEventListener("input", renderProducts);
  productSheet.addEventListener("close", () => {
    if (!activeCard) return;
    const manual = field(activeCard, "descripcion_manual");
    if (!field(activeCard, "id").value && !field(activeCard, "producto").value && !(manual && manual.value)) {
      field(activeCard, "DELETE").checked = true;
      activeCard.querySelectorAll("input,select,textarea").forEach(input => { input.required = false; });
      refreshTotals();
    }
    activeCard = null;
  });

  const clientSelect = editor.querySelector('[name="cliente"]');
  const clientSearch = document.getElementById("invoiceClientSearch");
  const clientResults = document.getElementById("invoiceClientResults");
  const clients = Array.from(clientSelect.options).filter(option => option.value).map(option => ({ id: option.value, name: option.textContent }));
  clientSearch.addEventListener("input", () => {
    const query = normalize(clientSearch.value);
    clientResults.replaceChildren(); clientResults.hidden = !query;
    if (!query) return;
    const matches = clients.filter(client => normalize(client.name).includes(query));
    matches.slice(0, 20).forEach(client => {
      const button = document.createElement("button"); button.type = "button"; button.textContent = client.name;
      button.addEventListener("click", () => { clientSelect.value = client.id; clientSearch.value = ""; clientResults.hidden = true; clientSelect.dispatchEvent(new Event("change", { bubbles: true })); });
      clientResults.append(button);
    });
    if (!matches.length) { const empty = document.createElement("div"); empty.className = "mi-product-empty"; empty.textContent = "No se encontró ese cliente."; clientResults.append(empty); }
  });
  const dateInput = editor.querySelector('[name="fecha_emision"]');
  const fiscalPrefix = document.getElementById("invoiceFiscalPrefix");
  if (dateInput && !dateInput.disabled && fiscalPrefix && editor.dataset.prefixUrl) {
    dateInput.addEventListener("change", async () => {
      if (!dateInput.value) return;
      const note = document.getElementById("invoiceFiscalPrefixNote");
      try {
        const url = new URL(editor.dataset.prefixUrl, window.location.origin); url.searchParams.set("fecha", dateInput.value);
        const response = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" });
        const data = await response.json();
        fiscalPrefix.textContent = data.ok ? data.prefijo : "Sin CAI para esta fecha";
        if (data.detalle) note.textContent = data.detalle;
      } catch (_error) { note.textContent = "No se pudo consultar el CAI. Se validará al guardar la factura."; }
    });
  }
  refreshTotals();
})();
