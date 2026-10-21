(function () {
    "use strict";

    document.querySelectorAll("form[data-login-form]").forEach(function (form) {
        const tokenInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
        const refreshUrl = form.dataset.csrfRefreshUrl;
        if (!tokenInput || !refreshUrl || !window.fetch || !window.AbortController) return;

        const buttons = Array.from(form.querySelectorAll('button[type="submit"], input[type="submit"]'));
        const originals = buttons.map(function (button) {
            return { disabled: button.disabled, html: button.innerHTML, value: button.value };
        });
        const status = document.createElement("p");
        status.className = "messages erp-field-errors";
        status.setAttribute("role", "alert");
        status.hidden = true;
        form.prepend(status);
        let submitting = false;
        let controller = null;

        function reset() {
            submitting = false;
            form.removeAttribute("aria-busy");
            buttons.forEach(function (button, index) {
                button.disabled = originals[index].disabled;
                if (button.tagName === "INPUT") button.value = originals[index].value;
                else button.innerHTML = originals[index].html;
            });
        }

        form.addEventListener("submit", async function (event) {
            if (event.defaultPrevented) return;
            event.preventDefault();
            if (submitting) return;
            submitting = true;
            status.hidden = true;
            form.setAttribute("aria-busy", "true");
            buttons.forEach(function (button) {
                button.disabled = true;
                if (button.tagName === "INPUT") button.value = "Ingresando…";
                else button.textContent = "Ingresando…";
            });

            const activeController = new AbortController();
            controller = activeController;
            const timeout = window.setTimeout(function () { activeController.abort(); }, 10000);
            try {
                const response = await fetch(refreshUrl, {
                    method: "GET",
                    credentials: "same-origin",
                    mode: "same-origin",
                    cache: "no-store",
                    redirect: "error",
                    headers: { Accept: "application/json" },
                    signal: activeController.signal,
                });
                if (!response.ok) throw new Error("No se pudo renovar el acceso.");
                const data = await response.json();
                if (typeof data.csrfToken !== "string" || !/^[a-zA-Z0-9]{64}$/.test(data.csrfToken)) {
                    throw new Error("Token de acceso inválido.");
                }
                if (controller !== activeController) return;
                if (!form.reportValidity()) {
                    reset();
                    return;
                }
                tokenInput.value = data.csrfToken;
                // El evento submit ya pasó la validación del navegador. Se hace
                // un único POST normal, sin almacenar ni reenviar contraseñas.
                HTMLFormElement.prototype.submit.call(form);
            } catch (error) {
                if (controller !== activeController) return;
                reset();
                status.textContent = "No pudimos conectar para iniciar sesión. Tus datos siguen aquí; intenta nuevamente.";
                status.hidden = false;
            } finally {
                window.clearTimeout(timeout);
                if (controller === activeController) controller = null;
            }
        });

        window.addEventListener("pageshow", function (event) {
            if (!event.persisted) return;
            if (controller) controller.abort();
            controller = null;
            reset();
            status.hidden = true;
        });
    });
}());
