(function () {
  "use strict";

  function initializeDashboard(dashboard) {
    if (dashboard.dataset.accountingReady === "true") return;

    var search = dashboard.querySelector("[data-module-search]");
    var cards = Array.prototype.slice.call(dashboard.querySelectorAll("[data-accounting-module]"));
    var filters = Array.prototype.slice.call(dashboard.querySelectorAll("[data-module-filter]"));
    var clear = dashboard.querySelector("[data-search-clear]");
    var result = dashboard.querySelector("[data-module-result]");
    var empty = dashboard.querySelector("[data-module-empty]");
    var activeFilter = "all";

    if (!search || !cards.length) return;
    dashboard.dataset.accountingReady = "true";
    dashboard.querySelectorAll("[data-module-controls]").forEach(function (controls) {
      controls.hidden = false;
    });

    function normalize(text) {
      return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    }

    function update() {
      var query = normalize(search.value);
      var words = query.split(/\s+/).filter(Boolean);
      var count = 0;

      cards.forEach(function (card) {
        var text = normalize(card.dataset.search || card.textContent);
        var category = (card.dataset.category || "").split(/\s+/);
        var matchesCategory = activeFilter === "all" || category.indexOf(activeFilter) !== -1;
        var matchesQuery = words.every(function (word) { return text.indexOf(word) !== -1; });
        card.hidden = !(matchesCategory && matchesQuery);
        if (!card.hidden) count += 1;
      });

      filters.forEach(function (button) {
        button.setAttribute("aria-pressed", String(button.dataset.moduleFilter === activeFilter));
      });

      if (clear) clear.hidden = !search.value.length;
      if (empty) empty.hidden = count > 0;
      if (result) {
        var message = count === 1 ? "1 módulo disponible" : count + " módulos disponibles";
        if (query || activeFilter !== "all") message += " de " + cards.length;
        result.textContent = message;
      }
    }

    search.addEventListener("input", update);
    search.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && search.value) {
        event.preventDefault();
        search.value = "";
        update();
      }
    });

    filters.forEach(function (button) {
      button.addEventListener("click", function () {
        activeFilter = button.dataset.moduleFilter || "all";
        update();
      });
    });

    if (clear) {
      clear.addEventListener("click", function () {
        search.value = "";
        update();
        search.focus();
      });
    }

    dashboard.querySelectorAll("[data-module-reset]").forEach(function (button) {
      button.addEventListener("click", function () {
        activeFilter = "all";
        search.value = "";
        update();
        search.focus();
      });
    });

    update();
  }

  function initialize() {
    document.querySelectorAll(".accounting-dashboard").forEach(initializeDashboard);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
