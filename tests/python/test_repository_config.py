from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kg_debugger.config import DebuggerConfig
from kg_debugger.security import SecurityError


class RepositoryConfigTests(unittest.TestCase):
    def test_named_descriptors_are_namespace_sorted_with_safe_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "frontend").mkdir()
            (workspace / "backend").mkdir()
            config = DebuggerConfig.from_dict(
                workspace,
                {"project": "demo", "repositories": [
                    {"namespace": "frontend", "path": "frontend"},
                    {"namespace": "backend", "path": "backend"},
                ]},
            )
            self.assertEqual([root.namespace for root in config.repositories], ["backend", "frontend"])
            self.assertEqual(config.repository_manifest, [{"namespace": "backend"}, {"namespace": "frontend"}])
            self.assertEqual(config.repoRoots, ("backend", "frontend"))

    def test_bare_paths_use_unique_basenames_and_explicit_names_resolve_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "one" / "api").mkdir(parents=True)
            (workspace / "two" / "API").mkdir(parents=True)
            with self.assertRaises(SecurityError):
                DebuggerConfig.from_dict(workspace, {"repoRoots": ["one/api", "two/API"]})
            config = DebuggerConfig.from_dict(
                workspace,
                {"repoRoots": ["first=one/api", "second=two/API"]},
            )
            self.assertEqual([root.namespace for root in config.repositories], ["first", "second"])

    def test_rejects_namespace_and_path_boundary_violations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "repo").mkdir()
            for namespace in ("", ".", "..", "naïve", "Uppercase", "a" * 65, "bad/name"):
                with self.subTest(namespace=namespace), self.assertRaises(SecurityError):
                    DebuggerConfig.from_dict(workspace, {"repositories": [{"namespace": namespace, "path": "repo"}]})
            for path in ("", "../repo", "%2e%2e/repo", "repo%2fchild", r"repo\\child", "/"):
                with self.subTest(path=path), self.assertRaises(SecurityError):
                    DebuggerConfig.from_dict(workspace, {"repositories": [{"namespace": "repo", "path": path}]})

    def test_rejects_symlinks_credentials_and_relative_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as external_temporary:
            workspace = Path(temporary)
            target = workspace / "target"
            target.mkdir()
            link = workspace / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(SecurityError):
                DebuggerConfig.from_dict(workspace, {"repoRoots": ["link"]})
            with self.assertRaises(SecurityError):
                DebuggerConfig.from_dict(workspace, {"repoRoots": ["../outside"]})
            with self.assertRaises(SecurityError):
                DebuggerConfig.from_dict(Path.home(), {"repoRoots": [str(Path.home() / ".ssh")]})

            external = Path(external_temporary).resolve()
            config = DebuggerConfig.from_dict(workspace, {"repoRoots": [f"outside={external}"]})
            self.assertEqual(config.repositories[0].display_root, "external:outside")

    def test_set_id_is_independent_of_root_order_and_namespaces_casefold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "a").mkdir()
            (workspace / "b").mkdir()
            first = DebuggerConfig.from_dict(workspace, {"project": "demo", "repoRoots": ["one=a", "two=b"]})
            second = DebuggerConfig.from_dict(workspace, {"project": "demo", "repoRoots": ["two=b", "one=a"]})
            self.assertEqual(first.repository_set_id, second.repository_set_id)
            with self.assertRaises(SecurityError):
                DebuggerConfig.from_dict(workspace, {"repoRoots": ["Repo=a", "repo=b"]})

    def test_project_ingress_is_string_only_bounded_normalized_and_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            invalid_projects: tuple[object, ...] = (
                None,
                True,
                1,
                1.0,
                [],
                {},
                "",
                "a" * 129,
                "Cafe\u0301",
                "line\nbreak",
                "tab\tname",
                "nul\x00name",
                "delete\x7fname",
                "-----BEGIN PRIVATE KEY-----",
                "Bearer abcdefgh",
                "Basic abcdefgh",
                "aaaaaaaa.aaaaaaaa.aaaaaaaa",
                "AKIA1234567890ABCDEF",
                "ghp_12345678901234567890",
                "xoxb-1234567890",
                "token=not-safe",
                "A" * 32,
            )
            for project in invalid_projects:
                with self.subTest(project=repr(project)):
                    with self.assertRaisesRegex(SecurityError, r"\Ainvalid project\Z"):
                        DebuggerConfig.from_dict(workspace, {"project": project})

            for project in (
                "Café",
                "東京",
                "project-123",
                "bearer short",
                "akia1234567890abcde",
                "ghp_short",
                "token-name",
                "A" * 31,
            ):
                with self.subTest(project=project):
                    config = DebuggerConfig.from_dict(workspace, {"project": project})
                    self.assertEqual(config.project, project)

    def test_project_defaults_to_workspace_name_without_coercion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="valid-project-") as temporary:
            workspace = Path(temporary)
            config = DebuggerConfig.from_dict(workspace, {})
            self.assertEqual(config.project, workspace.name)
    def test_public_serialization_never_discloses_resolved_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as external_temporary:
            workspace = Path(temporary)
            (workspace / "inside").mkdir()
            external = Path(external_temporary).resolve()
            config = DebuggerConfig.from_dict(
                workspace,
                {"repoRoots": ["inside", f"other={external}"]},
            )
            serialized = json.dumps(config.to_dict(), sort_keys=True)
            self.assertIn('"displayRoot": "inside"', serialized)
            self.assertIn('"displayRoot": "external:other"', serialized)
            self.assertNotIn(str(external), serialized)
            self.assertNotIn(str((workspace / "inside").resolve()), serialized)


if __name__ == "__main__":
    unittest.main()
