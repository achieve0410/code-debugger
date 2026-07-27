from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kg_debugger.adapters.django.analyzer import analyze_django


class DjangoAnalyzerTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_static_routes_converters_and_no_runtime_effects(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "raise RuntimeError('must not execute')\n")
            self.write(root, "app/__init__.py", "")
            self.write(root, "app/models.py", "from django.db import models\nclass Book(models.Model): pass\n")
            self.write(root, "app/views.py", "from .models import Book\ndef helper(): return Book.objects.filter()\ndef detail(request, id): return helper()\n")
            self.write(root, "app/urls.py", "from django.urls import path\nfrom .views import detail\nurlpatterns = [path('books/<int:id>/<slug:slug>/', detail), path('bad/<path:item>/', detail), path('custom/<hex:token>/', detail), path(r'^regex/$', detail)]\n")
            before = list(sys.path)
            fragment = analyze_django(root, "repo")
            self.assertEqual(sys.path, before)
            patterns = [node for node in fragment["nodes"] if node["kind"] == "django_url_pattern"]
            self.assertEqual([node["metadata"]["normalizedPath"] for node in patterns], ["/books/{p0}/{p1}/", "/bad/{p0}/", "/custom/{p0}/"])
            self.assertEqual(patterns[0]["metadata"]["converters"], [{"name": "id", "kind": "int", "segmentIndex": 1}, {"name": "slug", "kind": "slug", "segmentIndex": 2}])
            self.assertEqual(patterns[1]["metadata"]["converters"][0]["kind"], "path")
            self.assertEqual(patterns[2]["metadata"]["converters"][0]["kind"], "custom")
            self.assertTrue(any(item["code"] == "unresolved_django_url" for item in fragment["diagnostics"]))
            views = [node for node in fragment["nodes"] if node["kind"] == "django_view"]
            self.assertEqual(views[0]["metadata"]["pythonQualifiedName"], "app.views.detail")

    def test_repeated_calls_emit_one_canonical_edge(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "app/__init__.py", "")
            self.write(root, "app/views.py", "def helper(): pass\ndef detail(request):\n    helper()\n    helper()\n")
            self.write(root, "app/urls.py", "from django.urls import path\nfrom .views import detail\nurlpatterns = [path('items/', detail)]\n")

            fragment = analyze_django(root, "repo")
            invokes = [edge for edge in fragment["edges"] if edge["kind"] == "invokes"]
            self.assertEqual(len(invokes), 1)

    def test_duplicate_paths_keep_the_first_django_match(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "app/__init__.py", "")
            self.write(root, "app/views.py", "def first(request): pass\ndef second(request): pass\n")
            self.write(root, "app/urls.py", "from django.urls import path\nfrom .views import first, second\nurlpatterns = [path('items/', first), path('items/', second)]\n")

            fragment = analyze_django(root, "repo")
            routes = [route for route in fragment["routes"] if route["path"] == "/items/"]
            self.assertEqual(len(routes), 1)
            first_url = next(
                node for node in fragment["nodes"]
                if node["kind"] == "django_url_pattern"
                and node["identity"].endswith("urlpatterns:0:GET")
            )
            self.assertEqual(routes[0]["key"], first_url["key"])
    def test_unproven_package_chain_does_not_create_qualified_name(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "broken/views.py", "def detail(request): pass\n")
            self.write(root, "urls.py", "from django.urls import path\nfrom broken.views import detail\nurlpatterns = [path('x/', detail)]\n")
            fragment = analyze_django(root, "repo")
            self.assertFalse(any(node["kind"] == "django_view" for node in fragment["nodes"]))
            self.assertTrue(any(node["kind"] == "unresolved_target" for node in fragment["nodes"]))
            self.assertTrue(any(item["code"] == "python_import_module_unresolved" for item in fragment["diagnostics"]))
    def test_module_import_view_helper_query_model_and_external_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "raise RuntimeError('must not execute')\n")
            self.write(root, "shop/__init__.py", "")
            self.write(root, "shop/models.py", "from django.db import models\nclass Item(models.Model): pass\n")
            self.write(root, "shop/services.py", """from .models import Item
STATUS_URL = "https://inventory.example.test/status?token=not-retained"
def rejected():
    return {"method": "GET", "url": "https://user:top-secret@invalid.example.test/private"}
def list_items():
    Item.objects.filter(active=True)
    rejected()
    return {"method": "GET", "url": STATUS_URL, "name": "not-retained"}
""")
            self.write(root, "shop/views.py", "from .services import list_items as helper\ndef item_list(request): return helper()\n")
            self.write(root, "shop/urls.py", "from django.urls import path\nfrom . import views as routed\nurlpatterns = [path('items/', routed.item_list)]\n")
            fragment = analyze_django(root, "repo")
            nodes = {node["key"]: node for node in fragment["nodes"]}
            url = next(node for node in nodes.values() if node["kind"] == "django_url_pattern")
            view = next(node for node in nodes.values() if node["kind"] == "django_view")
            helper = next(node for node in nodes.values() if node["kind"] == "function" and node["label"] == "list_items")
            query = next(node for node in nodes.values() if node["kind"] == "query_boundary")
            model = next(node for node in nodes.values() if node["kind"] == "model")
            external = next(node for node in nodes.values() if node["kind"] == "external_service")
            pairs = {(edge["source"], edge["target"], edge["kind"]) for edge in fragment["edges"]}
            self.assertIn((url["key"], view["key"], "resolves_to"), pairs)
            self.assertIn((view["key"], helper["key"], "invokes"), pairs)
            self.assertIn((helper["key"], query["key"], "accesses"), pairs)
            self.assertIn((query["key"], model["key"], "accesses"), pairs)
            self.assertIn((helper["key"], external["key"], "calls"), pairs)
            self.assertEqual(external["metadata"], {"method": "GET", "scheme": "https", "host": "inventory.example.test", "pathPresent": True, "queryFieldCount": 1, "hasSensitiveQuery": True, "boundaryOnly": True})
            self.assertNotIn("not-retained", str(fragment))
            self.assertNotIn("top-secret", str(fragment))
            self.assertNotIn("/status", str(fragment))
            self.assertTrue(any(node["kind"] == "unresolved_target" for node in nodes.values()))

    def test_url_identity_uses_urlpatterns_nested_list_index_and_ambiguous_imports_unresolve(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "app/__init__.py", "")
            self.write(root, "app/views.py", "def detail(request): pass\n")
            urls = "from django.urls import path\nfrom . import views as alias\nnoise()\nurlpatterns = [[path('items/', alias.detail)]]\n"
            self.write(root, "app/urls.py", urls)
            first = analyze_django(root, "repo")
            first_url = next(node["identity"] for node in first["nodes"] if node["kind"] == "django_url_pattern")
            self.write(root, "app/urls.py", urls.replace("noise()\n", "noise()\nother_unrelated_call()\n"))
            self.write(root, "unrelated.py", "side_effect_that_must_not_run()\n")
            second = analyze_django(root, "repo")
            second_url = next(node["identity"] for node in second["nodes"] if node["kind"] == "django_url_pattern")
            self.assertEqual(first_url, second_url)
            self.assertTrue(any(node["kind"] == "django_view" for node in second["nodes"]))

            self.write(root, "nested/manage.py", "")
            self.write(root, "nested/app/__init__.py", "")
            self.write(root, "nested/app/views.py", "def detail(request): pass\n")
            ambiguous = analyze_django(root, "repo")
            url = next(node for node in ambiguous["nodes"] if node["kind"] == "django_url_pattern")
            targets = {edge["target"] for edge in ambiguous["edges"] if edge["source"] == url["key"]}
            self.assertTrue(any(node["key"] in targets and node["kind"] == "unresolved_target" for node in ambiguous["nodes"]))
            self.assertTrue(any(item["code"] == "python_import_module_ambiguous" for item in ambiguous["diagnostics"]))
    def test_relative_initializer_imports_and_overlevel_imports_are_conservative(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "pkg/__init__.py", "from .views import detail\n")
            self.write(root, "pkg/views.py", "def detail(request): pass\n")
            self.write(root, "pkg/urls.py", "from django.urls import path\nfrom .views import detail\nurlpatterns = [path('ok/', detail)]\n")
            self.write(root, "pkg/deep/__init__.py", "")
            self.write(root, "pkg/deep/urls.py", "from django.urls import path\nfrom ...views import detail\nurlpatterns = [path('bad/', detail)]\n")
            fragment = analyze_django(root, "repo")
            patterns = [node for node in fragment["nodes"] if node["kind"] == "django_url_pattern"]
            self.assertEqual(sorted(node["metadata"]["declaredPath"] for node in patterns), ["/bad/", "/ok/"])
            resolved = [edge for edge in fragment["edges"] if edge["kind"] == "resolves_to" and edge["confidence"] == 0.9]
            self.assertEqual(len(resolved), 1)

    def test_decoy_include_nested_owner_and_non_orm_methods_do_not_fabricate_topology(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "app/__init__.py", "")
            self.write(root, "app/views.py", """class Client:
    def filter(self): pass
def detail(request):
    def hidden():
        Client().filter()
    Client().get()
    return None
""")
            self.write(root, "app/child.py", "urlpatterns = []\n")
            self.write(root, "app/urls.py", """from django.urls import path, include
from .views import detail
decoy = path('decoy/', include('app.child'))
urlpatterns = [path('ok/', detail)]
""")
            fragment = analyze_django(root, "repo")
            patterns = [node for node in fragment["nodes"] if node["kind"] == "django_url_pattern"]
            self.assertEqual([node["metadata"]["declaredPath"] for node in patterns], ["/ok/"])
            self.assertFalse(any(node["kind"] == "query_boundary" for node in fragment["nodes"]))
            self.assertFalse(any(node["kind"] == "function" and node["label"] == "hidden" for node in fragment["nodes"]))

    def test_malformed_paths_and_source_failures_have_fixed_safe_diagnostics(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "app/__init__.py", "")
            self.write(root, "app/views.py", "def detail(request): pass\n")
            self.write(root, "app/urls.py", """from django.urls import path
from .views import detail
urlpatterns = [path('a//b/', detail), path('dot/../x/', detail), path('space /', detail), path('percent%2f/', detail), path('ok/', detail)]
""")
            fragment = analyze_django(root, "repo")
            patterns = [node for node in fragment["nodes"] if node["kind"] == "django_url_pattern"]
            self.assertEqual([node["metadata"]["declaredPath"] for node in patterns], ["/ok/"])
            self.assertEqual(sum(item["code"] == "unresolved_django_url" for item in fragment["diagnostics"]), 1)

            original = Path.read_bytes
            def fail_read(path: Path, *args: object, **kwargs: object) -> bytes:
                if path.name == "broken.py":
                    raise OSError("unreadable")
                return original(path, *args, **kwargs)
            self.write(root, "broken.py", "not relevant\n")
            with patch.object(Path, "read_bytes", fail_read):
                failed = analyze_django(root, "repo")
            source_diagnostic = next(item for item in failed["diagnostics"] if item["code"] == "source_read_failed")
            self.assertEqual(source_diagnostic["source"]["path"], "broken.py")
            self.assertNotIn(str(root), str(source_diagnostic))


    def test_credential_directories_are_pruned_from_module_and_root_walks(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "app/__init__.py", "")
            self.write(root, "app/views.py", "def detail(request): return None\n")
            self.write(root, "app/urls.py", "from django.urls import path\nfrom .views import detail\nurlpatterns = [path('ok/', detail)]\n")
            for directory_name in (".aws", ".config", ".ssh"):
                self.write(root, f"nested/{directory_name}/manage.py", "")
                self.write(root, f"nested/{directory_name}/leak.py", "def leaked(): pass\n")
            fragment = analyze_django(root, "repo")
            self.assertFalse(any(any(part in node["source"]["path"].split("/") for part in {".aws", ".config", ".ssh"}) for node in fragment["nodes"]))
            self.assertEqual([node["metadata"]["declaredPath"] for node in fragment["nodes"] if node["kind"] == "django_url_pattern"], ["/ok/"])

    def test_ipv6_external_boundary_uses_bracketed_display_host(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "app/__init__.py", "")
            self.write(root, "app/views.py", """def detail(request):
    return {"method": "GET", "url": "https://[::1]:8443/private"}
""")
            self.write(root, "app/urls.py", "from django.urls import path\nfrom .views import detail\nurlpatterns = [path('ok/', detail)]\n")
            fragment = analyze_django(root, "repo")
            external = next(node for node in fragment["nodes"] if node["kind"] == "external_service")
            self.assertEqual(external["metadata"]["host"], "::1")
            self.assertEqual(external["label"], "GET https://[::1]:8443")
if __name__ == "__main__":
    unittest.main()
