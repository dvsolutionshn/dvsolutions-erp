(function () {
    "use strict";

    const form = document.getElementById("ga-form");
    if (!form) return;
    const patientId = form.querySelector('[name="paciente"]');
    const patientSearch = document.getElementById("ga-patient-search");
    const patientSelected = document.getElementById("ga-selected-patient");
    const productSearch = document.getElementById("ga-product-search");
    const linesBody = document.getElementById("ga-lines");
    const linesData = document.getElementById("ga-lines-data");
    const totalDisplay = document.getElementById("ga-total");
    const lineCount = document.getElementById("ga-line-count");
    const emptyDisplay = document.getElementById("ga-lines-empty");
    const errorsDisplay = document.getElementById("ga-client-errors");
    const productFeedback = document.getElementById("ga-product-feedback");
    const numberFormat = new Intl.NumberFormat("es-HN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    let lineKey = 0;
    let submitting = false;

    function readData(id, fallback) {
        try {
            const node = document.getElementById(id);
            let value = JSON.parse(node ? node.textContent : "null");
            if (typeof value === "string") value = JSON.parse(value);
            return value == null ? fallback : value;
        } catch (_) {
            return fallback;
        }
    }

    // Work in hundredths so displayed line totals match decimal rounding on save.
    function hundredths(value) {
        const match = String(value).trim().match(/^(\d*)(?:\.(\d{0,2}))?$/);
        if (!match || (!match[1] && !match[2])) return null;
        return BigInt(match[1] || "0") * 100n + BigInt((match[2] || "").padEnd(2, "0"));
    }

    function canonical(value) {
        const amount = hundredths(value);
        if (amount === null) return String(value);
        return String(amount / 100n) + "." + String(amount % 100n).padStart(2, "0");
    }

    function money(cents) {
        return "L " + numberFormat.format(Number(cents) / 100);
    }

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function selectPatient(patient) {
        patientId.value = patient.id;
        patientSearch.value = patient.nombre || patient.text || "";
        patientSelected.replaceChildren(element("strong", "", patient.nombre || patient.text || "Paciente seleccionado"));
        const meta = [patient.identidad, patient.expediente_codigo || patient.expediente].filter(Boolean).join(" · ");
        if (meta) patientSelected.appendChild(element("span", "", meta));
        patientSelected.hidden = false;
    }

    function createSearch(input, results, url, select, isProduct) {
        let timer;
        let controller;
        let generation = 0;
        let options = [];
        let active = -1;

        function close() {
            results.hidden = true;
            input.setAttribute("aria-expanded", "false");
            input.removeAttribute("aria-activedescendant");
            active = -1;
        }

        function open() {
            results.hidden = false;
            input.setAttribute("aria-expanded", "true");
        }

        function message(text) {
            results.replaceChildren(element("div", "ga-search-message", text));
            options = [];
            active = -1;
            input.removeAttribute("aria-activedescendant");
            open();
        }

        function setActive(index) {
            active = index;
            options.forEach(function (option, optionIndex) {
                option.setAttribute("aria-selected", String(index === optionIndex));
            });
            if (options[index]) {
                input.setAttribute("aria-activedescendant", options[index].id);
                options[index].scrollIntoView({ block: "nearest" });
            }
        }

        function choose(item) {
            generation += 1;
            if (controller) controller.abort();
            window.clearTimeout(timer);
            select(item);
            close();
        }

        function render(items) {
            results.replaceChildren();
            active = -1;
            input.removeAttribute("aria-activedescendant");
            options = items.map(function (item, index) {
                const option = element("button", "ga-search-option");
                option.type = "button";
                option.id = results.id + "-" + index;
                option.setAttribute("role", "option");
                option.setAttribute("aria-selected", "false");
                const copy = element("span");
                copy.appendChild(element("strong", "", item.nombre || item.text || ""));
                const meta = isProduct ? item.codigo : [item.identidad, item.expediente_codigo || item.expediente].filter(Boolean).join(" · ");
                if (meta) copy.appendChild(element("small", "", meta));
                option.appendChild(copy);
                if (isProduct) {
                    const price = hundredths(item.precio || item.precio_venta || "0");
                    option.appendChild(element("span", "ga-search-price", money(price === null ? 0n : price)));
                }
                option.addEventListener("click", function () { choose(item); });
                results.appendChild(option);
                return option;
            });
            if (!options.length) message("No se encontraron resultados.");
            else open();
        }

        async function search() {
            const query = input.value.trim();
            const requestGeneration = ++generation;
            if (controller) controller.abort();
            if (query.length < 2) { close(); return; }
            controller = new AbortController();
            message("Buscando…");
            try {
                const endpoint = new URL(url, window.location.origin);
                endpoint.searchParams.set("q", query);
                const response = await fetch(endpoint, { signal: controller.signal, credentials: "same-origin", headers: { Accept: "application/json" } });
                if (!response.ok) throw new Error("search");
                const data = await response.json();
                if (requestGeneration !== generation) return;
                render(Array.isArray(data.results) ? data.results : []);
            } catch (error) {
                if (error.name !== "AbortError" && requestGeneration === generation) message("No se pudo completar la búsqueda. Inténtelo de nuevo.");
            }
        }

        input.addEventListener("input", function () {
            generation += 1;
            if (controller) controller.abort();
            window.clearTimeout(timer);
            close();
            timer = window.setTimeout(search, 220);
        });
        input.addEventListener("focus", function () {
            if (input.value.trim().length >= 2) search();
        });
        input.addEventListener("keydown", function (event) {
            if (event.key === "Escape") { close(); return; }
            if (event.key === "Enter") {
                event.preventDefault();
                if (!results.hidden && options.length) options[active < 0 ? 0 : active].click();
                return;
            }
            if (!results.hidden && options.length && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
                event.preventDefault();
                const offset = event.key === "ArrowDown" ? 1 : -1;
                setActive((active + offset + options.length) % options.length);
            }
        });
        document.addEventListener("pointerdown", function (event) {
            if (!input.parentElement.contains(event.target)) close();
        });
    }

    function updateTotals() {
        const rows = Array.from(linesBody.children);
        let total = 0n;
        const data = rows.map(function (row) {
            const quantity = row.querySelector('[data-value="cantidad"]');
            const price = row.querySelector('[data-value="precio_unitario"]');
            const qty = hundredths(quantity.value);
            const unit = hundredths(price.value);
            let subtotal = 0n;
            if (qty !== null && unit !== null) {
                const product = qty * unit;
                subtotal = product / 100n;
                const remainder = product % 100n;
                if (remainder > 50n || (remainder === 50n && subtotal % 2n === 1n)) subtotal += 1n;
            }
            row.querySelector("[data-subtotal]").textContent = money(subtotal);
            total += subtotal;
            return { producto_id: row.dataset.productId, cantidad: canonical(quantity.value), precio_unitario: canonical(price.value) };
        });
        totalDisplay.textContent = money(total);
        lineCount.textContent = rows.length + (rows.length === 1 ? " línea" : " líneas");
        emptyDisplay.hidden = rows.length > 0;
        linesData.value = JSON.stringify(data);
    }

    function addLine(product, focusQuantity) {
        const row = element("tr");
        row.dataset.productId = product.producto_id || product.id;
        const title = product.nombre || product.descripcion || product.text || "Producto del catálogo";
        const titleCell = element("td");
        titleCell.appendChild(element("strong", "", title));
        if (product.codigo) titleCell.appendChild(element("span", "ga-cell-sub", product.codigo));
        row.appendChild(titleCell);
        const quantityId = "ga-quantity-" + (++lineKey);
        const priceId = "ga-price-" + lineKey;
        function numberCell(id, type, label, value, minimum) {
            const cell = element("td");
            cell.dataset.label = label;
            const hiddenLabel = element("label", "ga-sr-only", label + ": " + title);
            hiddenLabel.htmlFor = id;
            const input = element("input");
            input.id = id;
            input.type = "number";
            input.min = minimum;
            input.step = "0.01";
            input.required = true;
            input.inputMode = "decimal";
            input.value = value;
            input.dataset.value = type;
            input.addEventListener("input", updateTotals);
            cell.append(hiddenLabel, input);
            row.appendChild(cell);
            return input;
        }
        const quantity = numberCell(quantityId, "cantidad", "Cantidad", product.cantidad == null ? "1" : product.cantidad, "0.01");
        numberCell(priceId, "precio_unitario", "Precio unitario", product.precio_unitario == null ? (product.precio || product.precio_venta || "0") : product.precio_unitario, "0");
        const subtotalCell = element("td", "ga-money");
        subtotalCell.dataset.subtotal = "";
        subtotalCell.dataset.label = "Subtotal";
        row.appendChild(subtotalCell);
        const removeCell = element("td");
        const removeButton = element("button", "ga-line-remove", "×");
        removeButton.type = "button";
        removeButton.setAttribute("aria-label", "Eliminar " + title);
        removeButton.title = "Eliminar línea";
        removeButton.addEventListener("click", function () {
            row.remove();
            updateTotals();
            productFeedback.textContent = "Línea eliminada: " + title + ".";
            productSearch.focus();
        });
        removeCell.appendChild(removeButton);
        row.appendChild(removeCell);
        linesBody.appendChild(row);
        updateTotals();
        if (focusQuantity) {
            quantity.focus();
            quantity.select();
        }
    }

    patientSearch.addEventListener("input", function () {
        patientId.value = "";
        patientSelected.hidden = true;
    });
    createSearch(patientSearch, document.getElementById("ga-patient-results"), form.dataset.patientUrl, selectPatient, false);
    createSearch(productSearch, document.getElementById("ga-product-results"), form.dataset.productUrl, function (product) {
        addLine(product, true);
        productSearch.value = "";
        productFeedback.textContent = "Agregado: " + (product.nombre || product.text || "producto") + ". Puede buscar el siguiente producto.";
    }, true);

    const initialPatient = readData("ga-initial-patient", null);
    if (initialPatient && initialPatient.id) selectPatient(initialPatient);
    let initialLines = readData("ga-initial-lines", []);
    if (!Array.isArray(initialLines) || !initialLines.length) {
        try { initialLines = JSON.parse(linesData.value || "[]"); } catch (_) { initialLines = []; }
    }
    if (Array.isArray(initialLines)) initialLines.forEach(function (line) { addLine(line, false); });
    updateTotals();

    form.addEventListener("submit", function (event) {
        if (submitting) { event.preventDefault(); return; }
        const errors = [];
        if (!patientId.value) errors.push("Seleccione un paciente de los resultados de búsqueda.");
        if (!linesBody.children.length) errors.push("Agregue al menos un producto o servicio.");
        Array.from(linesBody.children).forEach(function (row, index) {
            const quantity = hundredths(row.querySelector('[data-value="cantidad"]').value);
            const price = hundredths(row.querySelector('[data-value="precio_unitario"]').value);
            if (quantity === null || quantity <= 0n) errors.push("Línea " + (index + 1) + ": indique una cantidad mayor que cero con hasta dos decimales.");
            if (price === null) errors.push("Línea " + (index + 1) + ": indique un precio válido con hasta dos decimales.");
        });
        errorsDisplay.replaceChildren();
        errors.forEach(function (error) { errorsDisplay.appendChild(element("p", "", error)); });
        errorsDisplay.hidden = !errors.length;
        if (errors.length) {
            event.preventDefault();
            errorsDisplay.scrollIntoView({ block: "center", behavior: "smooth" });
            if (!patientId.value) patientSearch.focus();
            else if (!linesBody.children.length) productSearch.focus();
            return;
        }
        updateTotals();
        submitting = true;
        form.setAttribute("aria-busy", "true");
        if (event.submitter) event.submitter.textContent = "Guardando…";
    });
})();
