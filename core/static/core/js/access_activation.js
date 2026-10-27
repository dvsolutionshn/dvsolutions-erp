(() => {
    "use strict";
    document.querySelectorAll("[data-password-toggle]").forEach((button) => {
        const input = document.getElementById(button.dataset.passwordTarget);
        if (!(input instanceof HTMLInputElement) || input.type !== "password") return;
        button.hidden = false;
        button.addEventListener("click", () => {
            const visible = input.type === "password";
            input.type = visible ? "text" : "password";
            button.setAttribute("aria-pressed", String(visible));
            const description = button.dataset.passwordDescription || "contraseña";
            button.setAttribute("aria-label", `${visible ? "Ocultar" : "Mostrar"} ${description}`);
            const label = button.querySelector("[data-password-label]");
            if (label) label.textContent = visible ? "Ocultar" : "Mostrar";
        });
    });
    document.querySelectorAll(".oa-company img").forEach((image) => {
        image.addEventListener("error", () => { image.hidden = true; });
        if (image.complete && image.naturalWidth === 0) image.hidden = true;
    });
    const errors = document.querySelector(".oa-error-summary");
    if (errors) errors.focus();
})();
