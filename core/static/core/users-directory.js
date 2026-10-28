document.addEventListener("DOMContentLoaded", () => {
    const directory = document.querySelector("[data-users-directory]");
    if (!directory) return;
    const rows = [...directory.querySelectorAll("[data-user-row]")];
    const search = directory.querySelector("[data-user-search]");
    const company = directory.querySelector("[data-user-company]");
    const status = directory.querySelector("[data-user-status]");
    const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const contents = new Map(rows.map(row => [row, normalize(`${row.dataset.userIdentifier} ${row.textContent}`)]));

    function refresh() {
        const query = normalize(search.value);
        let visible = 0;
        rows.forEach(row => {
            const companies = row.dataset.userCompanies.trim().split(/\s+/).filter(Boolean);
            const allCompanies = row.dataset.userAllCompanies === "true";
            const companyMatches = !company.value || (company.value === "none" ? !allCompanies && companies.length === 0 : allCompanies || companies.includes(company.value));
            const stateMatches = !status.value || status.value === row.dataset.userState;
            const matches = companyMatches && stateMatches && contents.get(row).includes(query);
            // Filters only change which accounts are visible; they never alter
            // a form's method, action, CSRF token or account configuration.
            row.hidden = !matches;
            if (matches) visible += 1;
        });
        directory.querySelector("[data-user-table-wrap]").hidden = rows.length > 0 && visible === 0;
        directory.querySelector("[data-user-no-results]").hidden = rows.length === 0 || visible > 0;
        directory.querySelector("[data-user-results]").textContent = `${visible} ${visible === 1 ? "usuario" : "usuarios"} de ${rows.length} en esta vista.`;
        const filtered = Boolean(query || company.value || status.value);
        directory.querySelectorAll(".ud-table-footer [data-user-reset]").forEach(button => { button.hidden = !filtered; });
    }
    search.addEventListener("input", refresh);
    company.addEventListener("change", refresh);
    status.addEventListener("change", refresh);
    directory.querySelectorAll("[data-user-reset]").forEach(button => button.addEventListener("click", () => {
        search.value = "";
        company.value = "";
        status.value = "";
        refresh();
        search.focus();
    }));
    directory.querySelector("[data-user-filter-tools]").hidden = rows.length === 0;
    refresh();
});
