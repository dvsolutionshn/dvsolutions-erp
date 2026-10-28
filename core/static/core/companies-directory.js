document.addEventListener("DOMContentLoaded", () => {
    const directory = document.querySelector("[data-companies-directory]");
    if (!directory) return;
    const cards = [...directory.querySelectorAll("[data-company-card]")];
    const search = directory.querySelector("[data-company-search][type='search']");
    const state = directory.querySelector("[data-company-state-filter]");
    const license = directory.querySelector("[data-company-license-filter]");
    const type = directory.querySelector("[data-company-type-filter]");
    const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const contents = new Map(cards.map(card => [card, normalize(card.dataset.companySearch)]));

    function refresh() {
        const query = normalize(search.value);
        let visible = 0;
        cards.forEach(card => {
            const matches = contents.get(card).includes(query)
                && (!state.value || state.value === card.dataset.companyState)
                && (!license.value || license.value === card.dataset.companyLicense)
                && (!type.value || type.value === card.dataset.companyType);
            // Filters only control visibility. License forms retain their
            // original action, POST method and CSRF protection.
            card.hidden = !matches;
            if (matches) visible += 1;
        });
        directory.querySelector("#control-company-grid").hidden = cards.length > 0 && visible === 0;
        directory.querySelector("[data-company-empty]").hidden = cards.length === 0 || visible > 0;
        directory.querySelector("[data-company-results]").textContent = `${visible} ${visible === 1 ? "empresa" : "empresas"} de ${cards.length} en el directorio.`;
        const filtered = Boolean(query || state.value || license.value || type.value);
        directory.querySelectorAll(".cd-directory-footer [data-company-reset]").forEach(button => { button.hidden = !filtered; });
    }

    search.addEventListener("input", refresh);
    [state, license, type].forEach(filter => filter.addEventListener("change", refresh));
    directory.querySelectorAll("[data-company-reset]").forEach(button => button.addEventListener("click", () => {
        [search, state, license, type].forEach(input => { input.value = ""; });
        refresh();
        search.focus();
    }));
    directory.querySelector("[data-company-filter-tools]").hidden = cards.length === 0;
    refresh();
});
