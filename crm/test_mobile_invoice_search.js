/* Run: node crm/test_mobile_invoice_search.js. No database, browser or network. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "static/crm/mobile_invoice_search.js"), "utf8");

function fixture(names, {panel = true, complete = true} = {}) {
  const handlers = {};
  const input = {value: "", addEventListener: (name, callback) => {
    (handlers[name] ||= []).push(callback);
  }};
  const count = {textContent: ""};
  const empty = {hidden: true};
  const search = {hidden: true, dataset: {}, querySelector: selector =>
    selector === "#mobileInvoiceSearch" ? input : selector === "#mobileInvoiceSearchCount" ? count
      : selector === "[data-invoice-search-empty]" && complete ? empty : null};
  const rows = names.map(name => ({
    hidden: false, pdfHref: "/protected/pdf/", detailHref: "/protected/detail/?app=1",
    querySelector: () => ({textContent: name}),
  }));
  const root = {querySelector: () => search, querySelectorAll: () => rows};
  // Intentionally no transport, URL/history, storage, forms, permissions or money.
  const context = vm.createContext({document: {getElementById: () => panel ? root : null}});
  const run = () => vm.runInContext(source, context);
  run();
  return {input, count, empty, search, rows, handlers, run,
    type(value) { input.value = value; (handlers.input || []).forEach(callback => callback()); },
    visible() { return rows.flatMap((row, index) => row.hidden ? [] : [names[index]]); },
  };
}

const names = ["José María González", "Ana López", "José Ana", "<img src=x onerror=alert(1)>"];
test("initializes with all authorized rows and a count", () => {
  const app = fixture(names);
  assert.equal(app.search.hidden, false);
  assert.equal(app.count.textContent, "4 facturas en esta empresa");
  assert.deepEqual(app.visible(), names);
});
test("ignores name accents and case", () => {
  const app = fixture(names); app.type("JOSE GONZALEZ");
  assert.deepEqual(app.visible(), [names[0]]);
  assert.equal(app.count.textContent, "1 de 4 facturas");
});
test("matches multiple terms in a different order", () => {
  const app = fixture(names); app.type("gonzalez   maria");
  assert.deepEqual(app.visible(), [names[0]]);
});
test("accepts surrounding whitespace and keeps every match", () => {
  const app = fixture(names); app.type("  AnA  ");
  assert.deepEqual(app.visible(), [names[1], names[2]]);
});
test("shows a no-match message only after an unmatched query", () => {
  const app = fixture(names); app.type("no existe");
  assert.equal(app.empty.hidden, false);
  assert.deepEqual(app.visible(), []);
});
test("clearing the query restores every row", () => {
  const app = fixture(names); app.type("no existe"); app.type(" \t ");
  assert.equal(app.empty.hidden, true);
  assert.deepEqual(app.visible(), names);
});
test("treats markup-looking customer names as plain text", () => {
  const app = fixture(names); app.type("<img");
  assert.deepEqual(app.visible(), [names[3]]);
});
test("does not change PDF or detail destinations", () => {
  const app = fixture(names); app.type("ana"); app.type("");
  assert.ok(app.rows.every(row => row.pdfHref === "/protected/pdf/" && row.detailHref === "/protected/detail/?app=1"));
});
test("installs its handlers once", () => {
  const app = fixture(names); app.run();
  assert.equal(app.handlers.input.length, 1);
  assert.equal(app.handlers.search.length, 1);
});
test("does not duplicate the original empty-company message", () => {
  const app = fixture([]); app.type("ana");
  assert.equal(app.empty.hidden, true);
  assert.equal(app.count.textContent, "0 de 0 facturas");
});
test("is inert without the authorized invoice panel", () => {
  const app = fixture(names, {panel: false});
  assert.equal(app.search.hidden, true);
  assert.deepEqual(app.handlers, {});
});
test("leaves the invoice list usable if search markup is incomplete", () => {
  const app = fixture(names, {complete: false});
  assert.equal(app.search.hidden, true);
  assert.deepEqual(app.visible(), names);
  assert.deepEqual(app.handlers, {});
});
