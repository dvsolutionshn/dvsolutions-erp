document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-user-form]");
    if (!form) return;
    const primary = form.querySelector("#id_empresa");
    const companyCards = [...form.querySelectorAll("[data-company-card]")];
    const roleCards = [...form.querySelectorAll("[data-company-role-card]")];
    const tabs = [...form.querySelectorAll("[data-company-tab]")];
    const editing = form.dataset.editing === "true";
    let activeCompany = null;
    const text = (selector, value) => {
        form.querySelectorAll(selector).forEach(element => { element.textContent = value; });
    };
    const normalize = value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
    const selectedCompanies = () => {
        const ids = new Set([...form.querySelectorAll('input[name="empresas_acceso"]:checked')].map(input => input.value));
        if (primary?.value) ids.add(primary.value);
        return ids;
    };
    const quickMode = () => form.querySelector('input[name="modo_creacion"]:checked')?.value === "rapido";
    const reveal = target => {
        let parent = target?.parentElement;
        while (parent && parent !== form) {
            if (parent.tagName === "DETAILS") parent.open = true;
            parent = parent.parentElement;
        }
        if (target?.tagName === "DETAILS") target.open = true;
    };

    function updateSummary() {
        const first = form.querySelector("#id_first_name")?.value.trim() || "";
        const last = form.querySelector("#id_last_name")?.value.trim() || "";
        text("[data-preview-name]", [first, last].filter(Boolean).join(" ") || "Nuevo usuario");
        text("[data-preview-email]", form.querySelector("#id_email")?.value.trim() || "Agrega su correo electrónico");
        text("[data-preview-initials]", [first, last].filter(Boolean).map(name => Array.from(name)[0]).join("").toUpperCase() || "NU");
        text("[data-preview-primary]", primary?.value ? primary.selectedOptions[0].textContent : "Por seleccionar");
        text("[data-preview-access]", editing ? "Cuenta existente" : quickMode() ? "Contraseña inicial" : "Invitación por correo");
        text("[data-submit-label]", editing ? "Guardar cambios" : quickMode() ? "Crear usuario" : "Crear y enviar invitación");
        const note = form.querySelector("[data-preview-note] p");
        if (note) note.textContent = editing ? "Los cambios se aplicarán al guardar." : quickMode() ? "Guardamos sus permisos y habilitamos la cuenta con la contraseña inicial." : "Primero guardamos sus permisos. Después enviamos la invitación.";
        const ids = selectedCompanies();
        text("[data-company-count], [data-preview-company-count]", ids.size);
        text("[data-company-count-caption]", ids.size === 1 ? "empresa" : "empresas");
        const preview = form.querySelector("[data-preview-companies]");
        preview.replaceChildren();
        roleCards.forEach(card => {
            const inputs = [...card.querySelectorAll('[data-personal-permissions] input[type="checkbox"]')];
            const count = inputs.filter(input => input.checked).length;
            card.querySelector("[data-permission-count]").textContent = count;
            card.querySelector("[data-permission-count-caption]").textContent = count === 1 ? "seleccionado" : "seleccionados";
            card.querySelectorAll(".personal-permission-group").forEach(group => {
                const fields = [...group.querySelectorAll('input[type="checkbox"]')];
                const checked = fields.filter(input => input.checked).length;
                const badge = group.querySelector("[data-module-count]");
                badge.textContent = `${checked}/${fields.length}`;
                badge.classList.toggle("has-selection", checked > 0);
            });
            if (!ids.has(card.dataset.companyId)) return;
            const item = document.createElement("li");
            const name = document.createElement("strong");
            name.textContent = card.dataset.companyName;
            const detail = document.createElement("span");
            if (card.querySelector("[data-permission-mode]").value === "personalizado") {
                detail.textContent = count ? `${count} ${count === 1 ? "permiso seleccionado" : "permisos seleccionados"}` : "Sin permisos seleccionados";
            } else {
                const select = card.querySelector("[data-company-role-select]");
                const globalRole = form.querySelector("#id_rol_sistema");
                const role = select.value ? select.selectedOptions[0].textContent : globalRole?.value ? globalRole.selectedOptions[0].textContent : "Sin rol asignado";
                detail.textContent = `Rol: ${role}`;
            }
            item.append(name, detail);
            preview.append(item);
        });
        form.querySelector("[data-preview-empty]").hidden = ids.size > 0;
        const administrator = ["#id_es_administrador_empresa", "#id_is_superuser"].some(selector => form.querySelector(selector)?.checked);
        form.querySelector("[data-preview-admin]").hidden = !administrator;
    }

    function updateCompanies() {
        const ids = selectedCompanies();
        if (!ids.has(activeCompany)) activeCompany = ids.has(primary?.value) ? primary.value : [...ids][0] || null;
        const query = normalize(form.querySelector("[data-company-search]")?.value || "");
        let matches = 0;
        companyCards.forEach(card => {
            const checkbox = card.querySelector('input[name="empresas_acceso"]');
            const principal = checkbox.value === primary?.value;
            card.classList.toggle("is-selected", ids.has(checkbox.value));
            card.classList.toggle("is-principal", principal);
            card.querySelector("[data-company-card-caption]").textContent = principal ? "Empresa principal · siempre incluida" : "Acceso adicional";
            card.hidden = !normalize(card.dataset.companyText).includes(query);
            if (!card.hidden) matches += 1;
        });
        form.querySelector("[data-company-empty]").hidden = matches > 0;
        form.querySelector("[data-company-role-empty]").hidden = ids.size > 0;
        form.querySelector("[data-company-tabs]").hidden = ids.size === 0;
        tabs.forEach(tab => {
            tab.hidden = !ids.has(tab.dataset.companyTab);
            tab.setAttribute("aria-pressed", String(tab.dataset.companyTab === activeCompany));
        });
        roleCards.forEach(card => {
            const authorized = ids.has(card.dataset.companyId);
            card.classList.toggle("is-visible", authorized);
            card.hidden = !authorized || card.dataset.companyId !== activeCompany;
            const mode = card.querySelector("[data-permission-mode]");
            const personal = card.querySelector("[data-personal-permissions]");
            const role = card.querySelector("[data-company-role-choice]");
            const custom = mode.value === "personalizado";
            // Tabs and searches only affect presentation. Other selected companies
            // must still submit their complete permission selection.
            mode.disabled = !authorized;
            personal.hidden = !custom;
            personal.querySelectorAll('input[type="checkbox"]').forEach(input => { input.disabled = !authorized || !custom; });
            role.hidden = custom;
            role.querySelectorAll("select").forEach(select => { select.disabled = !authorized || custom; });
        });
        updateSummary();
    }

    function updateCreationMode() {
        form.querySelectorAll("[data-quick-password-field]").forEach(field => {
            field.hidden = !quickMode();
            field.querySelectorAll("input").forEach(input => { input.disabled = !quickMode(); });
        });
        form.querySelectorAll(".access-mode-card").forEach(card => {
            card.classList.toggle("is-selected", card.querySelector('input[type="radio"]').checked);
        });
        updateSummary();
    }

    tabs.forEach(tab => tab.addEventListener("click", () => { activeCompany = tab.dataset.companyTab; updateCompanies(); }));
    form.querySelector("[data-company-search]")?.addEventListener("input", updateCompanies);
    roleCards.forEach(card => {
        card.querySelector("[data-permission-search]").addEventListener("input", event => {
            const query = normalize(event.target.value);
            let matchingGroups = 0;
            card.querySelectorAll(".personal-permission-group").forEach(group => {
                const title = normalize(group.querySelector("[data-permission-group-title]").textContent);
                if (query && group.dataset.openBeforeSearch === undefined) group.dataset.openBeforeSearch = String(group.open);
                let matches = 0;
                group.querySelectorAll(".personal-permission-option").forEach(option => {
                    option.hidden = !!query && !normalize(`${title} ${option.textContent}`).includes(query);
                    if (!option.hidden) matches += 1;
                });
                group.hidden = matches === 0;
                if (query && matches) group.open = true;
                if (!query && group.dataset.openBeforeSearch !== undefined) {
                    group.open = group.dataset.openBeforeSearch === "true";
                    delete group.dataset.openBeforeSearch;
                }
                if (!group.hidden) matchingGroups += 1;
            });
            card.querySelector("[data-permission-empty]").hidden = matchingGroups > 0;
        });
    });
    form.addEventListener("input", event => {
        if (["first_name", "last_name", "email"].includes(event.target.name)) updateSummary();
    });
    form.addEventListener("change", event => {
        if (event.target.name === "modo_creacion") updateCreationMode();
        else if (event.target === primary || event.target.name === "empresas_acceso" || event.target.matches("[data-permission-mode]")) updateCompanies();
        else updateSummary();
    });
    form.querySelectorAll("[data-password-eye]").forEach(button => button.addEventListener("click", () => {
        const input = document.getElementById(button.getAttribute("aria-controls"));
        const visible = input.type === "password";
        input.type = visible ? "text" : "password";
        button.textContent = visible ? "Ocultar" : "Mostrar";
        button.setAttribute("aria-pressed", String(visible));
    }));
    form.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener("click", () => {
        reveal(document.getElementById(link.getAttribute("href").slice(1)));
    }));
    form.addEventListener("invalid", event => { reveal(event.target); }, true);
    document.querySelector("[data-temp-password-form]")?.addEventListener("submit", event => {
        if (!window.confirm("Se reemplazará la contraseña actual y se mostrará una temporal una sola vez. ¿Deseas continuar?")) event.preventDefault();
    });
    updateCompanies();
    updateCreationMode();
    if (form.querySelector("[data-error-summary]")) form.querySelector("[data-error-summary]").focus();
});
