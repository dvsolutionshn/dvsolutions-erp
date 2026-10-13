"""Category presentation over the unchanged, permission-derived app registry.

Fixtures are synthetic. Standalone reads are passive; the trained agenda keeps
its original initializers and is deliberately not described as a zero-write GET.
Browser category transitions are checked separately by the local UI harness.
"""

from html.parser import HTMLParser
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Empresa, EmpresaModulo, Modulo, RolSistema, Usuario, UsuarioEmpresaPermiso
from . import views
from .mobile_modules import MODULOS_OCULTOS_APP_CLINICA, construir_contexto_modulos_app


class _CategoryNode:
    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []

    def descendants(self):
        for child in self.children:
            if isinstance(child, _CategoryNode):
                yield child
                yield from child.descendants()

    def text(self):
        return "".join(child.text() if isinstance(child, _CategoryNode) else child for child in self.children)

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()


class _CategoryMarkup(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = _CategoryNode("document")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _CategoryNode(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_CategoryNode(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def nodes(self, *, attr=None, value=None, class_name=None, tag=None, within=None):
        return [node for node in (within or self.root).descendants()
                if (tag is None or node.tag == tag)
                and (class_name is None or node.has_class(class_name))
                and (attr is None or attr in node.attrs and (value is None or node.attrs[attr] == value))]


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MobileModuleCategoryTests(TestCase):
    SLUGS = ("hospital_mia", "medical_spa", "luque_aestetic", "serviciosmedicos")
    CATEGORIES = ("Atención clínica", "Operación", "Administración")
    SCRIPT = "/static/crm/mobile_modules.js"
    CSS = "/static/crm/mobile_modules.css?v=categories-20261013"

    @classmethod
    def setUpTestData(cls):
        cls.empresas = {}
        cls.modulos = {}
        for code in ("clinica_medica", "facturacion", "agenda_citas", "crm_marketing"):
            cls.modulos[code], _ = Modulo.objects.get_or_create(codigo=code, defaults={"nombre": f"Módulo sintético {code}"})
        for index, slug in enumerate(cls.SLUGS, 1):
            company = Empresa.objects.create(nombre=f"Categorías sintéticas {slug}", slug=slug,
                                             rtn=f"MODULE-CATEGORY-{index}", tipo_solucion="clinica")
            cls.empresas[slug] = company
            for module in cls.modulos.values():
                EmpresaModulo.objects.get_or_create(empresa=company, modulo=module, defaults={"activo": True})
        cls.empresa = cls.empresas[cls.SLUGS[0]]
        cls.default_role = RolSistema.objects.create(
            nombre="Categorías completo", codigo="module-categories-full", es_rol_clinico=True,
            usa_permisos_clinicos_granulares=True, puede_ver_pacientes=True, puede_ver_historia_clinica=True,
            puede_ver_terapias=True, puede_escribir_terapias=True, puede_productos=True,
            puede_inventario=True, puede_configuracion_clinica=True,
        )
        cls.actor = Usuario.objects.create_user(username="module-categories-user", password="synthetic-only",
                                               empresa=cls.empresa, rol_sistema=cls.default_role)
        cls.actor.empresas_acceso.add(*cls.empresas.values())

    def setUp(self):
        self.client.force_login(self.actor)
        self.url()
        for target in ("socket.socket.connect", "clinica.views.EmailMultiAlternatives.send",
                       "clinica.views.subir_documento_whatsapp", "clinica.views.enviar_documento_whatsapp"):
            blocker = patch(target, side_effect=AssertionError("No real transport in category tests"))
            mock = blocker.start()
            self.addCleanup(blocker.stop)
            self.addCleanup(mock.assert_not_called)

    def url(self, company=None, json=False):
        return reverse("agenda_mobile_modules" if json else "agenda_mobile_modules_screen", args=[(company or self.empresa).slug])

    def request(self, company=None, user=None):
        company = company or self.empresa
        request = RequestFactory().get(self.url(company))
        request.user = user if user is not None else Usuario.objects.get(pk=self.actor.pk)
        request.resolver_match = type("Match", (), {"kwargs": {"empresa_slug": company.slug}})()
        return request

    def registry(self, company=None, user=None):
        return construir_contexto_modulos_app(self.request(company, user), company or self.empresa)

    def role_for(self, company=None, granular=True, **flags):
        role = RolSistema.objects.create(nombre="Categorías restringido", codigo=f"module-category-{RolSistema.objects.count()}",
                                         es_rol_clinico=granular, usa_permisos_clinicos_granulares=granular, **flags)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.actor, empresa=company or self.empresa,
                                                       defaults={"rol_sistema": role, "activo": True})
        return role

    def snapshot(self):
        return {model._meta.label: list(model.objects.order_by("pk").values())
                for model in apps.get_models(include_auto_created=True)
                if model._meta.managed and not model._meta.proxy}

    def readonly(self, callback):
        before = self.snapshot()
        connection.queries_log.clear()
        with CaptureQueriesContext(connection) as queries:
            result = callback()
        self.assertEqual(self.snapshot(), before)
        for entry in queries:
            self.assertFalse(entry["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")), entry["sql"])
        return result, queries

    def render_panel(self, context=None, embedded=False):
        context = dict(context if context is not None else self.registry())
        context.update(empresa=self.empresa, modulos_standalone=not embedded)
        return render_to_string("crm/mobile_modules_panel.html", context)

    def assert_registry_groups(self, context):
        groups = context["grupos_modulos_app"]
        self.assertEqual([group["nombre"] for group in groups],
                         [name for name in self.CATEGORIES if any(item["categoria"] == name for item in context["modulos_app"])])
        flattened = []
        for group in groups:
            self.assertTrue(group["modulos"])
            self.assertEqual(group["modulos"], [item for item in context["modulos_app"] if item["categoria"] == group["nombre"]])
            flattened.extend(group["modulos"])
        self.assertEqual({item["key"]: item for item in flattened},
                         {item["key"]: item for item in context["modulos_app"]})
        self.assertEqual(len({item["key"] for item in flattened}), len(flattened))

    def assert_panel_matches(self, html, context, embedded=False):
        markup = _CategoryMarkup(html)
        roots = markup.nodes(attr="data-modules-root")
        self.assertEqual(len(roots), 1)
        root = roots[0]
        self.assertEqual(root.attrs["id"], "mas-funciones-app")
        self.assertEqual(root.attrs["data-modules-embedded"], "1" if embedded else "0")
        buttons = markup.nodes(attr="data-modules-category-target", within=root)
        groups = markup.nodes(attr="data-modules-group", within=root)
        self.assertEqual(len(buttons), len(context["grupos_modulos_app"]))
        self.assertEqual(len(groups), len(buttons))
        for button, node, group in zip(buttons, groups, context["grupos_modulos_app"]):
            target = node.attrs["id"]
            self.assertEqual(button.attrs["data-modules-category-target"], target)
            self.assertEqual(button.attrs["aria-controls"], target)
            titles = markup.nodes(attr="data-modules-group-title", within=node)
            self.assertEqual(len(titles), 1)
            self.assertEqual(titles[0].text().strip(), group["nombre"])
            cards = markup.nodes(class_name="mobile-modules-card", within=node)
            self.assertEqual(len(cards), len(group["modulos"]))
            for card, item in zip(cards, group["modulos"]):
                strong = markup.nodes(tag="strong", within=card)
                self.assertEqual(strong[0].text().strip(), item["nombre"])
                states = markup.nodes(class_name="mobile-modules-state", within=card)
                self.assertEqual(len(states), 1)
                self.assertTrue(states[0].has_class(item["estado"]))
                self.assertEqual(card.attrs.get("href", ""), item["url"])
                self.assertEqual(card.attrs.get("data-app-open", ""), item["panel"])
                self.assertEqual(card.tag, "a" if item["url"] else "article")
        return markup, root

    def test_registry_groups_derived_exactly_from_authorized_items_without_empty_groups(self):
        context, _ = self.readonly(self.registry)
        self.assert_registry_groups(context)
        self.assertEqual([group["nombre"] for group in context["grupos_modulos_app"]], list(self.CATEGORIES))

    def test_four_companies_granular_and_legacy_standalone_match_registry_exactly(self):
        for company in self.empresas.values():
            for granular in (True, False):
                flags = {"puede_ver_pacientes": True, "puede_ver_historia_clinica": True, "puede_ver_terapias": True,
                         "puede_productos": True, "puede_inventario": True, "puede_configuracion_clinica": True} if granular else {
                             "puede_pacientes": True, "puede_expediente_clinico": True, "puede_productos": True,
                             "puede_inventario": True, "puede_configuracion_clinica": True}
                self.role_for(company, granular, **flags)
                with self.subTest(company=company.slug, granular=granular):
                    context = self.registry(company)
                    self.assert_registry_groups(context)
                    response, _ = self.readonly(lambda: self.client.get(self.url(company)))
                    self.assertEqual(response.status_code, 200)
                    self.assertTemplateUsed(response, "crm/mobile_modules.html")
                    self.assert_panel_matches(response.content.decode(), context)
                    self.assertEqual(response.context["modulos_app"], context["modulos_app"])

    def test_category_controls_are_native_buttons_with_keyboard_focus_targets_and_icons(self):
        markup, root = self.assert_panel_matches(self.render_panel(), self.registry())
        for button in markup.nodes(attr="data-modules-category-target", within=root):
            self.assertEqual(button.tag, "button")
            self.assertEqual(button.attrs.get("type"), "button")
            self.assertEqual(button.attrs.get("aria-expanded"), "false")
            self.assertTrue(button.text().strip())
            icons = markup.nodes(tag="svg", within=button)
            self.assertEqual(len(icons), 1)
            self.assertEqual(icons[0].attrs.get("focusable"), "false")
            icon_wrapper = markup.nodes(class_name="mobile-modules-category-icon", within=button)
            self.assertEqual(len(icon_wrapper), 1)
            self.assertEqual(icon_wrapper[0].attrs.get("aria-hidden"), "true")
        for title in markup.nodes(attr="data-modules-group-title", within=root):
            self.assertEqual(title.attrs.get("tabindex"), "-1")
        overview_title = markup.nodes(attr="id", value="mobileModulesCategoriesTitle", within=root)
        self.assertEqual(len(overview_title), 1)
        self.assertEqual(overview_title[0].attrs.get("tabindex"), "-1")

    def test_server_markup_preserves_no_js_fallback_and_hidden_category_overview(self):
        markup, root = self.assert_panel_matches(self.render_panel(), self.registry())
        overview = markup.nodes(attr="data-modules-overview", within=root)
        self.assertEqual(len(overview), 1)
        self.assertEqual(overview[0].attrs["id"], "mobileModulesCategories")
        self.assertIn("hidden", overview[0].attrs)
        for group in markup.nodes(attr="data-modules-group", within=root):
            self.assertNotIn("hidden", group.attrs)
        navigation = markup.nodes(attr="data-modules-group-navigation", within=root)
        self.assertEqual(len(navigation), 1)
        self.assertIn("hidden", navigation[0].attrs)

    def test_local_back_control_is_not_a_route_or_global_agenda_close_action(self):
        markup, root = self.assert_panel_matches(self.render_panel(), self.registry())
        back = markup.nodes(attr="data-modules-back", within=root)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0].tag, "button")
        self.assertEqual(back[0].attrs.get("type"), "button")
        self.assertNotIn("href", back[0].attrs)
        self.assertNotIn("data-app-close", back[0].attrs)
        self.assertNotIn("onclick", back[0].attrs)

    def test_clinical_only_role_sees_only_authorized_category_no_empty_operations_admin(self):
        self.role_for(puede_ver_terapias=True)
        context = self.registry()
        self.assertEqual([group["nombre"] for group in context["grupos_modulos_app"]], ["Atención clínica"])
        self.assert_registry_groups(context)
        self.assert_panel_matches(self.render_panel(context), context)

    def test_product_only_role_sees_operation_group_without_clinical_or_administration_grants(self):
        for granular in (True, False):
            self.role_for(granular=granular, puede_productos=True)
            context = self.registry()
            self.assertEqual([group["nombre"] for group in context["grupos_modulos_app"]], ["Operación"])
            response, _ = self.readonly(lambda: self.client.get(self.url()))
            self.assertEqual(response.status_code, 200)
            self.assert_panel_matches(response.content.decode(), context)
            user = Usuario.objects.get(pk=self.actor.pk)
            self.assertFalse(user.tiene_permiso_erp("puede_ver_calendario", self.empresa))
            self.assertFalse(user.tiene_permiso_erp("puede_ver_historia_clinica", self.empresa))

    def test_configuration_only_granular_role_sees_administration_without_other_categories(self):
        self.role_for(puede_configuracion_clinica=True)
        context = self.registry()
        self.assertEqual([group["nombre"] for group in context["grupos_modulos_app"]], ["Administración"])
        self.assert_panel_matches(self.render_panel(context), context)

    def test_no_permissions_empty_registry_does_not_fabricate_category_buttons(self):
        self.role_for()
        context = self.registry()
        self.assertEqual(context["modulos_app"], [])
        self.assertEqual(context["grupos_modulos_app"], [])
        markup = _CategoryMarkup(self.render_panel(context))
        self.assertEqual(markup.nodes(attr="data-modules-category-target"), [])
        self.assertEqual(markup.nodes(attr="data-modules-group"), [])

    def test_pending_cards_stay_non_links_and_keep_original_states_descriptions(self):
        self.role_for(puede_ver_terapias=True, puede_escribir_terapias=True, puede_productos=True)
        context = self.registry()
        pending = [item for item in context["modulos_app"] if item["estado"] == "pendiente"]
        self.assertTrue(pending)
        markup, root = self.assert_panel_matches(self.render_panel(context), context)
        cards = markup.nodes(class_name="mobile-modules-pending", within=root)
        self.assertEqual(len(cards), len(pending))
        for node, item in zip(cards, pending):
            self.assertEqual(node.tag, "article")
            self.assertNotIn("href", node.attrs)
            self.assertNotIn("data-app-open", node.attrs)
            self.assertIn(item["descripcion"], node.text())

    def test_available_partial_and_remaining_destinations_match_registry_values(self):
        self.role_for(puede_ver_pacientes=True, puede_ver_historia_clinica=True, puede_ver_terapias=True,
                      puede_ver_calendario=True, puede_productos=True, puede_inventario=True)
        context = self.registry()
        self.assertTrue(any(item["estado"] == "disponible" for item in context["modulos_app"]))
        self.assertTrue(any(item["estado"] == "parcial" for item in context["modulos_app"]))
        self.assertFalse(any(item["key"] in {"pacientes", "agenda", "facturacion", "inventario"}
                             for item in context["modulos_app"]))
        self.assert_panel_matches(self.render_panel(context, embedded=True), context, embedded=True)

    def test_four_duplicate_entries_are_the_only_items_removed_for_all_companies_and_roles(self):
        duplicates = {"pacientes", "agenda", "facturacion", "inventario"}
        for company in self.empresas.values():
            for granular in (True, False):
                flags = {"puede_ver_pacientes": True, "puede_ver_calendario": True} if granular else {
                    "puede_pacientes": True, "puede_citas": True,
                }
                self.role_for(company, granular, puede_productos=True, puede_inventario=True,
                              puede_facturas=True, puede_crear_facturas=True,
                              puede_configuracion_clinica=True, **flags)
                with self.subTest(company=company.slug, granular=granular):
                    with patch("crm.mobile_modules.MODULOS_OCULTOS_APP_CLINICA",
                               MODULOS_OCULTOS_APP_CLINICA - duplicates):
                        before = self.registry(company)
                    self.assertTrue(duplicates.issubset(item["key"] for item in before["modulos_app"]))
                    after, _ = self.readonly(lambda: self.registry(company))
                    self.assertEqual(after["modulos_app"], [item for item in before["modulos_app"]
                                                            if item["key"] not in duplicates])
                    self.assert_registry_groups(after)
                    self.assertEqual(after["modulos_disponibles_app"],
                                     sum(bool(item["url"]) for item in after["modulos_app"]))
                    self.assertEqual(after["rol_app"], before["rol_app"])
                    self.assertEqual(after["puede_ver_modulos_app"], before["puede_ver_modulos_app"])

    def test_duplicates_absent_from_standalone_embedded_and_json_but_home_accesses_remain(self):
        names = {"Pacientes", "Agenda y citas", "Facturación", "Productos y existencias"}
        for company in self.empresas.values():
            with self.subTest(company=company.slug):
                request = self.request(company)
                context = self.registry(company)
                context.update(empresa=company, request=request, puede_ver_productos_app=True,
                               hospital_mia_app_premium=True, erp_access={"facturas": True})
                for template in ("crm/mobile_modules.html", "crm/agenda_mobile.html"):
                    context["modulos_standalone"] = template == "crm/mobile_modules.html"
                    html, _ = self.readonly(lambda: render_to_string(template, context))
                    markup, root = self.assert_panel_matches(html, context, embedded=template == "crm/agenda_mobile.html")
                    cards = markup.nodes(class_name="mobile-modules-card", within=root)
                    self.assertTrue(names.isdisjoint(markup.nodes(tag="strong", within=card)[0].text().strip()
                                                    for card in cards))
                    if template == "crm/agenda_mobile.html":
                        home = markup.nodes(attr="id", value="inicio")[0]
                        for panel in ("pacientes-app", "factura-app", "facturas-app", "productos-app", "agenda-app"):
                            self.assertEqual(len(markup.nodes(tag="a", attr="data-app-open", value=panel, within=home)), 1)
                            self.assertEqual(len(markup.nodes(attr="id", value=panel)), 1)
                response, _ = self.readonly(lambda: self.client.get(self.url(company, json=True)))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["modulos"], context["modulos_app"])
                self.assertTrue(names.isdisjoint(item["nombre"] for item in response.json()["modulos"]))

    def test_patient_only_role_keeps_existing_directory_navigation_without_duplicate_category(self):
        for company in self.empresas.values():
            self.role_for(company, puede_ver_pacientes=True)
            with self.subTest(company=company.slug):
                response, _ = self.readonly(lambda: self.client.get(self.url(company)))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["modulos_app"], [])
                self.assertEqual(response.context["grupos_modulos_app"], [])
                self.assertContains(response, 'No hay módulos adicionales disponibles')
                self.assertContains(response, f'href="{reverse("clinica_pacientes", args=[company.slug])}?app=1"')
                markup = _CategoryMarkup(response.content.decode())
                self.assertEqual(markup.nodes(attr="data-modules-category-target"), [])

    def test_company_role_overrides_change_groups_without_global_role_or_membership_updates(self):
        target = self.empresas["medical_spa"]
        self.role_for(target, puede_productos=True)
        before = self.snapshot()
        self.assertEqual([group["nombre"] for group in self.registry(target)["grupos_modulos_app"]], ["Operación"])
        self.assertEqual([group["nombre"] for group in self.registry()["grupos_modulos_app"]], list(self.CATEGORIES))
        self.assertEqual(self.snapshot(), before)
        UsuarioEmpresaPermiso.objects.update_or_create(usuario=self.actor, empresa=target, defaults={"rol_sistema": None})
        self.assertEqual(self.registry(target)["grupos_modulos_app"], [])

    def test_disabled_clinical_module_removes_only_clinical_permissions_derived_groups(self):
        EmpresaModulo.objects.filter(empresa=self.empresa, modulo=self.modulos["clinica_medica"]).update(activo=False)
        context = self.registry()
        self.assert_registry_groups(context)
        self.assertNotIn("Atención clínica", [group["nombre"] for group in context["grupos_modulos_app"]])
        self.assertIn("Operación", [group["nombre"] for group in context["grupos_modulos_app"]])
        self.assert_panel_matches(self.render_panel(context), context)

    def test_outside_four_wrong_profile_anonymous_and_non_member_do_not_get_category_surface(self):
        outside = Empresa.objects.create(nombre="Categorías externa", slug="module_categories_external", rtn="MODULE-CATEGORY-OUT", tipo_solucion="clinica")
        self.actor.empresas_acceso.add(outside)
        self.assertEqual(self.registry(outside)["grupos_modulos_app"], [])
        self.assertEqual(self.registry(user=AnonymousUser())["grupos_modulos_app"], [])
        company = self.empresas["medical_spa"]
        self.actor.empresas_acceso.remove(company)
        self.assertEqual(self.registry(company)["grupos_modulos_app"], [])
        Empresa.objects.filter(pk=self.empresa.pk).update(tipo_solucion="erp")
        self.empresa.refresh_from_db()
        self.assertEqual(self.registry()["grupos_modulos_app"], [])

    def test_license_filter_and_inactive_company_keep_existing_backend_denials(self):
        Empresa.objects.filter(pk=self.empresa.pk).update(estado_licencia="suspendida")
        self.empresa.refresh_from_db()
        self.assertEqual(self.registry()["grupos_modulos_app"], [])
        response = self.client.get(self.url())
        self.assertRedirects(response, reverse("empresa_login", args=[self.empresa.slug]), fetch_redirect_response=False)
        Empresa.objects.filter(pk=self.empresa.pk).update(activa=False)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_standalone_no_calendar_renders_one_script_css_and_unique_new_ids(self):
        response, _ = self.readonly(lambda: self.client.get(self.url()))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["app_home_disponible"])
        markup, root = self.assert_panel_matches(response.content.decode(), self.registry())
        scripts = markup.nodes(tag="script", attr="src", value=self.SCRIPT)
        self.assertEqual(len(scripts), 1)
        self.assertIn("defer", scripts[0].attrs)
        self.assertEqual(len(markup.nodes(tag="link", attr="href", value=self.CSS)), 1)
        ids = [node.attrs["id"] for node in root.descendants() if "id" in node.attrs]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(markup.nodes(attr="id", value="mas-funciones-app")), 1)
        self.assertEqual(markup.nodes(tag="table", within=root), [])
        self.assertNotContains(response, 'rel="manifest"')

    def test_embedded_template_has_one_panel_script_and_preserves_global_back_control(self):
        context = self.registry()
        context.update(empresa=self.empresa, request=self.request())
        # Template-only embed: this does not call the agenda's seedful handler.
        html, _ = self.readonly(lambda: render_to_string("crm/agenda_mobile.html", context))
        markup, root = self.assert_panel_matches(html, context, embedded=True)
        self.assertEqual(len(markup.nodes(tag="script", attr="src", value=self.SCRIPT)), 1)
        self.assertEqual(len(markup.nodes(tag="link", attr="href", value=self.CSS)), 1)
        self.assertEqual(len(markup.nodes(attr="data-app-close", within=root)), 1)
        self.assertEqual(len(markup.nodes(attr="data-modules-back", within=root)), 1)
        self.assertEqual(len(markup.nodes(attr="id", value="mas-funciones-app")), 1)

    def test_capability_false_omits_panel_and_conditional_assets_from_embedded_template(self):
        context = self.registry(user=AnonymousUser())
        context.update(empresa=self.empresa, request=self.request(user=AnonymousUser()))
        for template in ("crm/mobile_modules.html", "crm/agenda_mobile.html"):
            html = render_to_string(template, context)
            markup = _CategoryMarkup(html)
            self.assertEqual(markup.nodes(attr="data-modules-root"), [])
            if template == "crm/agenda_mobile.html":
                self.assertEqual(markup.nodes(tag="script", attr="src", value=self.SCRIPT), [])
                self.assertEqual(markup.nodes(tag="link", attr="href", value=self.CSS), [])
            # Standalone authorization precedes rendering. Its original CSS
            # is unconditional; loading a local script cannot authorize data.

    def test_company_role_and_module_text_autoescape_without_executable_markup(self):
        context = self.registry()
        context["rol_app"] = '<img src=x onerror="synthetic-role">'
        company_name = '<script>synthetic-company</script>'
        self.empresa.nombre = company_name
        item = dict(context["grupos_modulos_app"][0]["modulos"][0])
        item.update(nombre='<script>synthetic-module</script>', descripcion='<img src=x onerror="synthetic-description">')
        context["grupos_modulos_app"] = [{"nombre": "Atención clínica", "modulos": [item]}]
        context["modulos_app"] = [item]
        html = self.render_panel(context)
        self.assertIn("&lt;script&gt;synthetic-company&lt;/script&gt;", html)
        self.assertIn("&lt;script&gt;synthetic-module&lt;/script&gt;", html)
        self.assertNotIn(company_name, html)
        markup, root = self.assert_panel_matches(html, context)
        self.assertEqual(markup.nodes(tag="img", within=root), [])
        self.assertEqual(markup.nodes(tag="script", within=root), [])

    def test_json_and_rendered_groups_preserve_registry_keys_states_urls_panels_without_private_queries(self):
        expected = self.registry()
        response, queries = self.readonly(lambda: self.client.get(self.url(json=True)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["modulos"], expected["modulos_app"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.registry()["modulos_app"], expected["modulos_app"])
        for entry in queries:
            for table in ('"clinica_paciente"', '"clinica_historiaclinicaespecialidad"', '"facturacion_factura"'):
                self.assertNotIn(table, entry["sql"].lower())

    def test_trained_agenda_get_keeps_original_initializers_and_selected_date_not_zero_write_claim(self):
        self.role_for(puede_ver_calendario=True, puede_ver_pacientes=True, puede_ver_terapias=True)
        before = self.snapshot()
        with patch("crm.views.CitaClienteForm", wraps=views.CitaClienteForm) as form_constructor, \
                patch("crm.views._asegurar_pacientes_empresas_clinicas", wraps=views._asegurar_pacientes_empresas_clinicas) as patients_initializer, \
                patch("crm.views.asegurar_cuentas_financieras_base_honduras", wraps=views.asegurar_cuentas_financieras_base_honduras) as account_initializer:
            response = self.client.get(reverse("agenda_mobile", args=[self.empresa.slug]) + "?app=agenda&vista=dia&fecha=2026-10-13")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/agenda_mobile.html")
        form_constructor.assert_called_once()
        patients_initializer.assert_called_once_with(response.context["empresa"])
        account_initializer.assert_called_once_with(response.context["empresa"])
        self.assertEqual(response.context["fecha_seleccionada"].isoformat(), "2026-10-13")
        self.assertEqual(response.context["vista"], "dia")
        self.assert_panel_matches(response.content.decode(), self.registry(), embedded=True)
        after = self.snapshot()
        # The original form may seed professionals/services/accounts. Navigation
        # neither saves a visit nor adds a patient/history/invoice/payment.
        for label in ("clinica.Paciente", "clinica.HistoriaClinicaEspecialidad", "crm.CitaCliente", "facturacion.Factura", "facturacion.PagoFactura"):
            self.assertEqual(after[label], before[label])
