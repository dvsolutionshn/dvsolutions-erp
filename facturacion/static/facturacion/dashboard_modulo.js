(function () {
    "use strict";

    function normalize(value) {
        return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
            .toLowerCase().replace(/\s+/g, " ").trim();
    }

    function initializeDashboard(root) {
        if (root.dataset.billingReady === "true") return;
        const form = root.querySelector("[data-billing-search-form]");
        const input = root.querySelector("[data-billing-search]");
        if (!form || !input) return;

        const controls = root.querySelector("[data-billing-controls]");
        const clear = root.querySelector("[data-billing-clear]");
        const count = root.querySelector("[data-billing-count]");
        const status = root.querySelector("[data-billing-status]");
        const empty = root.querySelector("[data-billing-empty]");
        const buttons = Array.from(root.querySelectorAll("[data-billing-filter]"));
        const sections = Array.from(root.querySelectorAll("[data-billing-section]")).map(function (section) {
            return {
                element: section,
                group: section.dataset.billingSection,
                cards: Array.from(section.querySelectorAll("[data-billing-card]")).map(function (card) {
                    return {
                        element: card,
                        text: normalize((card.dataset.billingTitle || "") + " "
                            + (card.dataset.billingKeywords || card.textContent || "")),
                    };
                }),
            };
        });
        const total = sections.reduce(function (sum, section) { return sum + section.cards.length; }, 0);
        let selectedGroup = "all";

        buttons.forEach(function (button) {
            const group = button.dataset.billingFilter;
            button.hidden = group !== "all" && !sections.some(function (section) {
                return section.group === group && section.cards.length > 0;
            });
            button.addEventListener("click", function (event) {
                event.preventDefault();
                selectedGroup = group;
                filter(true);
            });
        });

        function filter(announce) {
            const query = normalize(input.value);
            const tokens = query ? query.split(" ") : [];
            let matches = 0;
            sections.forEach(function (section) {
                let sectionMatches = 0;
                section.cards.forEach(function (card) {
                    const visible = (selectedGroup === "all" || selectedGroup === section.group)
                        && tokens.every(function (token) { return card.text.includes(token); });
                    card.element.hidden = !visible;
                    if (visible) sectionMatches += 1;
                });
                section.element.hidden = sectionMatches === 0;
                matches += sectionMatches;
            });
            buttons.forEach(function (button) {
                button.setAttribute("aria-pressed", String(button.dataset.billingFilter === selectedGroup));
            });
            if (clear) {
                clear.hidden = !input.value;
                clear.disabled = !input.value;
            }
            if (count) {
                count.textContent = (query || selectedGroup !== "all" ? matches + " de " : "")
                    + total + (total === 1 ? " acceso" : " accesos");
                count.hidden = total === 0;
            }
            if (status) {
                status.textContent = announce && total > 0
                    ? matches + (matches === 1 ? " acceso encontrado." : " accesos encontrados.") : "";
            }
            if (empty) empty.hidden = matches > 0;
        }

        function clearSearch() {
            input.value = "";
            filter(true);
            input.focus();
        }

        input.addEventListener("input", function () { filter(true); });
        input.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !event.isComposing) {
                event.preventDefault();
                clearSearch();
            }
        });
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            filter(true);
        });
        if (clear) clear.addEventListener("click", function (event) {
            event.preventDefault();
            clearSearch();
        });
        root.querySelectorAll("[data-billing-reset]").forEach(function (button) {
            button.addEventListener("click", function (event) {
                event.preventDefault();
                selectedGroup = "all";
                clearSearch();
            });
        });
        if (controls) controls.hidden = total === 0;
        root.dataset.billingReady = "true";
        filter(false);
    }

    function initialize() {
        document.querySelectorAll("[data-billing-dashboard]").forEach(initializeDashboard);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, {once: true});
    else initialize();
})();
