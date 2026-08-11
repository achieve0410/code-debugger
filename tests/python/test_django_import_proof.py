from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kg_debugger.adapters.django.analyzer import analyze_django


class DjangoImportProofTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_external_modules_do_not_emit_local_import_root_warning(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "manage.py", "")
            self.write(root, "app/__init__.py", "")
            self.write(
                root,
                "app/views.py",
                "import hashlib\n"
                "from rest_framework.response import Response\n"
                "def detail(request):\n"
                "    hashlib.sha256()\n"
                "    return Response()\n",
            )
            self.write(
                root,
                "app/urls.py",
                "from django.urls import path\n"
                "from .views import detail\n"
                "urlpatterns = [path('items/', detail)]\n",
            )
            self.write(root, "config/__init__.py", "")
            self.write(
                root,
                "config/urls.py",
                "from django.urls import path\n"
                "from django.views.generic import RedirectView\n"
                "urlpatterns = [path('redirect/', RedirectView.as_view())]\n",
            )

            fragment = analyze_django(root, "backend")

            paths = {route["path"] for route in fragment["routes"]}
            self.assertEqual(paths, {"/items/", "/redirect/"})
            self.assertTrue(
                any(node["kind"] == "django_view" for node in fragment["nodes"])
            )
            self.assertTrue(
                any(
                    node["kind"] == "unresolved_target"
                    for node in fragment["nodes"]
                )
            )
            self.assertFalse(
                any(
                    diagnostic["code"] == "python_import_module_unresolved"
                    for diagnostic in fragment["diagnostics"]
                )
            )


if __name__ == "__main__":
    unittest.main()
