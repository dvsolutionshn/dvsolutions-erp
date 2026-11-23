/* Read-only filtering of the invoices already authorized and rendered by the app. */
(() => {
  "use strict";
  const panel = document.getElementById("facturas-app");
  if (!panel) return;
  const search = panel.querySelector("[data-mobile-invoice-search]");
  if (!search || search.dataset.invoiceSearchReady === "1") return;
  const input = search.querySelector("#mobileInvoiceSearch");
  const count = search.querySelector("#mobileInvoiceSearchCount");
  const empty = search.querySelector("[data-invoice-search-empty]");
  if (!input || !count || !empty) return;

  const normalize = value => String(value || "").normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  const rows = Array.from(panel.querySelectorAll(".mobile-invoices-list .mobile-invoice-row"))
    .map(element => ({
      element,
      customer: normalize(element.querySelector(".mobile-invoice-main strong")?.textContent),
    }));

  function filterInvoices() {
    const terms = normalize(input.value).split(/\s+/).filter(Boolean);
    let matches = 0;
    rows.forEach(row => {
      const visible = terms.every(term => row.customer.includes(term));
      row.element.hidden = !visible;
      if (visible) matches += 1;
    });
    count.textContent = terms.length
      ? `${matches} de ${rows.length} factura${rows.length === 1 ? "" : "s"}`
      : `${rows.length} factura${rows.length === 1 ? "" : "s"} en esta empresa`;
    empty.hidden = !terms.length || rows.length === 0 || matches > 0;
  }

  input.addEventListener("input", filterInvoices);
  input.addEventListener("search", filterInvoices);
  search.dataset.invoiceSearchReady = "1";
  filterInvoices();
  search.hidden = false;
})();
