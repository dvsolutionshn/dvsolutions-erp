document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-company-form]");
    if (!form) return;
    const modules = [...form.querySelectorAll("[data-module-option]")];
    const search = form.querySelector("[data-module-search]");
    const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const moduleText = new Map(modules.map(option => [option, normalize(option.textContent)]));
    const text = (selector, value) => form.querySelectorAll(selector).forEach(element => { element.textContent = value; });
    const field = name => form.elements.namedItem(name);
    const selectedText = (name, fallback) => {
        const select = field(name);
        return select?.value ? select.selectedOptions[0].textContent : fallback;
    };
    const reveal = target => {
        let current = target;
        while (current && current !== form) {
            if (current.tagName === "DETAILS") current.open = true;
            current = current.parentElement;
        }
    };

    function updateSummary() {
        const name = field("nombre").value.trim();
        text("[data-preview-name]", name || "Nueva empresa");
        text("[data-preview-slug]", field("slug").value.trim() || "Define su identificador");
        text("[data-preview-initials]", name ? Array.from(name)[0].toUpperCase() : "E");
        const solution = form.querySelector('input[name="tipo_solucion"]:checked');
        text("[data-preview-solution]", solution?.closest(".cf-solution").querySelector("strong").textContent || "Por seleccionar");
        text("[data-preview-plan]", selectedText("plan_comercial", "Sin plan asignado"));
        text("[data-preview-active]", field("activa").checked ? "Activa" : "Inactiva");
        text("[data-preview-license]", selectedText("estado_licencia", "Sin seleccionar"));
        const expiry = field("fecha_vencimiento_plan").value;
        text("[data-preview-expiry]", expiry ? expiry.split("-").reverse().join("/") : "Sin fecha indicada");
        const selected = modules.filter(option => option.querySelector("input").checked).length;
        text("[data-module-selected], [data-preview-modules]", selected);
        text("[data-module-caption]", selected === 1 ? "marcado" : "marcados");
        const logo = field("logo");
        const clearLogo = field("logo-clear");
        const currentLogo = form.querySelector("[data-preview-logo]");
        if (!currentLogo.dataset.initialLabel) currentLogo.dataset.initialLabel = currentLogo.textContent;
        text("[data-preview-logo]", logo.files?.[0]?.name || (clearLogo?.checked ? "Eliminar logo actual" : currentLogo.dataset.initialLabel));
    }

    function filterModules() {
        const query = normalize(search.value);
        let visible = 0;
        modules.forEach(option => {
            option.hidden = !moduleText.get(option).includes(query);
            if (!option.hidden) visible += 1;
        });
        form.querySelector("[data-module-empty]").hidden = modules.length === 0 || visible > 0;
    }

    // Search and details are presentation only; all selected checkboxes keep
    // their names, values and enabled state when the form is submitted.
    search.addEventListener("input", filterModules);
    form.querySelector("[data-module-reset]").addEventListener("click", () => {
        search.value = "";
        filterModules();
        search.focus();
    });
    form.addEventListener("input", updateSummary);
    form.addEventListener("change", event => {
        if (event.target.name === "tipo_solucion" && event.target.value === "clinica") form.querySelector("#cf-clinical").open = true;
        updateSummary();
    });
    form.addEventListener("invalid", event => { reveal(event.target); }, true);
    form.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener("click", event => {
        const target = document.getElementById(link.getAttribute("href").slice(1));
        if (!target) return;
        event.preventDefault();
        reveal(target);
        target.scrollIntoView({block: "start"});
        if (target.tagName === "DETAILS") target.querySelector("summary").focus({preventScroll: true});
        else if (target.matches("input, select, textarea")) target.focus({preventScroll: true});
    }));
    updateSummary();
    filterModules();
    form.querySelector("[data-company-preview]").hidden = false;
    form.querySelector("[data-module-counter]").hidden = false;
    form.querySelector("[data-module-tools]").hidden = modules.length === 0;
    form.querySelector("[data-company-errors]")?.focus();
});
