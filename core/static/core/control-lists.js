document.addEventListener("DOMContentLoaded", () => {
    const page = document.querySelector("[data-control-list]");
    if (!page) return;
    const normalize = value => (value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    page.querySelectorAll("[data-list-filter]").forEach(section => {
        const rows = [...section.querySelectorAll("[data-list-row]")];
        const search = section.querySelector("[data-list-search]");
        const state = section.querySelector("select[data-list-state]");
        const extra = section.querySelector("select[data-list-extra-filter]");
        const contents = new Map(rows.map(row => [row, normalize(`${row.textContent} ${[...row.querySelectorAll("[title]")].map(element => element.title).join(" ")}`)]));
        function refresh() {
            const query = normalize(search.value);
            let visible = 0;
            rows.forEach(row => {
                const matches = contents.get(row).includes(query)
                    && (!state?.value || state.value === row.dataset.listState)
                    && (!extra?.value || extra.value === row.dataset.listExtra);
                // Filtering is local and only changes visibility. Existing
                // links, POST forms and CSRF tokens keep their behavior.
                row.hidden = !matches;
                if (matches) visible += 1;
            });
            section.querySelector("[data-list-content]").hidden = rows.length > 0 && visible === 0;
            section.querySelector("[data-list-empty]").hidden = rows.length === 0 || visible > 0;
            const result = section.querySelector("[data-list-result]");
            result.textContent = `${visible} de ${rows.length} ${section.dataset.listLabel || "registros"} en esta vista.`;
            result.hidden = false;
        }
        search.addEventListener("input", refresh);
        state?.addEventListener("change", refresh);
        extra?.addEventListener("change", refresh);
        section.querySelectorAll("[data-list-reset]").forEach(button => button.addEventListener("click", () => {
            search.value = "";
            if (state) state.value = "";
            if (extra) extra.value = "";
            refresh();
            search.focus();
        }));
        section.querySelector("[data-list-tools]").hidden = rows.length === 0;
        refresh();
    });
});
