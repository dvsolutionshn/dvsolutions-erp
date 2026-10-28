document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-commercial-plan-form]");
    if (!form) return;
    const modules = [...form.querySelectorAll("[data-plan-module]")];
    const search = form.querySelector("[data-module-search]");
    const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const moduleText = new Map(modules.map(option => [option, normalize(option.querySelector(".pf-module-label").textContent)]));
    const field = name => form.elements.namedItem(name);
    const text = (selector, value) => form.querySelectorAll(selector).forEach(element => { element.textContent = value; });
    const money = new Intl.NumberFormat("es-HN", {minimumFractionDigits: 2, maximumFractionDigits: 2});

    function updateSummary() {
        const name = field("nombre").value.trim();
        text("[data-preview-name]", name || "Nuevo plan");
        text("[data-preview-code]", field("codigo").value.trim() || "Define su código interno");
        text("[data-preview-initials]", name ? Array.from(name)[0].toUpperCase() : "P");
        const price = field("precio_mensual").value;
        text("[data-preview-price]", price !== "" && Number.isFinite(Number(price)) ? `L. ${money.format(Number(price))}` : "Por indicar");
        const active = field("activo").checked;
        text("[data-preview-status-label]", active ? "Activo" : "Inactivo");
        form.querySelector("[data-preview-status]").dataset.state = active ? "activo" : "inactivo";
        const selected = modules.filter(option => option.querySelector("input").checked);
        text("[data-module-count], [data-preview-count]", selected.length);
        text("[data-module-count-caption]", selected.length === 1 ? "seleccionado" : "seleccionados");
        const preview = form.querySelector("[data-preview-modules]");
        preview.replaceChildren();
        selected.forEach(option => {
            const item = document.createElement("li");
            item.textContent = option.querySelector(".pf-module-label").textContent;
            preview.append(item);
        });
        form.querySelector("[data-preview-empty]").hidden = selected.length > 0;
    }

    function filterModules() {
        const query = normalize(search.value);
        let visible = 0;
        modules.forEach(option => {
            // Search keeps every checkbox enabled and preserves its selection.
            option.hidden = !moduleText.get(option).includes(query);
            if (!option.hidden) visible += 1;
        });
        form.querySelector("[data-module-empty]").hidden = modules.length === 0 || visible > 0;
    }
    search.addEventListener("input", filterModules);
    form.querySelector("[data-module-reset]").addEventListener("click", () => {
        search.value = "";
        filterModules();
        search.focus();
    });
    form.addEventListener("input", updateSummary);
    form.addEventListener("change", updateSummary);
    form.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener("click", event => {
        const target = document.getElementById(link.getAttribute("href").slice(1));
        if (!target) return;
        event.preventDefault();
        target.scrollIntoView({block: "start"});
        if (target.matches("input, textarea")) target.focus({preventScroll: true});
        if (target.id === "pf-modules") search.focus({preventScroll: true});
    }));
    updateSummary();
    filterModules();
    form.querySelector("[data-plan-preview]").hidden = false;
    form.querySelector("[data-plan-module-counter]").hidden = false;
    form.querySelector("[data-module-tools]").hidden = modules.length === 0;
    form.querySelector("[data-plan-errors]")?.focus();
});
