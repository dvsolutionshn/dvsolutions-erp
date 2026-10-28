document.addEventListener("DOMContentLoaded", () => {
    const detail = document.querySelector("[data-company-detail]");
    if (!detail) return;
    const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    detail.querySelectorAll("[data-detail-filter]").forEach(section => {
        const rows = [...section.querySelectorAll("[data-filter-row]")];
        const search = section.querySelector("[data-filter-search]");
        const state = section.querySelector("select[data-filter-state]");
        const contents = new Map(rows.map(row => [row, normalize(row.textContent)]));
        function refresh() {
            const query = normalize(search.value);
            let visible = 0;
            rows.forEach(row => {
                const matches = contents.get(row).includes(query) && (!state?.value || state.value === row.dataset.filterState);
                // Only visibility changes; links, forms and CSRF stay intact.
                row.hidden = !matches;
                if (matches) visible += 1;
            });
            section.querySelector("[data-filter-table]").hidden = rows.length > 0 && visible === 0;
            section.querySelector("[data-filter-empty]").hidden = rows.length === 0 || visible > 0;
            const result = section.querySelector("[data-filter-result]");
            result.textContent = `${visible} de ${rows.length} ${section.dataset.filterLabel} en esta vista.`;
            result.hidden = rows.length === 0;
        }
        search.addEventListener("input", refresh);
        state?.addEventListener("change", refresh);
        section.querySelector("[data-filter-reset]").addEventListener("click", () => {
            search.value = "";
            if (state) state.value = "";
            refresh();
            search.focus();
        });
        section.querySelector("[data-filter-tools]").hidden = rows.length === 0;
        refresh();
    });
});
