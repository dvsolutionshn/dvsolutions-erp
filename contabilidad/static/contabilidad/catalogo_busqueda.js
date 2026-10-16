(function () {
  "use strict";

  function normalize(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/\s+/g, " ").trim();
  }

  function initializeCatalog(panel) {
    if (panel.dataset.searchReady === "true") return;
    const input = panel.querySelector("[data-catalog-search]");
    const clear = panel.querySelector("[data-catalog-clear]");
    const status = panel.querySelector("[data-catalog-status]");
    const table = document.getElementById(input && input.getAttribute("aria-controls"));
    if (!input || !clear || !status || !table) return;
    const rows = Array.from(table.querySelectorAll("[data-catalog-row]"));
    const empty = table.querySelector("[data-catalog-no-results]");
    const accounts = new Map(rows.map(function (row) {
      return [row.dataset.accountId, {
        row: row, parent: row.dataset.accountParent || "", children: [],
        code: normalize(row.dataset.accountCode), name: normalize(row.dataset.accountName),
      }];
    }));
    accounts.forEach(function (account, id) {
      if (accounts.has(account.parent)) accounts.get(account.parent).children.push(id);
    });

    function filter() {
      const query = normalize(input.value);
      const matches = new Set();
      const visible = new Set();
      accounts.forEach(function (account, id) {
        if (!query || account.code.includes(query) || account.name.includes(query)) matches.add(id);
      });
      matches.forEach(function (id) {
        let current = id;
        const ancestors = new Set();
        while (accounts.has(current) && !ancestors.has(current)) {
          ancestors.add(current);
          visible.add(current);
          current = accounts.get(current).parent;
        }
        const descendants = [id];
        const visited = new Set();
        while (descendants.length) {
          current = descendants.pop();
          if (visited.has(current)) continue;
          visited.add(current);
          visible.add(current);
          descendants.push.apply(descendants, accounts.get(current).children);
        }
      });
      accounts.forEach(function (account, id) {
        account.row.hidden = !visible.has(id);
        account.row.classList.toggle("cc-context-row", Boolean(query) && visible.has(id) && !matches.has(id));
        const context = account.row.querySelector("[data-catalog-context]");
        if (context) context.hidden = !query || !visible.has(id) || matches.has(id);
      });
      if (empty) empty.hidden = !query || matches.size > 0;
      clear.disabled = !input.value;
      if (!query) status.textContent = rows.length + (rows.length === 1 ? " cuenta en este catálogo" : " cuentas en este catálogo");
      else if (!matches.size) status.textContent = "No hay coincidencias por código o nombre.";
      else status.textContent = matches.size + (matches.size === 1 ? " coincidencia" : " coincidencias")
        + (visible.size > matches.size ? " · " + visible.size + " cuentas visibles con su jerarquía" : "");
    }

    input.addEventListener("input", filter);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && input.value) {
        event.preventDefault();
        input.value = "";
        filter();
      }
    });
    clear.addEventListener("click", function () { input.value = ""; filter(); input.focus(); });
    panel.dataset.searchReady = "true";
    panel.hidden = false;
    filter();
  }

  function initialize() {
    document.querySelectorAll("[data-catalog-search-panel]").forEach(initializeCatalog);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
