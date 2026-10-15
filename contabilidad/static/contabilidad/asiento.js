(function () {
  "use strict";

  const MAX_LINE_CENTS = 99999999999999n;
  const integerFormatter = new Intl.NumberFormat("es-HN", { maximumFractionDigits: 0 });
  const decimalSeparator = new Intl.NumberFormat("es-HN").formatToParts(1.1)
    .find(function (part) { return part.type === "decimal"; }).value;

  function normalize(value) {
    return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/[–—-]/g, " ").replace(/\s+/g, " ").trim();
  }

  // Keep every entered cent exact, including totals larger than Number.MAX_SAFE_INTEGER.
  function parseCentavos(value) {
    const raw = String(value == null ? "" : value).trim();
    if (!raw) return 0n;
    if (!/^(?:\d+(?:\.\d{0,2})?|\.\d{1,2})$/.test(raw)) return null;
    const parts = raw.split(".");
    const cents = BigInt(parts[0] || "0") * 100n + BigInt((parts[1] || "").padEnd(2, "0"));
    return cents <= MAX_LINE_CENTS ? cents : null;
  }

  function inputAmount(cents) {
    return String(cents / 100n) + "." + String(cents % 100n).padStart(2, "0");
  }

  function formatMoney(cents) {
    const absolute = cents < 0n ? -cents : cents;
    return (cents < 0n ? "−" : "") + "L " + integerFormatter.format(absolute / 100n)
      + decimalSeparator + String(absolute % 100n).padStart(2, "0");
  }

  function initializeEntry(form) {
    if (form.dataset.entryReady === "true") return;

    const table = form.querySelector("#erpAsientoTable");
    const totalForms = form.querySelector('[name$="-TOTAL_FORMS"]');
    const maxFormsInput = form.querySelector('[name$="-MAX_NUM_FORMS"]');
    const emptyTemplate = form.querySelector("#erpAsientoEmptyRow");
    const accountData = document.getElementById("erp-cuentas-contables-data");
    if (!table || !totalForms || !emptyTemplate || !accountData) return;

    let accounts;
    try {
      accounts = JSON.parse(accountData.textContent);
      if (!Array.isArray(accounts)) return;
    } catch (_error) {
      // Native account selects and delete checkboxes remain usable without enhancement.
      return;
    }

    accounts = accounts.filter(function (account) {
      return account && account.id != null && typeof account.codigo === "string"
        && typeof account.nombre === "string";
    }).map(function (account) {
      return {
        id: String(account.id), codigo: account.codigo, nombre: account.nombre,
        label: account.codigo + " — " + account.nombre,
        query: normalize(account.codigo + " " + account.nombre),
      };
    });

    const accountsById = new Map(accounts.map(function (account) { return [account.id, account]; }));
    const addButtons = [form.querySelector("#erpAddAsientoLine"), form.querySelector("#erpAddAsientoLineBottom")]
      .filter(Boolean);
    const counterpartButton = form.querySelector("#erpCompleteCounterpart");
    const announcement = form.querySelector("[data-asiento-announcement]");
    const undoPanel = form.querySelector("[data-asiento-undo]");
    const undoButton = form.querySelector("[data-undo-remove]");
    const maxForms = Number.parseInt(maxFormsInput && maxFormsInput.value, 10) || 1000;
    const originalDisabled = new WeakMap();
    let lastRemoved = null;
    let totals = { debe: 0n, haber: 0n, difference: 0n, invalid: false };

    function announce(message) {
      if (announcement) announcement.textContent = message;
    }

    function getRows() {
      return Array.from(table.querySelectorAll(".erp-asiento-row"));
    }

    function field(row, name) {
      return row.querySelector('[name$="-' + name + '"]');
    }

    function isDeleted(row) {
      const checkbox = field(row, "DELETE");
      return Boolean(checkbox && checkbox.checked);
    }

    function amount(input) {
      if (!input) return 0n;
      if (input.validity && input.validity.badInput) return null;
      return parseCentavos(input.value);
    }

    function rowHasData(row) {
      const account = field(row, "cuenta");
      const search = row.querySelector('[data-role="cuenta-search"]');
      const detail = field(row, "detalle");
      const debe = amount(field(row, "debe"));
      const haber = amount(field(row, "haber"));
      return Boolean((account && account.value) || (search && search.value.trim())
        || (detail && detail.value.trim()) || debe !== 0n || haber !== 0n);
    }

    function isEntirelyBlank(row) {
      const savedId = field(row, "id");
      return !isDeleted(row) && !(savedId && savedId.value) && !rowHasData(row)
        && !row.querySelector(".errorlist");
    }

    function setCheck(name, state) {
      const check = form.querySelector('[data-asiento-check="' + name + '"]');
      if (check) check.dataset.state = state;
    }

    function closeResults(row) {
      const results = row.querySelector('[data-role="cuenta-results"]');
      const search = row.querySelector('[data-role="cuenta-search"]');
      if (results) {
        results.hidden = true;
        results.classList.remove("is-open");
      }
      if (search) {
        search.setAttribute("aria-expanded", "false");
        search.removeAttribute("aria-activedescendant");
      }
      row.dataset.focusedOption = "-1";
      row.classList.remove("is-account-open");
    }

    function updateTotals() {
      let debe = 0n;
      let haber = 0n;
      let invalid = false;
      let populated = 0;
      let missingAccounts = 0;
      let completeLines = 0;
      let visibleCount = 0;

      getRows().forEach(function (row) {
        const deleted = isDeleted(row);
        row.hidden = deleted;
        [field(row, "cuenta"), field(row, "detalle"), field(row, "debe"), field(row, "haber"),
          row.querySelector('[data-role="cuenta-search"]')].filter(Boolean).forEach(function (control) {
          if (!originalDisabled.has(control)) originalDisabled.set(control, Boolean(control.disabled));
          control.disabled = deleted || originalDisabled.get(control);
        });
        if (deleted) {
          closeResults(row);
          row.querySelectorAll("input").forEach(function (input) { input.setCustomValidity(""); });
          return;
        }

        visibleCount += 1;
        const number = row.querySelector("[data-line-number]");
        if (number) {
          number.textContent = String(visibleCount).padStart(2, "0");
          number.setAttribute("aria-label", "Línea " + visibleCount);
        }
        const search = row.querySelector('[data-role="cuenta-search"]');
        if (search) search.setAttribute("aria-label", "Buscar cuenta de la línea " + visibleCount);
        [["detalle", "Detalle"], ["debe", "Debe en lempiras"], ["haber", "Haber en lempiras"]]
          .forEach(function (item) {
            const input = field(row, item[0]);
            if (input) input.setAttribute("aria-label", item[1] + ", línea " + visibleCount);
          });
        const remove = row.querySelector('[data-role="remove-line"]');
        if (remove) remove.setAttribute("aria-label", "Eliminar línea " + visibleCount);

        const debeInput = field(row, "debe");
        const haberInput = field(row, "haber");
        const rowDebe = amount(debeInput);
        const rowHaber = amount(haberInput);
        const hasData = rowHasData(row);
        const account = field(row, "cuenta");
        const selectedAccount = account ? accountsById.get(account.value) : null;
        const selected = Boolean(selectedAccount);
        const selection = row.querySelector("[data-account-selection]");
        if (selection) {
          const showName = selectedAccount && selectedAccount.nombre.length > 38;
          selection.hidden = !showName;
          selection.textContent = showName ? selectedAccount.nombre : "";
        }
        if (search) {
          if (selectedAccount) search.title = selectedAccount.label;
          else search.removeAttribute("title");
        }
        const invalidAmount = rowDebe === null || rowHaber === null;
        const bothSides = !invalidAmount && rowDebe > 0n && rowHaber > 0n;
        const noAmount = !invalidAmount && rowDebe === 0n && rowHaber === 0n;
        let warning = "";

        if (hasData) {
          populated += 1;
          if (!selected) missingAccounts += 1;
          if (selected && !invalidAmount && !bothSides && !noAmount) completeLines += 1;
        }
        if (invalidAmount) {
          warning = "Usa importes positivos con un máximo de dos decimales.";
        } else if (bothSides) {
          warning = "Registra el importe en el Debe o en el Haber, en una sola columna.";
        } else if (account && account.value && !selected) {
          warning = "Selecciona una cuenta activa que permita movimientos.";
        }

        const rowWarning = row.querySelector("[data-row-warning]");
        if (rowWarning) {
          rowWarning.hidden = !warning;
          rowWarning.textContent = warning;
        }
        row.classList.toggle("has-warning", Boolean(warning));
        if (search) {
          search.setCustomValidity(hasData && !selected ? "Selecciona una cuenta de la lista para esta línea." : "");
        }
        [debeInput, haberInput].filter(Boolean).forEach(function (input) {
          const inputInvalid = amount(input) === null;
          input.setCustomValidity(bothSides ? "Usa una sola columna de importe por línea."
            : inputInvalid ? "Ingresa un importe positivo con un máximo de dos decimales." : "");
        });

        invalid = invalid || invalidAmount || bothSides || Boolean(account && account.value && !selected);
        if (rowDebe !== null) debe += rowDebe;
        if (rowHaber !== null) haber += rowHaber;
      });

      const difference = debe - haber;
      totals = { debe: debe, haber: haber, difference: difference, invalid: invalid };
      const balancedAmounts = !invalid && debe > 0n && difference === 0n;
      const ready = balancedAmounts && completeLines >= 2 && completeLines === populated;
      const state = invalid ? "invalid" : ready ? "balanced" : populated ? "pending" : "empty";

      [["erpAsientoDebe", debe], ["erpAsientoHaber", haber], ["erpAsientoDiferencia", difference < 0n ? -difference : difference]]
        .forEach(function (item) {
          const node = form.querySelector("#" + item[0]);
          if (node) node.textContent = formatMoney(item[1]);
        });
      form.querySelectorAll("[data-asiento-status]").forEach(function (node) { node.dataset.state = state; });
      form.querySelectorAll("[data-asiento-status-label]").forEach(function (node) {
        node.textContent = invalid ? "Requiere revisión" : ready ? "Asiento cuadrado"
          : !populated ? "Sin movimientos" : balancedAmounts ? "Completa los datos" : "Pendiente de cuadrar";
      });
      const status = form.querySelector("#erpAsientoEstado");
      if (status) {
        if (invalid) status.textContent = "Revisa los avisos de las líneas antes de guardar.";
        else if (ready) status.textContent = "Asiento cuadrado. Revisa los datos antes de guardar.";
        else if (!populated) status.textContent = "Agrega al menos dos líneas para empezar.";
        else if (difference !== 0n) {
          status.textContent = "Agrega " + formatMoney(difference < 0n ? -difference : difference)
            + " al " + (difference > 0n ? "Haber" : "Debe") + " para cuadrar el asiento.";
        } else if (missingAccounts) {
          status.textContent = "Los importes coinciden. Falta seleccionar " + missingAccounts
            + (missingAccounts === 1 ? " cuenta." : " cuentas.");
        } else status.textContent = "Completa al menos dos líneas con cuenta e importe.";
      }

      form.querySelectorAll("[data-asiento-line-count]").forEach(function (node) {
        node.textContent = populated + (populated === 1 ? " línea con datos" : " líneas con datos");
      });
      setCheck("lines", completeLines >= 2 ? "complete" : "pending");
      setCheck("accounts", populated && !missingAccounts ? "complete" : "pending");
      setCheck("balance", invalid ? "invalid" : balancedAmounts ? "complete" : "pending");

      if (counterpartButton) {
        const absolute = difference < 0n ? -difference : difference;
        counterpartButton.disabled = invalid || absolute === 0n || absolute > MAX_LINE_CENTS;
        counterpartButton.title = absolute > MAX_LINE_CENTS
          ? "Divide la diferencia en varias líneas: supera el importe máximo de una línea."
          : "Completa la diferencia en una línea vacía y elige su cuenta.";
      }
      addButtons.forEach(function (button) {
        button.disabled = Number.parseInt(totalForms.value, 10) >= maxForms;
      });
    }

    function focusOption(row, index) {
      const results = row.querySelector('[data-role="cuenta-results"]');
      const search = row.querySelector('[data-role="cuenta-search"]');
      const options = results ? Array.from(results.querySelectorAll('[role="option"]')) : [];
      if (!options.length) {
        row.dataset.focusedOption = "-1";
        if (search) search.removeAttribute("aria-activedescendant");
        return;
      }
      const next = (index + options.length) % options.length;
      row.dataset.focusedOption = String(next);
      options.forEach(function (option, optionIndex) {
        option.classList.toggle("is-focused", optionIndex === next);
        option.setAttribute("aria-selected", String(optionIndex === next));
      });
      if (search) search.setAttribute("aria-activedescendant", options[next].id);
      options[next].scrollIntoView({ block: "nearest" });
    }

    function chooseAccount(row, account) {
      const select = field(row, "cuenta");
      const search = row.querySelector('[data-role="cuenta-search"]');
      if (!select || !search || isDeleted(row)) return;
      select.value = account.id;
      if (select.value !== account.id) return;
      search.value = account.label;
      search.setCustomValidity("");
      closeResults(row);
      updateTotals();
      focusAmount(row);
    }

    function focusAmount(row, preferred) {
      const debe = field(row, "debe");
      const haber = field(row, "haber");
      const target = preferred || (amount(debe) === null ? debe
        : amount(haber) === null || amount(haber) > 0n ? haber : debe);
      if (!target || target.disabled) return;
      target.focus();
      if (amount(target) === 0n && typeof target.select === "function") target.select();
    }

    function skipNavigation(event) {
      return event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey
        || (typeof event.getModifierState === "function" && event.getModifierState("AltGraph"));
    }

    function focusAccount(row, message) {
      const search = row.querySelector('[data-role="cuenta-search"]');
      if (search && !search.disabled) search.focus();
      if (message) announce(message);
    }

    function continueFromAmount(row, currentInput) {
      if (isDeleted(row)) return;
      updateTotals();
      const account = field(row, "cuenta");
      if (!account || !accountsById.has(account.value)) {
        focusAccount(row, "Selecciona una cuenta de la lista antes de continuar.");
        return;
      }
      const debeInput = field(row, "debe");
      const haberInput = field(row, "haber");
      const debe = amount(debeInput);
      const haber = amount(haberInput);
      if (debe === null || haber === null) {
        focusAmount(row, debe === null ? debeInput : haberInput);
        announce("Revisa el importe: usa un valor positivo con un máximo de dos decimales.");
        return;
      }
      if (debe > 0n && haber > 0n) {
        focusAmount(row, currentInput);
        announce("Usa el Debe o el Haber en esta línea. Conservamos ambos importes para que puedas corregirlos.");
        return;
      }
      if (debe === 0n && haber === 0n) {
        focusAmount(row, currentInput);
        announce("Ingresa un importe en el Debe o en el Haber antes de continuar.");
        return;
      }
      const activeRows = getRows().filter(function (item) {
        const search = item.querySelector('[data-role="cuenta-search"]');
        const selected = field(item, "cuenta");
        return !isDeleted(item) && search && !search.disabled && selected && !selected.disabled;
      });
      const next = activeRows[activeRows.indexOf(row) + 1] || addLine();
      if (next) focusAccount(next, "Continúa con la cuenta de la siguiente línea.");
    }

    function renderResults(row, query) {
      const results = row.querySelector('[data-role="cuenta-results"]');
      const search = row.querySelector('[data-role="cuenta-search"]');
      if (!results || !search || isDeleted(row)) return;
      getRows().filter(function (other) { return other !== row; }).forEach(closeResults);
      const words = normalize(query).split(" ").filter(Boolean);
      let matches = accounts.filter(function (account) {
        return words.every(function (word) { return account.query.includes(word); });
      });
      const selected = accountsById.get(field(row, "cuenta").value);
      if (!words.length && selected) {
        matches = [selected].concat(matches.filter(function (account) { return account !== selected; }));
      }
      results.replaceChildren();
      if (!matches.length) {
        const empty = document.createElement("p");
        empty.className = "erp-account-empty";
        empty.textContent = "No encontramos cuentas activas con ese texto.";
        results.appendChild(empty);
      } else {
        matches.slice(0, 12).forEach(function (account, index) {
          const option = document.createElement("button");
          option.type = "button";
          option.className = "erp-account-option";
          option.id = results.id + "-option-" + index;
          option.setAttribute("role", "option");
          option.setAttribute("aria-selected", "false");
          option.tabIndex = -1;
          const code = document.createElement("strong");
          code.textContent = account.codigo;
          const name = document.createElement("span");
          name.textContent = account.nombre;
          option.append(code, name);
          option.addEventListener("mousedown", function (event) { event.preventDefault(); });
          option.addEventListener("click", function () { chooseAccount(row, account); });
          results.appendChild(option);
        });
        if (matches.length > 12) {
          const help = document.createElement("p");
          help.className = "erp-account-empty";
          help.textContent = "Mostrando 12 de " + matches.length + " cuentas. Escribe para afinar la búsqueda.";
          results.appendChild(help);
        }
      }
      results.hidden = false;
      results.classList.add("is-open");
      row.classList.add("is-account-open");
      search.setAttribute("aria-expanded", "true");
      focusOption(row, 0);
    }

    function addLine() {
      const index = Number.parseInt(totalForms.value, 10);
      if (!Number.isSafeInteger(index) || index < 0 || index >= maxForms) {
        announce("Se alcanzó el límite de líneas. Revisa las que ya agregaste.");
        return null;
      }
      const fragment = emptyTemplate.content.cloneNode(true);
      fragment.querySelectorAll("*").forEach(function (element) {
        Array.from(element.attributes).forEach(function (attribute) {
          if (attribute.value.includes("__prefix__")) {
            element.setAttribute(attribute.name, attribute.value.replace(/__prefix__/g, String(index)));
          }
        });
      });
      const textWalker = document.createTreeWalker(fragment, 4);
      let textNode;
      while ((textNode = textWalker.nextNode())) {
        if (textNode.nodeValue.includes("__prefix__")) {
          textNode.nodeValue = textNode.nodeValue.replace(/__prefix__/g, String(index + 1));
        }
      }
      const row = fragment.querySelector(".erp-asiento-row");
      if (!row) return null;
      table.appendChild(fragment);
      totalForms.value = String(index + 1);
      initializeRow(row);
      updateTotals();
      return row;
    }

    function initializeRow(row) {
      if (row.dataset.entryInitialized === "true") return;
      row.dataset.entryInitialized = "true";
      const account = field(row, "cuenta");
      const search = row.querySelector('[data-role="cuenta-search"]');
      const results = row.querySelector('[data-role="cuenta-results"]');
      const picker = row.querySelector("[data-account-picker], .erp-account-picker");
      const nativeAccount = row.querySelector("[data-native-account]");
      const label = row.querySelector("[data-account-label]");
      const nativeDelete = row.querySelector("[data-native-delete]");
      const remove = row.querySelector('[data-role="remove-line"]');

      if (account && search && results && picker) {
        if (nativeAccount) nativeAccount.hidden = true;
        picker.hidden = false;
        account.required = false;
        if (!search.id) search.id = account.id + "_search";
        if (!results.id) results.id = account.id + "_results";
        if (label) label.htmlFor = search.id;
        search.setAttribute("role", "combobox");
        search.setAttribute("aria-autocomplete", "list");
        search.setAttribute("aria-haspopup", "listbox");
        search.setAttribute("aria-controls", results.id);
        search.setAttribute("aria-expanded", "false");
        results.setAttribute("role", "listbox");
        results.setAttribute("aria-label", "Cuentas contables disponibles");
        results.hidden = true;

        function syncSelection() {
          const selected = accountsById.get(account.value);
          if (selected) search.value = selected.label;
          else if (account.value && account.selectedOptions && account.selectedOptions.length) {
            search.value = account.selectedOptions[0].textContent.trim();
          } else search.value = "";
        }
        syncSelection();
        account.addEventListener("change", function () { syncSelection(); updateTotals(); });
        search.addEventListener("focus", function () {
          renderResults(row, account.value ? "" : search.value);
        });
        search.addEventListener("input", function () {
          account.value = "";
          renderResults(row, search.value);
          updateTotals();
        });
        search.addEventListener("keydown", function (event) {
          if (skipNavigation(event)) return;
          const open = search.getAttribute("aria-expanded") === "true";
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) renderResults(row, account.value ? "" : search.value);
            else {
              const current = Number.parseInt(row.dataset.focusedOption || "-1", 10);
              focusOption(row, current + (event.key === "ArrowDown" ? 1 : -1));
            }
          } else if (event.key === "Enter") {
            event.preventDefault();
            if (event.repeat) return;
            if (open) {
              const options = results.querySelectorAll('[role="option"]');
              const index = Number.parseInt(row.dataset.focusedOption || "-1", 10);
              if (options[index]) options[index].click();
              else announce("No hay coincidencias. Escribe otro código o nombre para buscar la cuenta.");
            } else if (accountsById.has(account.value)) focusAmount(row);
            else renderResults(row, search.value);
          } else if (event.key === "Escape") {
            if (open) event.preventDefault();
            closeResults(row);
          } else if (event.key === "Tab") closeResults(row);
        });
        picker.addEventListener("focusout", function (event) {
          if (!picker.contains(event.relatedTarget)) closeResults(row);
        });
      }

      [field(row, "debe"), field(row, "haber")].filter(Boolean).forEach(function (input) {
        input.setAttribute("step", "0.01");
        input.setAttribute("min", "0");
        input.setAttribute("inputmode", "decimal");
        input.addEventListener("input", updateTotals);
        input.addEventListener("keydown", function (event) {
          if (skipNavigation(event)) return;
          if (event.key !== "Enter") return;
          event.preventDefault();
          if (!event.repeat) continueFromAmount(row, input);
        });
      });
      const detail = field(row, "detalle");
      if (detail) {
        detail.addEventListener("input", updateTotals);
        detail.addEventListener("keydown", function (event) {
          if (skipNavigation(event)) return;
          if (event.key !== "Enter" || detail.tagName === "TEXTAREA") return;
          event.preventDefault();
          if (event.repeat) return;
          if (account && accountsById.has(account.value)) focusAmount(row);
          else focusAccount(row, "Selecciona la cuenta de esta línea para continuar con el importe.");
        });
      }
      const deleteField = field(row, "DELETE");
      if (deleteField) deleteField.addEventListener("change", updateTotals);
      if (remove && deleteField) {
        remove.hidden = false;
        deleteField.hidden = true;
        if (nativeDelete) nativeDelete.hidden = true;
        remove.addEventListener("click", function () {
          deleteField.checked = true;
          lastRemoved = row;
          if (undoPanel && undoButton) undoPanel.hidden = false;
          updateTotals();
          announce("Línea eliminada. Puedes deshacer esta acción antes de guardar.");
          const remaining = getRows().filter(function (item) { return !isDeleted(item); });
          const nextFocus = remaining.length ? remaining[remaining.length - 1]
            .querySelector('[data-role="cuenta-search"]') : addButtons[0];
          if (nextFocus) nextFocus.focus();
        });
      }
    }

    addButtons.forEach(function (button) {
      button.hidden = false;
      button.addEventListener("click", function () {
        const row = addLine();
        if (!row) return;
        announce("Nueva línea agregada. Selecciona su cuenta contable.");
        const search = row.querySelector('[data-role="cuenta-search"]');
        if (search) search.focus();
      });
    });

    if (counterpartButton) {
      counterpartButton.hidden = false;
      counterpartButton.addEventListener("click", function () {
        updateTotals();
        const difference = totals.difference;
        if (totals.invalid || difference === 0n) return;
        const absolute = difference < 0n ? -difference : difference;
        if (absolute > MAX_LINE_CENTS) {
          announce("Divide la diferencia en varias líneas porque supera el importe máximo por línea.");
          return;
        }
        const row = getRows().find(isEntirelyBlank) || addLine();
        if (!row) return;
        const target = field(row, difference > 0n ? "haber" : "debe");
        if (!target) return;
        target.value = inputAmount(absolute);
        updateTotals();
        announce("Se agregó " + formatMoney(absolute) + " al " + (difference > 0n ? "Haber" : "Debe")
          + ". Selecciona la cuenta de la contrapartida.");
        const search = row.querySelector('[data-role="cuenta-search"]');
        if (search) search.focus();
      });
    }

    if (undoButton) undoButton.addEventListener("click", function () {
      if (!lastRemoved) return;
      const row = lastRemoved;
      const checkbox = field(row, "DELETE");
      if (checkbox) checkbox.checked = false;
      lastRemoved = null;
      if (undoPanel) undoPanel.hidden = true;
      updateTotals();
      announce("Se restauró la última línea eliminada con sus datos.");
      const search = row.querySelector('[data-role="cuenta-search"]');
      if (search) search.focus();
    });

    document.addEventListener("pointerdown", function (event) {
      getRows().forEach(function (row) {
        const picker = row.querySelector("[data-account-picker], .erp-account-picker");
        if (!picker || !picker.contains(event.target)) closeResults(row);
      });
    });
    form.addEventListener("keydown", function (event) {
      if (event.defaultPrevented || event.isComposing) return;
      const key = String(event.key || "").toLowerCase();
      const altGraph = typeof event.getModifierState === "function" && event.getModifierState("AltGraph");
      if (!altGraph && event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey && key !== "enter") {
        if (key === "n") {
          event.preventDefault();
          if (!event.repeat && addButtons[0]) addButtons[0].click();
        } else if (key === "c") {
          event.preventDefault();
          if (event.repeat) return;
          if (counterpartButton && !counterpartButton.disabled) counterpartButton.click();
          else announce(totals.invalid ? "Corrige los avisos antes de completar la contrapartida."
            : "No hay una diferencia disponible para completar en una contrapartida.");
        }
        return;
      }
      if (key !== "enter") return;
      const target = event.target;
      if (!target || !["INPUT", "SELECT"].includes(target.tagName)) return;
      if (["button", "submit", "reset", "image"].includes(String(target.type).toLowerCase())) return;
      event.preventDefault();
      if (!event.repeat) announce("Usa Tab para continuar. Al terminar, guarda con los botones del asiento.");
    });
    form.addEventListener("submit", function () { getRows().forEach(closeResults); });
    getRows().forEach(initializeRow);
    form.dataset.entryReady = "true";
    form.querySelectorAll("[data-keyboard-guide]").forEach(function (guide) { guide.hidden = false; });
    addButtons.forEach(function (button) { button.setAttribute("aria-keyshortcuts", "Alt+n"); });
    if (counterpartButton) counterpartButton.setAttribute("aria-keyshortcuts", "Alt+c");
    updateTotals();
  }

  function initialize() {
    document.querySelectorAll("[data-asiento-form]").forEach(initializeEntry);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else initialize();
})();
