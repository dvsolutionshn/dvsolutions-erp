document.addEventListener("DOMContentLoaded", function () {
    if (window.lucide) window.lucide.createIcons();
    const modules = document.getElementById("demo-modules");
    const search = document.getElementById("demo-search");
    const empty = document.getElementById("demo-search-empty");
    const cards = Array.from(document.querySelectorAll("[data-demo-module]"));
    const viewButtons = Array.from(document.querySelectorAll("[data-demo-view]"));
    const preferenceKey = "dv-demo-1-dashboard-view";

    function normalize(value) {
        return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("es");
    }

    function setView(view) {
        const selected = view === "list" ? "list" : "grid";
        modules.classList.toggle("is-list", selected === "list");
        viewButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.demoView === selected)));
    }

    if (!modules || !search) return;
    try { setView(localStorage.getItem(preferenceKey)); } catch (_) { setView("grid"); }
    viewButtons.forEach(button => button.addEventListener("click", function () {
        setView(button.dataset.demoView);
        try { localStorage.setItem(preferenceKey, button.dataset.demoView); } catch (_) { /* Optional preference. */ }
    }));
    search.addEventListener("input", function () {
        const query = normalize(search.value.trim());
        cards.forEach(card => { card.hidden = !normalize(card.dataset.search).includes(query); });
        empty.hidden = !query || cards.some(card => !card.hidden);
    });
});
