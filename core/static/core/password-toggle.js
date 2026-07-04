document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
        const input = document.getElementById(button.getAttribute("aria-controls"));
        if (!input) return;

        button.addEventListener("click", function () {
            const willShow = input.type === "password";
            input.type = willShow ? "text" : "password";
            button.setAttribute("aria-pressed", String(willShow));
            button.setAttribute("aria-label", willShow ? "Ocultar contraseña" : "Mostrar contraseña");
            button.querySelector("[data-password-toggle-label]").textContent = willShow ? "Ocultar" : "Mostrar";
            input.focus({ preventScroll: true });
            input.setSelectionRange(input.value.length, input.value.length);
        });
    });
});
