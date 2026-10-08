(function () {
    "use strict";

    const form = document.getElementById("ga-form");
    if (!form) return;
    const patientId = form.querySelector('[name="paciente"]');
    const patientSearch = document.getElementById("ga-patient-search");
    const patientSelected = document.getElementById("ga-selected-patient");
    const productSearch = document.getElementById("ga-product-search");
    const surgeryField = form.querySelector('[name="tipo_cirugia"]');
    const linesBody = document.getElementById("ga-lines");
    const linesData = document.getElementById("ga-lines-data");
    const totalDisplay = document.getElementById("ga-total");
    const lineCount = document.getElementById("ga-line-count");
    const emptyDisplay = document.getElementById("ga-lines-empty");
    const errorsDisplay = document.getElementById("ga-client-errors");
    const productFeedback = document.getElementById("ga-product-feedback");
    const subtotalDisplay = document.getElementById("ga-subtotal");
    const taxesDisplay = document.getElementById("ga-taxes");
    const summaryCount = document.getElementById("ga-summary-count");
    const summaryPatient = document.getElementById("ga-summary-patient");
    const taxNote = document.getElementById("ga-tax-note");
    const taxLabel = document.getElementById("ga-tax-label");
    const pricesIncludeTax = form.dataset.priceIncludesTax !== "false";
    const canEditPrice = form.dataset.canEditPrice !== "false";
    const numberFormat = new Intl.NumberFormat("es-HN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    let lineKey = 0;
    let submitting = false;

    const mobileNavigation = document.querySelector(".erp-mobile-bottom-nav");
    const summary = form.querySelector(".ga-compose-summary");
    function positionMobileSummary() {
        const navigationRect = mobileNavigation ? mobileNavigation.getBoundingClientRect() : null;
        const offset = navigationRect && navigationRect.height > 0 ? Math.max(16, window.innerHeight - navigationRect.top + 10) : 16;
        form.style.setProperty("--ga-mobile-nav-offset", offset + "px");
        if (summary) form.style.setProperty("--ga-mobile-summary-height", summary.offsetHeight + "px");
    }
    if (typeof ResizeObserver !== "undefined") {
        const observer = new ResizeObserver(positionMobileSummary);
        if (mobileNavigation) observer.observe(mobileNavigation);
        if (summary) observer.observe(summary);
    }
    window.addEventListener("resize", positionMobileSummary);
    positionMobileSummary();

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

    function updateSurgerySelection() {
        const ready = !surgeryField || Boolean(surgeryField.value);
        productSearch.disabled = !ready;
        productSearch.placeholder = ready ? "Ver catálogo o buscar producto" : "Seleccione primero el tipo de cirugía";
        productFeedback.textContent = ready ? "Haga clic para ver productos. Seleccione uno para agregarlo." : "Seleccione el tipo de cirugía en los datos del paciente para habilitar el catálogo.";
        if (!ready) {
            document.getElementById("ga-product-results").hidden = true;
            productSearch.setAttribute("aria-expanded", "false");
        }
    }
    if (surgeryField) surgeryField.addEventListener("change", updateSurgerySelection);
    updateSurgerySelection();

    function roundEven(numerator, denominator) {
        const quotient = numerator / denominator;
        const remainder = numerator % denominator;
        return quotient + (remainder * 2n > denominator || (remainder * 2n === denominator && quotient % 2n === 1n) ? 1n : 0n);
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
        const meta = [patient.identidad, patient.telefono || patient.whatsapp, patient.expediente_codigo || patient.expediente].filter(Boolean).join(" · ");
        if (meta) patientSelected.appendChild(element("span", "", meta));
        patientSelected.hidden = false;
        if (summaryPatient) summaryPatient.textContent = patient.nombre || patient.text || "Paciente seleccionado";
    }

    function createSearch(input, results, url, select, isProduct) {
        let timer;
        let controller;
        let generation = 0;
        let options = [];
        let active = -1;
        let items = [];
        let nextPage = null;
        let currentQuery = "";
        let loadingMore = false;
        const cache = new Map();

        function close(dismiss) {
            if (dismiss) {
                generation += 1;
                window.clearTimeout(timer);
                if (controller) controller.abort();
            }
            results.hidden = true;
            input.setAttribute("aria-expanded", "false");
            input.removeAttribute("aria-activedescendant");
            active = -1;
        }

        function open() {
            if (input.disabled) { results.hidden = true; return; }
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
            select(item);
            close(true);
        }

        function render(data, append) {
            const scrollTop = results.scrollTop;
            const newItems = Array.isArray(data.results) ? data.results : [];
            items = append ? items.concat(newItems.filter(function (item) { return !items.some(function (existing) { return String(existing.id) === String(item.id); }); })) : newItems;
            nextPage = data.has_more ? data.next_page : null;
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
                const meta = isProduct ? [item.codigo, item.empresa_nombre].filter(Boolean).join(" · ") : [item.identidad, item.telefono || item.whatsapp, item.expediente_codigo || item.expediente].filter(Boolean).join(" · ");
                if (meta) copy.appendChild(element("small", "", meta));
                option.appendChild(copy);
                if (isProduct) {
                    const price = hundredths(item.precio || item.precio_venta || "0");
                    const priceCopy = element("span", "ga-search-price");
                    priceCopy.appendChild(element("strong", "", money(price === null ? 0n : price)));
                    if (item.impuesto_id != null && item.impuesto_activo) priceCopy.appendChild(element("small", "", Number(item.impuesto_porcentaje) === 0 ? "Exento" : "ISV " + item.impuesto_porcentaje + "%"));
                    option.appendChild(priceCopy);
                }
                option.addEventListener("click", function () { choose(item); });
                results.appendChild(option);
                return option;
            });
            if (!options.length) { message("No se encontraron resultados."); return; }
            if (nextPage) {
                const more = element("button", "ga-search-more", "Ver más " + (isProduct ? "productos" : "pacientes"));
                more.type = "button";
                more.addEventListener("click", function () { search(true); });
                results.appendChild(more);
            }
            if (data.total != null) results.appendChild(element("div", "ga-search-meta", items.length + " de " + data.total + " " + (isProduct ? "productos" : "pacientes")));
            open();
            if (append) results.scrollTop = scrollTop;
        }

        function queryValue() {
            // Un paciente ya seleccionado puede sustituirse desde la lista inicial.
            return !isProduct && patientId.value ? "" : input.value.trim();
        }

        async function fetchPage(query, page, signal) {
            const key = query + "\u0000" + page;
            if (cache.has(key)) return cache.get(key);
            const endpoint = new URL(url, window.location.origin);
            endpoint.searchParams.set("q", query);
            endpoint.searchParams.set("page", String(page));
            const pending = fetch(endpoint, { signal: signal, credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (response) {
                if (!response.ok) throw new Error("search");
                return response.json();
            });
            cache.set(key, pending);
            try { return await pending; }
            catch (error) { cache.delete(key); throw error; }
        }

        async function search(append) {
            append = append === true;
            const query = queryValue();
            if (append && (loadingMore || !nextPage || query !== currentQuery)) return;
            const requestGeneration = ++generation;
            if (controller) controller.abort();
            controller = new AbortController();
            const page = append ? nextPage : 1;
            currentQuery = query;
            if (append) {
                loadingMore = true;
                const button = results.querySelector(".ga-search-more");
                if (button) { button.disabled = true; button.textContent = "Cargando…"; }
            } else message(query ? "Buscando…" : "Cargando " + (isProduct ? "productos…" : "pacientes…"));
            try {
                const data = await fetchPage(query, page, controller.signal);
                if (requestGeneration !== generation) return;
                render(data, append);
            } catch (error) {
                if (error.name !== "AbortError" && requestGeneration === generation) message("No se pudo completar la búsqueda. Inténtelo de nuevo.");
            } finally {
                if (requestGeneration === generation) loadingMore = false;
            }
        }

        input.addEventListener("input", function () {
            generation += 1;
            if (controller) controller.abort();
            window.clearTimeout(timer);
            loadingMore = false;
            message(input.value.trim() ? "Buscando…" : "Cargando lista…");
            timer = window.setTimeout(search, 220);
        });
        input.addEventListener("focus", function () { search(); });
        input.addEventListener("click", function () { if (results.hidden) search(); });
        input.addEventListener("blur", function () {
            window.setTimeout(function () { if (!input.parentElement.contains(document.activeElement)) close(true); }, 0);
        });
        input.addEventListener("keydown", function (event) {
            if (event.key === "Escape") { close(true); return; }
            if (event.key === "Enter") {
                event.preventDefault();
                if (!results.hidden && options.length) options[active < 0 ? 0 : active].click();
                return;
            }
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const offset = event.key === "ArrowDown" ? 1 : -1;
                if (results.hidden) {
                    search().then(function () { if (options.length && !results.hidden) setActive(offset > 0 ? 0 : options.length - 1); });
                } else if (options.length) setActive(active < 0 ? (offset > 0 ? 0 : options.length - 1) : (active + offset + options.length) % options.length);
            }
        });
        document.addEventListener("pointerdown", function (event) {
            if (!input.parentElement.contains(event.target)) close(true);
        });
        // Preparar la primera página hace que abrir el campo no requiera escribir.
        fetchPage("", 1).catch(function () {});
    }

    function updateTotals() {
        const rows = Array.from(linesBody.children);
        let total = 0n;
        let subtotalTotal = 0n;
        let taxesTotal = 0n;
        let missingTax = false;
        const data = rows.map(function (row) {
            const quantity = row.querySelector('[data-value="cantidad"]');
            const price = row.querySelector('[data-value="precio_unitario"]');
            const qty = hundredths(quantity.value);
            const unit = hundredths(price.value);
            let subtotal = 0n;
            if (qty !== null && unit !== null) {
                subtotal = roundEven(qty * unit, 100n);
            }
            row.querySelector("[data-subtotal]").textContent = money(subtotal);
            total += subtotal;
            const rate = hundredths(row.dataset.taxRate);
            const knownTax = row.dataset.taxActive === "true" && rate !== null;
            if (!knownTax) missingTax = true;
            const net = pricesIncludeTax && knownTax ? roundEven(subtotal * 10000n, 10000n + rate) : subtotal;
            const tax = pricesIncludeTax ? subtotal - net : (knownTax ? roundEven(subtotal * rate, 10000n) : 0n);
            const lineTax = row.querySelector("[data-line-tax]");
            if (lineTax) {
                lineTax.textContent = !knownTax ? "Impuesto pendiente de configurar" : (rate === 0n ? "Exento · ISV L 0.00" : "ISV " + Number(rate) / 100 + "%: " + money(tax) + (pricesIncludeTax ? " incluido" : " al facturar"));
            }
            subtotalTotal += net;
            taxesTotal += tax;
            return { producto_id: row.dataset.productId, cantidad: canonical(quantity.value), precio_unitario: canonical(price.value) };
        });
        totalDisplay.textContent = money(total);
        if (subtotalDisplay) subtotalDisplay.textContent = missingTax ? "Pendiente" : money(subtotalTotal);
        if (taxesDisplay) taxesDisplay.textContent = missingTax ? "Pendiente" : money(taxesTotal);
        if (taxLabel) taxLabel.textContent = pricesIncludeTax ? "Impuestos incluidos" : "Impuestos al facturar";
        if (taxNote) {
            taxNote.textContent = missingTax ? "Un producto requiere revisar su impuesto antes de facturar." : (pricesIncludeTax ? "Impuestos incluidos según la configuración de cada producto." : "El total corresponde al gasto. Los impuestos se aplican al facturar.");
            taxNote.classList.toggle("ga-tax-pending", missingTax);
        }
        lineCount.textContent = rows.length + (rows.length === 1 ? " línea" : " líneas");
        if (summaryCount) summaryCount.textContent = rows.length + (rows.length === 1 ? " producto seleccionado" : " productos seleccionados");
        emptyDisplay.hidden = rows.length > 0;
        linesData.value = JSON.stringify(data);
    }

    function addLine(product, focusQuantity) {
        const row = element("tr");
        row.dataset.productId = product.producto_id || product.id;
        row.dataset.taxRate = product.impuesto_porcentaje == null ? "" : String(product.impuesto_porcentaje);
        row.dataset.taxActive = String(product.impuesto_activo === true);
        const title = product.nombre || product.descripcion || product.text || "Producto del catálogo";
        const titleCell = element("td");
        titleCell.appendChild(element("strong", "", title));
        const lineMeta = [product.codigo, product.empresa_nombre].filter(Boolean).join(" · ");
        if (lineMeta) titleCell.appendChild(element("span", "ga-cell-sub", lineMeta));
        const lineTax = element("span", "ga-cell-sub ga-line-tax");
        lineTax.dataset.lineTax = "";
        titleCell.appendChild(lineTax);
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
        const priceInput = numberCell(priceId, "precio_unitario", "Precio unitario", product.precio_unitario == null ? (product.precio || product.precio_venta || "0") : product.precio_unitario, "0");
        priceInput.readOnly = !canEditPrice;
        if (!canEditPrice) priceInput.title = "Precio según el catálogo";
        const subtotalCell = element("td", "ga-money");
        subtotalCell.dataset.subtotal = "";
        subtotalCell.dataset.label = "Importe";
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
        if (summaryPatient) summaryPatient.textContent = "Seleccione un paciente";
    });
    createSearch(patientSearch, document.getElementById("ga-patient-results"), form.dataset.patientUrl, selectPatient, false);
    createSearch(productSearch, document.getElementById("ga-product-results"), form.dataset.productUrl, function (product) {
        if (surgeryField && !surgeryField.value) return;
        addLine(product, true);
        productSearch.value = "";
        productFeedback.textContent = "Agregado: " + (product.nombre || product.text || "producto") + ". Puede buscar el siguiente producto.";
    }, true);
    form.querySelectorAll("[data-ga-add-more]").forEach(function (button) {
        button.addEventListener("click", function () {
            productSearch.value = "";
            productSearch.focus();
            productSearch.click();
        });
    });

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
        if (surgeryField && !surgeryField.value) errors.push("Seleccione el tipo de cirugía antes de guardar el gasto adicional.");
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
            else if (surgeryField && !surgeryField.value) surgeryField.focus();
            else if (!linesBody.children.length) productSearch.focus();
            return;
        }
        updateTotals();
        submitting = true;
        form.setAttribute("aria-busy", "true");
        if (event.submitter) event.submitter.textContent = "Guardando…";
    });
})();
