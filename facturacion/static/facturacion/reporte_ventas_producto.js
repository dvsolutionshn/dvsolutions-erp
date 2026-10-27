(function () {
  "use strict";
  const root = document.querySelector("[data-product-search]");
  if (!root) return;
  const input = root.querySelector("[role=combobox]");
  const value = root.querySelector('input[name="producto"]');
  const toggle = root.querySelector(".rvp-product-toggle");
  const popup = root.querySelector(".rvp-product-popup");
  const list = root.querySelector("[role=listbox]");
  const status = root.querySelector(".rvp-product-status");
  const more = root.querySelector(".rvp-load-more");
  if (!input || !value || !root.dataset.searchUrl) return;
  let timer, controller, active = -1, nextPage = null, query = "", sequence = 0;
  let selectedText = value.value ? input.value : "";
  function close() {
    popup.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }
  function open() {
    popup.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  function options() { return Array.from(list.querySelectorAll('[role="option"]')); }
  function activate(index) {
    const items = options();
    if (!items.length) return;
    active = Math.max(0, Math.min(index, items.length - 1));
    items.forEach((item, i) => item.setAttribute("aria-selected", String(i === active)));
    input.setAttribute("aria-activedescendant", items[active].id);
    items[active].scrollIntoView({ block: "nearest" });
  }
  function choose(item) {
    value.value = String(item.id);
    selectedText = item.text || item.nombre || "";
    input.value = selectedText;
    close();
    input.focus();
  }
  function addOption(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rvp-product-option";
    button.id = "rvp-product-option-" + options().length;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", "false");
    button.tabIndex = -1;
    const title = document.createElement("strong");
    title.textContent = item.nombre || item.text || "Producto";
    const detail = document.createElement("small");
    const codigo = String(item.codigo || "").trim();
    const referencia = String(item.referencia || "").trim();
    detail.textContent = [codigo && ("Código: " + codigo), referencia && referencia !== codigo && ("Referencia: " + referencia), item.activo === false && "Inactivo"].filter(Boolean).join(" · ");
    button.append(title);
    if (detail.textContent) button.append(detail);
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", () => choose(item));
    list.append(button);
  }
  async function search(page, append) {
    if (controller) controller.abort();
    controller = new AbortController();
    const current = ++sequence;
    if (!append) {
      query = input.value === selectedText && value.value ? "" : input.value.trim();
      list.replaceChildren();
      active = -1;
      input.removeAttribute("aria-activedescendant");
      nextPage = null;
    }
    open();
    more.hidden = true;
    more.disabled = true;
    status.textContent = "Buscando productos…";
    list.setAttribute("aria-busy", "true");
    const url = new URL(root.dataset.searchUrl, window.location.href);
    url.searchParams.set("q", query);
    url.searchParams.set("pagina", page);
    try {
      const response = await fetch(url, { signal: controller.signal, headers: { "Accept": "application/json" }, credentials: "same-origin" });
      if (!response.ok) throw new Error("search failed");
      const data = await response.json();
      if (current !== sequence) return;
      (data.results || []).forEach(addOption);
      nextPage = data.has_more ? (data.next_page || Number(page) + 1) : null;
      more.hidden = !nextPage;
      more.disabled = false;
      status.textContent = options().length ? "Selecciona un producto de la lista." : "No se encontraron productos. Prueba otro nombre, código o referencia.";
    } catch (error) {
      if (error.name === "AbortError" || current !== sequence) return;
      status.textContent = "No se pudo cargar la lista. Vuelve a escribir para intentarlo de nuevo.";
      more.disabled = false;
    } finally {
      if (current === sequence) list.setAttribute("aria-busy", "false");
    }
  }
  input.addEventListener("input", () => {
    value.value = "";
    selectedText = "";
    clearTimeout(timer);
    if (controller) controller.abort();
    sequence++;
    list.replaceChildren();
    more.hidden = true;
    status.textContent = "Buscando productos…";
    open();
    timer = setTimeout(() => search(1, false), 220);
  });
  input.addEventListener("focus", () => {
    if (popup.hidden) { clearTimeout(timer); search(1, false); }
  });
  input.addEventListener("click", () => {
    if (popup.hidden) { clearTimeout(timer); search(1, false); }
  });
  toggle.addEventListener("click", () => {
    if (!popup.hidden) close();
    else { input.focus(); if (popup.hidden) search(1, false); }
  });
  more.addEventListener("click", () => { if (nextPage) search(nextPage, true); });
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") { clearTimeout(timer); close(); return; }
    if (event.key === "Tab") { close(); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (popup.hidden) { search(1, false); return; }
      activate(event.key === "ArrowDown" ? active + 1 : active < 0 ? options().length - 1 : active - 1);
    }
    if (event.key === "Enter" && !popup.hidden && active >= 0) {
      event.preventDefault();
      const item = options()[active];
      if (item) item.click();
    }
  });
  document.addEventListener("click", event => { if (!root.contains(event.target)) close(); });
  root.addEventListener("focusout", event => { if (!root.contains(event.relatedTarget)) close(); });
  root.closest("form").addEventListener("submit", event => {
    if (!value.value) {
      event.preventDefault();
      input.focus();
      open();
      status.textContent = "Selecciona un producto de la lista antes de consultar.";
    }
  });
})();
