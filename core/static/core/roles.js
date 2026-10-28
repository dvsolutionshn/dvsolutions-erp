document.addEventListener("DOMContentLoaded", () => {
    const normalize = text => text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const catalog = document.querySelector("[data-role-catalog]");
    if (catalog) {
        const search = catalog.querySelector("[data-role-search]");
        const cards = [...catalog.querySelectorAll("[data-role-card]")];
        const filters = [...catalog.querySelectorAll("[data-role-state]")];
        const contents = new Map(cards.map(card => [card, normalize(card.textContent)]));
        let state = "all";
        function refreshCatalog() {
            const query = normalize(search.value);
            let shown = 0;
            cards.forEach(card => {
                const visible = contents.get(card).includes(query) && (state === "all" || card.dataset.roleActive === state);
                card.hidden = !visible;
                if (visible) shown += 1;
            });
            catalog.querySelector("[data-role-no-results]").hidden = cards.length === 0 || shown > 0;
            catalog.querySelector("[data-role-results]").textContent = `${shown} ${shown === 1 ? "rol" : "roles"} de ${cards.length} en el catálogo.`;
            filters.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.roleState === state)));
        }
        search.addEventListener("input", refreshCatalog);
        filters.forEach(button => button.addEventListener("click", () => { state = button.dataset.roleState; refreshCatalog(); }));
        catalog.querySelector("[data-role-reset]").addEventListener("click", () => { search.value = ""; state = "all"; refreshCatalog(); search.focus(); });
        catalog.querySelector("[data-role-catalog-tools]").hidden = cards.length === 0;
        refreshCatalog();
    }

    const form = document.querySelector("[data-role-editor]");
    if (!form) return;
    const search = form.querySelector("[data-role-permission-search]");
    const moduleFilter = form.querySelector("[data-role-module-filter]");
    const groups = [...form.querySelectorAll("[data-role-group]")];
    const groupFields = new Map(groups.map(group => [group, [...group.querySelectorAll('[data-role-permission] input[type="checkbox"]')]]));
    const permissionTexts = new Map([...form.querySelectorAll("[data-role-permission]")].map(option => [option, normalize(option.textContent)]));
    const roleName = form.querySelector("#id_nombre");
    const roleCode = form.querySelector("#id_codigo");
    const active = form.querySelector("#id_activo");
    const setText = (selector, text) => { form.querySelector(selector).textContent = text; };

    function updateSummary() {
        let total = 0;
        let available = 0;
        let selectedGroups = 0;
        groups.forEach(group => {
            const fields = groupFields.get(group);
            const selected = fields.filter(field => field.checked).length;
            const badge = group.querySelector("[data-role-group-count]");
            badge.textContent = `${selected}/${fields.length}`;
            badge.classList.toggle("has-selection", selected > 0);
            const summary = form.querySelector(`[data-role-summary-group="${group.dataset.roleGroup}"]`);
            summary.hidden = selected === 0;
            summary.querySelector("[data-role-summary-count]").textContent = selected;
            total += selected;
            available += fields.length;
            if (selected) selectedGroups += 1;
        });
        setText("[data-role-name-preview]", roleName.value.trim() || "Nuevo rol");
        setText("[data-role-code-preview]", roleCode.value.trim() || "Código por definir");
        setText("[data-role-selected-total]", total);
        setText("[data-role-selected-caption]", total === 1 ? "permiso seleccionado" : "permisos seleccionados");
        setText("[data-role-available-total]", available);
        setText("[data-role-selected-groups]", selectedGroups);
        form.querySelector("[data-role-selection-empty]").hidden = total > 0;
        form.querySelector(".rp-role-review").classList.toggle("is-inactive", !active.checked);
        setText("[data-role-state-note]", active.checked ? "El rol está habilitado para sus asignaciones." : "Mientras esté inactivo, este rol no autoriza acciones.");
    }

    function filterPermissions() {
        const query = normalize(search.value);
        const module = moduleFilter.value;
        const filtering = Boolean(query || module);
        let visibleActions = 0;
        groups.forEach(group => {
            if (filtering && group.dataset.openBeforeFilter === undefined) group.dataset.openBeforeFilter = String(group.open);
            const groupName = normalize(group.querySelector("[data-role-group-name]").textContent);
            let matches = 0;
            group.querySelectorAll("[data-role-permission]").forEach(option => {
                const visible = !query || `${groupName} ${permissionTexts.get(option)}`.includes(query);
                // Filtering only changes presentation. Every checkbox stays enabled
                // and retains its value, including selections in hidden modules.
                option.hidden = !visible;
                if (visible) matches += 1;
            });
            group.hidden = matches === 0 || (module !== "" && module !== group.dataset.roleGroup);
            if (!group.hidden) visibleActions += matches;
            if (filtering && !group.hidden) group.open = true;
            if (!filtering && group.dataset.openBeforeFilter !== undefined) {
                group.open = group.dataset.openBeforeFilter === "true";
                delete group.dataset.openBeforeFilter;
            }
        });
        form.querySelector("[data-role-permission-empty]").hidden = visibleActions > 0;
        setText("[data-role-permission-results]", `${visibleActions} acciones visibles. Los filtros conservan toda la selección.`);
    }

    function resetFilters() { search.value = ""; moduleFilter.value = ""; filterPermissions(); }
    search.addEventListener("input", filterPermissions);
    moduleFilter.addEventListener("change", filterPermissions);
    form.querySelector("[data-role-permission-reset]").addEventListener("click", () => { resetFilters(); search.focus(); });
    form.querySelectorAll("[data-role-open-group]").forEach(button => button.addEventListener("click", () => {
        resetFilters();
        const group = document.getElementById(`rp-module-${button.dataset.roleOpenGroup}`);
        group.open = true;
        group.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
        group.querySelector("summary").focus({ preventScroll: true });
    }));
    form.addEventListener("change", updateSummary);
    roleName.addEventListener("input", updateSummary);
    roleCode.addEventListener("input", updateSummary);
    const revealField = field => {
        const group = field?.closest("[data-role-group]");
        if (group) { resetFilters(); group.open = true; }
    };
    form.addEventListener("invalid", event => revealField(event.target), true);
    form.querySelectorAll('.ua-error-links a[href^="#"]').forEach(link => link.addEventListener("click", () => revealField(document.getElementById(link.hash.slice(1)))));
    groups.forEach(group => { if (groupFields.get(group).some(field => field.checked)) group.open = true; });
    form.querySelector("[data-role-permission-tools]").hidden = false;
    updateSummary();
    filterPermissions();
    form.querySelector("[data-role-errors]")?.focus();
});
