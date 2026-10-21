(function () {
  "use strict";
  document.querySelectorAll(".mi-bottom a.active").forEach(link => {
    link.classList.remove("active");
    link.removeAttribute("aria-current");
  });
  const input = document.getElementById("mobileInventorySearch");
  if (!input) return;
  const products = Array.from(document.querySelectorAll("[data-stock-product]"));
  const filters = Array.from(document.querySelectorAll("[data-stock-filter]"));
  let activeFilter = "todos";
  const normalize = value => String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  function applyFilters() {
    const query = normalize(input.value);
    let count = 0;
    products.forEach(product => {
      const matching = normalize(product.dataset.search).includes(query)
        && (activeFilter === "todos" || (activeFilter === "bajo" && product.dataset.stockLow === "1")
          || (activeFilter === "sin" && product.dataset.stockEmpty === "1")
          || (activeFilter === "vencer" && product.dataset.stockExpiry === "1"));
      product.hidden = !matching;
      if (matching) count += 1;
    });
    document.getElementById("mobileInventoryCount").textContent = `${count} producto${count === 1 ? "" : "s"}`;
    document.getElementById("mobileInventoryEmpty").hidden = count > 0;
  }
  input.addEventListener("input", applyFilters);
  filters.forEach(button => button.addEventListener("click", () => {
    activeFilter = button.dataset.stockFilter;
    filters.forEach(item => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    applyFilters();
  }));
})();
