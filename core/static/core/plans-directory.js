document.addEventListener("DOMContentLoaded", () => {
    const directory = document.querySelector("[data-plans-directory]");
    if (!directory) return;
    const cards = [...directory.querySelectorAll("[data-plan-card]")];
    const search = directory.querySelector("[data-plan-search]");
    const filters = [...directory.querySelectorAll("[data-plan-filter]")];
    const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const contents = new Map(cards.map(card => [card, normalize(card.dataset.planText)]));
    let state = "";

    function refresh() {
        const query = normalize(search.value);
        let visible = 0;
        cards.forEach(card => {
            const matches = contents.get(card).includes(query) && (!state || state === card.dataset.planState);
            // Filters only change visibility; every plan keeps its configuration link.
            card.hidden = !matches;
            if (matches) visible += 1;
        });
        filters.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.planFilter === state)));
        directory.querySelector("[data-plan-grid]").hidden = cards.length > 0 && visible === 0;
        directory.querySelector("[data-plan-empty]").hidden = cards.length === 0 || visible > 0;
        directory.querySelector("[data-plan-results]").textContent = `${visible} ${visible === 1 ? "plan" : "planes"} de ${cards.length} en el catálogo.`;
        directory.querySelectorAll(".pd-footer [data-plan-reset]").forEach(button => { button.hidden = !query && !state; });
    }
    search.addEventListener("input", refresh);
    filters.forEach(button => button.addEventListener("click", () => { state = button.dataset.planFilter; refresh(); }));
    directory.querySelectorAll("[data-plan-reset]").forEach(button => button.addEventListener("click", () => {
        search.value = "";
        state = "";
        refresh();
        search.focus();
    }));
    directory.querySelector("[data-plan-tools]").hidden = cards.length === 0;
    refresh();
});
