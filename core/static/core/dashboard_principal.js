(function () {
    "use strict";

    function normalize(value) {
        return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
            .toLowerCase().replace(/\s+/g, " ").trim();
    }

    function initializeDashboard(root) {
        if (root.dataset.homeReady === "true") return;
        const form = root.querySelector("[data-home-search-form]");
        const input = root.querySelector("[data-home-search]");
        const grid = root.querySelector("[data-home-module-grid]");
        if (!form || !input || !grid) return;

        const controls = root.querySelector("[data-home-search-controls]");
        const clear = root.querySelector("[data-home-search-clear]");
        const count = root.querySelector("[data-home-count]");
        const status = root.querySelector("[data-home-search-status]");
        const empty = root.querySelector("[data-home-no-results]");
        const serverEmpty = root.querySelector("[data-home-empty]");
        const modules = Array.from(grid.querySelectorAll("[data-home-module]")).map(function (card) {
            return {
                card: card,
                text: normalize((card.dataset.homeTitle || "") + " "
                    + (card.dataset.homeKeywords || card.textContent || "")),
            };
        });
        const total = modules.length;
        const moduleLabel = total === 1 ? " módulo" : " módulos";
        if (serverEmpty) serverEmpty.hidden = total > 0;

        function filter() {
            const query = normalize(input.value);
            const tokens = query ? query.split(" ") : [];
            let matches = 0;
            modules.forEach(function (module) {
                const visible = tokens.every(function (token) { return module.text.includes(token); });
                module.card.hidden = !visible;
                if (visible) matches += 1;
            });
            if (clear) {
                clear.hidden = !input.value;
                clear.disabled = !input.value;
            }
            if (count) {
                count.textContent = (query ? matches + " de " : "") + total + moduleLabel;
                count.hidden = false;
            }
            if (status) {
                status.hidden = !query || total === 0;
                status.textContent = !query || total === 0 ? "" : matches
                    ? matches + " de " + total + moduleLabel
                        + (matches === 1 ? " coincide" : " coinciden") + " con la búsqueda."
                    : "No encontramos módulos con esa búsqueda.";
            }
            if (empty) empty.hidden = !query || matches > 0 || total === 0;
        }

        function reset() {
            input.value = "";
            filter();
            input.focus();
        }

        input.addEventListener("input", filter);
        input.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                event.preventDefault();
                reset();
            }
        });
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            filter();
        });
        if (clear) clear.addEventListener("click", function (event) {
            event.preventDefault();
            reset();
        });
        if (controls) controls.hidden = false;
        root.dataset.homeReady = "true";
        filter();
    }

    function initialize() {
        document.querySelectorAll("[data-home-dashboard]").forEach(initializeDashboard);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, {once: true});
    else initialize();
})();
