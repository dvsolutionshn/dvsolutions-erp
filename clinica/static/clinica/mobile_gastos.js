/* Mobile presentation adapter. Catalogs, line state, totals and submit validation
   remain in the existing gastos_adicionales.js used by the web. */
(function () {
  "use strict";
  document.body.classList.add("mg-mobile-screen");
  document.querySelectorAll(".mi-bottom a.active").forEach(link => {
    link.classList.remove("active");
    link.removeAttribute("aria-current");
  });

  const form = document.getElementById("ga-form");
  if (!form) return;
  const list = document.getElementById("ga-lines");
  function adaptCards() {
    Array.from(list.children).forEach(row => {
      if (row.dataset.mobileCard === "true") return;
      row.dataset.mobileCard = "true";
      row.setAttribute("role", "group");
      const title = row.querySelector("strong");
      row.setAttribute("aria-label", title ? title.textContent : "Producto seleccionado");
      row.querySelectorAll("td").forEach(cell => cell.setAttribute("role", "presentation"));
      const quantity = row.querySelector('[data-value="cantidad"]');
      if (!quantity) return;
      const controls = document.createElement("div"); controls.className = "mg-quantity-control";
      quantity.parentElement.insertBefore(controls, quantity);
      const minus = document.createElement("button"); minus.type = "button"; minus.textContent = "−"; minus.setAttribute("aria-label", "Disminuir cantidad");
      const plus = document.createElement("button"); plus.type = "button"; plus.textContent = "+"; plus.setAttribute("aria-label", "Aumentar cantidad");
      controls.append(minus, quantity, plus);
      function step(increase) {
        try { increase ? quantity.stepUp(100) : quantity.stepDown(100); }
        catch (_error) { quantity.value = "1"; }
        quantity.dispatchEvent(new Event("input", { bubbles: true }));
      }
      minus.addEventListener("click", () => step(false));
      plus.addEventListener("click", () => step(true));
    });
  }
  const observer = new MutationObserver(adaptCards);
  observer.observe(list, { childList: true });
  adaptCards();
  const surgery = form.querySelector('[name="tipo_cirugia"]');
  const addMore = form.querySelector("[data-ga-add-more]");
  function updateAddMore() { if (addMore && surgery) addMore.disabled = !surgery.value; }
  if (surgery) surgery.addEventListener("change", updateAddMore);
  updateAddMore();
})();
