document.addEventListener("DOMContentLoaded", () => {
    const directory = document.querySelector("[data-modules-directory]");
    if (!directory) return;
    const cards = [...directory.querySelectorAll("[data-module-card]")];
    const search = directory.querySelector("[data-module-search]");
    const filter = directory.querySelector("[data-module-filter]");
    const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const contents = new Map(cards.map(card => [card, normalize(card.dataset.moduleText)]));

    function refresh() {
        const query = normalize(search.value);
        let visible = 0;
        cards.forEach(card => {
            const plans = Number(card.dataset.modulePlans);
            const companies = Number(card.dataset.moduleCompanies);
            const assigned = !filter.value
                || (filter.value === "planes" && plans > 0)
                || (filter.value === "ajustes" && companies > 0)
                || (filter.value === "sin" && plans === 0 && companies === 0);
            // This catalog is read-only; filtering changes only card visibility.
            card.hidden = !assigned || !contents.get(card).includes(query);
            if (!card.hidden) visible += 1;
        });
        directory.querySelector("[data-module-grid]").hidden = cards.length > 0 && visible === 0;
        directory.querySelector("[data-module-empty]").hidden = cards.length === 0 || visible > 0;
        directory.querySelector("[data-module-results]").textContent = `${visible} ${visible === 1 ? "módulo" : "módulos"} de ${cards.length} en el catálogo.`;
        directory.querySelectorAll(".md-footer [data-module-reset]").forEach(button => { button.hidden = !query && !filter.value; });
    }
    search.addEventListener("input", refresh);
    filter.addEventListener("change", refresh);
    directory.querySelectorAll("[data-module-reset]").forEach(button => button.addEventListener("click", () => {
        search.value = "";
        filter.value = "";
        refresh();
        search.focus();
    }));
    directory.querySelector("[data-module-tools]").hidden = cards.length === 0;
    refresh();
});
