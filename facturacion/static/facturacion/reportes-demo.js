document.addEventListener("DOMContentLoaded", function () {
    const assistant = document.querySelector("[data-assistant-toggle]");
    const assistantSlot = document.querySelector("[data-report-assistant-slot]");
    if (assistant && assistantSlot) {
        assistantSlot.appendChild(assistant);
        assistant.setAttribute("aria-label", "Abrir asistente Onix");
        assistant.title = "Asistente Onix";
        const icon = document.createElement("i");
        icon.dataset.lucide = "bot";
        icon.setAttribute("aria-hidden", "true");
        assistant.appendChild(icon);
    }
    if (window.lucide) window.lucide.createIcons();
    const form = document.getElementById("report-filters");
    if (!form) return;
    const pending = document.querySelector("[data-report-pending]");
    const exports = Array.from(document.querySelectorAll("[data-report-export]"));
    const initial = new URLSearchParams(new FormData(form)).toString();

    function markChanges() {
        const changed = new URLSearchParams(new FormData(form)).toString() !== initial;
        pending.hidden = !changed;
        exports.forEach(link => link.setAttribute("aria-disabled", String(changed)));
    }
    form.addEventListener("input", markChanges);
    form.addEventListener("change", markChanges);
    exports.forEach(link => link.addEventListener("click", event => {
        if (link.getAttribute("aria-disabled") === "true") {
            event.preventDefault();
            form.querySelector("button[type=submit]").focus();
        }
    }));

    const period = document.querySelector("[data-report-period]");
    const start = form.elements.fecha_desde;
    const end = form.elements.fecha_hasta;
    const [year, month, day] = form.dataset.today.split("-").map(Number);
    const today = new Date(year, month - 1, day);
    function iso(date) {
        return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-");
    }
    function range(value) {
        if (value === "all") return ["", ""];
        if (value === "month") return [iso(new Date(year, month - 1, 1)), iso(today)];
        if (value === "previous") return [iso(new Date(year, month - 2, 1)), iso(new Date(year, month - 1, 0))];
        if (value === "quarter") return [iso(new Date(year, Math.floor((month - 1) / 3) * 3, 1)), iso(today)];
        if (value === "year") return [iso(new Date(year, 0, 1)), iso(today)];
        return null;
    }
    function matchPeriod() {
        period.value = ["all", "month", "previous", "quarter", "year"].find(value => {
            const [a, b] = range(value);
            return a === start.value && b === end.value;
        }) || "custom";
    }
    matchPeriod();
    period.addEventListener("change", () => {
        const dates = range(period.value);
        if (dates) [start.value, end.value] = dates;
        markChanges();
    });
    [start, end].forEach(input => input.addEventListener("change", matchPeriod));

    document.querySelectorAll("[data-report-detail]").forEach(button => {
        button.addEventListener("click", () => {
            const row = document.getElementById(button.dataset.reportDetail);
            row.hidden = !row.hidden;
            button.setAttribute("aria-expanded", String(!row.hidden));
        });
    });
    const menu = document.querySelector("[data-export-menu]");
    document.addEventListener("click", event => { if (menu && !menu.contains(event.target)) menu.open = false; });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && menu && menu.open) {
            menu.open = false;
            menu.querySelector("summary").focus();
        }
    });
    const order = document.getElementById("report-order");
    if (order) order.addEventListener("change", markChanges);
    document.querySelector("[data-report-print]").addEventListener("click", () => window.print());
});
