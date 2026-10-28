document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("[data-license-renewal]");
    if (!form) return;
    const field = name => form.elements.namedItem(name);
    const text = (selector, value) => { form.querySelector(selector).textContent = value; };
    const money = new Intl.NumberFormat("es-HN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
    function dateFromISO(value) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
        const date = new Date(`${value}T00:00:00Z`);
        return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value ? date : null;
    }
    const displayDate = date => date.toISOString().slice(0, 10).split("-").reverse().join("/");

    function expiryPreview(paymentDate, months) {
        const currentExpiry = dateFromISO(form.dataset.currentExpiry);
        const base = currentExpiry && currentExpiry >= paymentDate ? currentExpiry : paymentDate;
        // Match sumar_meses: calendar months with the day clamped to the last
        // day of the destination month, rather than a fixed number of days.
        const target = new Date(base.getTime());
        target.setUTCDate(1);
        target.setUTCMonth(target.getUTCMonth() + months);
        if (!Number.isFinite(target.getTime()) || target.getUTCFullYear() > 9999) return null;
        const lastDay = new Date(target.getTime());
        lastDay.setUTCMonth(lastDay.getUTCMonth() + 1, 0);
        target.setUTCDate(Math.min(base.getUTCDate(), lastDay.getUTCDate()));
        return target;
    }

    function updateSummary() {
        const plan = field("plan_comercial");
        text("[data-preview-plan]", plan.value ? plan.selectedOptions[0].textContent : form.dataset.currentPlan);
        const amount = field("monto").value;
        text("[data-preview-amount]", amount !== "" && Number.isFinite(Number(amount)) ? `L. ${money.format(Number(amount))}` : "Por indicar");
        const months = Number(field("cantidad_meses").value);
        const validMonths = Number.isSafeInteger(months) && months > 0 && field("cantidad_meses").validity.valid;
        text("[data-preview-months]", validMonths ? `${months} ${months === 1 ? "mes" : "meses"}` : "Indica meses mayores que cero");
        const paymentDate = dateFromISO(field("fecha_pago").value);
        text("[data-preview-payment-date]", paymentDate ? displayDate(paymentDate) : "Por indicar");
        const method = field("metodo");
        text("[data-preview-method]", method.value ? method.selectedOptions[0].textContent : "Por seleccionar");
        const expiry = paymentDate && validMonths ? expiryPreview(paymentDate, months) : null;
        text("[data-preview-expiry]", expiry ? displayDate(expiry) : "Completa una fecha y un período válidos");
    }

    // The summary never changes payment fields or submits the form.
    form.addEventListener("input", updateSummary);
    form.addEventListener("change", updateSummary);
    form.querySelectorAll('a[href^="#"]').forEach(link => link.addEventListener("click", event => {
        const target = document.getElementById(link.getAttribute("href").slice(1));
        if (!target) return;
        event.preventDefault();
        target.focus();
    }));
    updateSummary();
    form.querySelector("[data-renewal-preview]").hidden = false;
    form.querySelector("[data-renewal-errors]")?.focus();
});
