# -*- coding: utf-8 -*-
"""Tests unitaires et d'intégration pour le durcissement Codex & Sous-Agents (v6.23.10.1)."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBS_DIR = REPO_ROOT / "14-owui-libs"
TOOLS_DIR = REPO_ROOT / "12-owui-tools"
if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import echo_codex_git  # noqa: E402
import echo_constants  # noqa: E402
import echo_core  # noqa: E402
import echo_paths  # noqa: E402
import echo_state_manager  # noqa: E402
from echo_codex_git import CodexRepo  # noqa: E402


def _load_tool_module(name: str, filename: str):
    """Charge un module d'outil de 12-owui-tools de manière déterministe."""
    if name in sys.modules:
        return sys.modules[name]
    mod_path = TOOLS_DIR / filename
    spec = importlib.util.spec_from_file_location(name, str(mod_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


echo_codex_tool = _load_tool_module("echo_codex_tool", "echo_codex_tool.py")
strategic_planner = _load_tool_module("strategic_planner", "strategic_planner.py")


class TestCodexHardening(unittest.IsolatedAsyncioTestCase):
    """Couvre pagination géante, déshydratation Base64, plafond net, MAX_TOKENS et sandbox sous-agent."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = self._tmp.name
        self.users_root = os.path.join(self.tmp_root, "users")
        self.data_root = os.path.join(self.tmp_root, "data")
        os.makedirs(self.users_root, exist_ok=True)
        os.makedirs(self.data_root, exist_ok=True)

        self._patches = [
            mock.patch.object(echo_constants, "ECHO_USERS_ROOT", self.users_root),
            mock.patch.object(echo_constants, "ECHO_BASE_DATA_DIR", self.data_root),
            mock.patch.object(echo_paths, "ECHO_USERS_ROOT", self.users_root),
            mock.patch.object(echo_state_manager, "ECHO_USERS_ROOT", self.users_root),
            mock.patch.object(echo_codex_git, "ECHO_USERS_ROOT", self.users_root),
            mock.patch.object(echo_codex_tool, "ECHO_USERS_ROOT", self.users_root),
        ]
        for p in self._patches:
            p.start()

        self.uid = "user_test"
        self.cid = "chat_test"
        self.user = {"id": self.uid}
        self.meta = {"chat_id": self.cid}

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _get_repo(self, workspace: str = "main") -> CodexRepo:
        return CodexRepo(self.uid, self.cid, workspace)

    def test_paginate_giant_single_line_utf8(self) -> None:
        """Une ligne unique > 46080 octets est tronquée en UTF-8 Head+Tail et la pagination avance."""
        giant_payload = "é" * 5000 + "A" * 1213312
        lines = [f"ligne_{i}" for i in range(1, 306)] + [giant_payload] + [
            f"ligne_{i}" for i in range(307, 312)
        ]
        self.assertEqual(len(lines), 311)

        p1 = echo_core.paginate_lines_window(lines, start_line=1)
        self.assertEqual(p1["end_line"], 305)
        self.assertTrue(p1["has_more"])
        self.assertEqual(p1["next_start_line"], 306)
        self.assertEqual(p1["truncated_lines"], [])

        p2 = echo_core.paginate_lines_window(lines, start_line=306)
        self.assertEqual(p2["start_line"], 306)
        self.assertEqual(p2["end_line"], 306)
        self.assertEqual(p2["lines_returned"], 1)
        self.assertEqual(p2["truncated_lines"], [306])
        self.assertTrue(p2["has_more"])
        self.assertEqual(p2["next_start_line"], 307)
        entry_utf8 = p2["selected_lines"][0].encode("utf-8")
        self.assertLessEqual(len(entry_utf8), echo_constants.ECHO_CODEX_MAX_READ_BYTES)
        self.assertIn("TRONQUÉE", p2["selected_lines"][0])

        p3 = echo_core.paginate_lines_window(lines, start_line=307)
        self.assertEqual(p3["start_line"], 307)
        self.assertEqual(p3["end_line"], 311)
        self.assertEqual(p3["truncated_lines"], [])
        self.assertFalse(p3["has_more"])

    def test_dehydrate_and_rehydrate_base64_blobs(self) -> None:
        """Masquage bijectif d'un Data URI Base64 de 1,23 Mo et détection de jeton altéré."""
        blob = "ABCD" * 310000  # 1,24 Mo
        html_doc = f'<html><body><h1>Titre</h1><img src="data:image/jpeg;base64,{blob}"></body></html>'

        masked, slots = echo_core.dehydrate_inline_base64_blobs(html_doc)
        self.assertEqual(len(slots), 1)
        self.assertLess(len(masked), 10000)
        self.assertIn("__ECHO_B64_SLOT_0_", masked)

        restored, dangling = echo_core.rehydrate_inline_base64_blobs(masked, slots)
        self.assertEqual(dangling, [])
        self.assertEqual(restored, html_doc)

        corrupted_doc = masked.replace("__ECHO_B64_SLOT_0_", "__ECHO_B64_SLOT_9_")
        _, dangling_bad = echo_core.rehydrate_inline_base64_blobs(corrupted_doc, slots)
        self.assertTrue(len(dangling_bad) >= 1)

    async def test_edit_codex_assisted_masks_base64_and_preserves_blob(self) -> None:
        """edit_codex(instructions=...) masque le Base64 dans le prompt et le réhydrate sans perte."""
        blob = "EFGH" * 310000  # 1,24 Mo
        html_doc = f'<html><body><h1>Ancien</h1><img src="data:image/jpeg;base64,{blob}"></body></html>'
        repo = self._get_repo("main")
        repo.commit_file("rapport.html", html_doc, "Init html")

        captured_prompts = []

        async def _fake_call_cascade(**kwargs):
            payload = kwargs["payload"]
            user_txt = payload["contents"][0]["parts"][0]["text"]
            captured_prompts.append(user_txt)
            # Récupère le document masqué et modifie seulement le titre
            doc_block = user_txt.split("```html\n", 1)[1].split("\n```\n\n## Instructions", 1)[0]
            edited = doc_block.replace("<h1>Ancien</h1>", "<h1>Nouveau</h1>")
            return (
                {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": edited}]},
                        }
                    ]
                },
                "MODEL_APOLLO",
                "ok",
            )

        tools = echo_codex_tool.Tools()
        with mock.patch.object(
            echo_codex_tool.EchoGeminiClient, "call_cascade", side_effect=_fake_call_cascade
        ):
            res = await tools.edit_codex(
                filename="rapport.html",
                instructions="Remplacer Ancien par Nouveau",
                workspace="main",
                __user__=self.user,
                __metadata__=self.meta,
            )

        self.assertEqual(res["status"]["status"], "success")
        self.assertEqual(len(captured_prompts), 1)
        self.assertLess(len(captured_prompts[0]), 15000)
        self.assertNotIn(blob[:64], captured_prompts[0])

        saved = repo.read_file("rapport.html")["content"]
        self.assertEqual(saved, html_doc.replace("<h1>Ancien</h1>", "<h1>Nouveau</h1>"))

    async def test_edit_codex_assisted_rejects_oversized_net_text_preflight(self) -> None:
        """Un fichier texte pur > ECHO_CODEX_ASSISTED_EDIT_MAX_CHARS est refusé avant appel LLM."""
        big_text = "x" * 150000
        repo = self._get_repo("main")
        repo.commit_file("big.txt", big_text, "Init big")

        tools = echo_codex_tool.Tools()
        with mock.patch.object(echo_codex_tool.EchoGeminiClient, "call_cascade") as m_call:
            res = await tools.edit_codex(
                filename="big.txt",
                instructions="Ajouter une ligne",
                workspace="main",
                __user__=self.user,
                __metadata__=self.meta,
            )

        m_call.assert_not_called()
        self.assertEqual(res["status"]["status"], "error")
        self.assertEqual(res["status"]["error_code"], "file_too_large_for_assisted_edit")

    async def test_max_tokens_aborts_without_commit_in_codex_and_planner(self) -> None:
        """Une sortie MAX_TOKENS annule l'écriture sans commit dans edit_codex et update_plan."""
        codex_repo = self._get_repo("main")
        orig_py = "def hello():\n    return 'v1'\n"
        codex_repo.commit_file("app.py", orig_py, "Init py")

        plans_repo = self._get_repo("plans")
        plan_id = "plan-1700000000-abcd"
        plan_fname = f"{plan_id}_demo.md"
        orig_plan = f"---\nplan_id: {plan_id}\nstatus: proposed\n---\n\n# Plan Initial\n"
        plans_repo.commit_file(plan_fname, orig_plan, "Init plan")

        async def _truncated_cascade(**kwargs):
            return (
                {
                    "candidates": [
                        {
                            "finishReason": "MAX_TOKENS",
                            "content": {"parts": [{"text": "PARTIAL_TRUNCATED_OUTPUT"}]},
                        }
                    ]
                },
                "MODEL_APOLLO",
                "max_tokens",
            )

        codex_tools = echo_codex_tool.Tools()
        planner_tools = strategic_planner.Tools()

        with mock.patch.object(
            echo_codex_tool.EchoGeminiClient, "call_cascade", side_effect=_truncated_cascade
        ):
            res_codex = await codex_tools.edit_codex(
                filename="app.py",
                instructions="Modifier hello",
                workspace="main",
                __user__=self.user,
                __metadata__=self.meta,
            )

        self.assertEqual(res_codex["status"]["error_code"], "output_truncated_max_tokens")
        self.assertEqual(codex_repo.read_file("app.py")["content"], orig_py)

        with mock.patch.object(
            strategic_planner.EchoGeminiClient, "call_cascade", side_effect=_truncated_cascade
        ):
            res_plan = await planner_tools.update_plan(
                plan_id=plan_id,
                instructions="Ajouter une phase",
                __user__=self.user,
                __metadata__=self.meta,
            )

        self.assertEqual(res_plan["status"]["error_code"], "output_truncated_max_tokens")
        self.assertEqual(plans_repo.read_file(plan_fname)["content"], orig_plan)

    async def test_subagent_sandbox_read_edit_root_vs_create_isolation(self) -> None:
        """Sous-agent : read/edit/delete voient la racine sandbox si absent de <sub_sid>/, create isole sous <sub_sid>/."""
        sandbox_repo = self._get_repo("sandbox")
        sandbox_repo.commit_file("build_reco_dossier.py", "print('root_v1')\n", "Init root")
        sandbox_repo.commit_file("dlg_peer/foo.py", "print('peer')\n", "Init peer")

        codex_tools = echo_codex_tool.Tools()
        token = echo_constants.ECHO_SUBAGENT_CONTEXT.set({"sub_sid": "dlg_test01"})
        try:
            r_read = await codex_tools.read_codex(
                filename="build_reco_dossier.py",
                workspace="sandbox",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(r_read["status"]["status"], "success")
            self.assertIn("root_v1", r_read["text"])

            r_edit = await codex_tools.edit_codex(
                filename="build_reco_dossier.py",
                new_content="print('root_v2')\n",
                workspace="sandbox",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(r_edit["status"]["status"], "success")
            self.assertEqual(
                sandbox_repo.read_file("build_reco_dossier.py")["content"],
                "print('root_v2')\n",
            )

            r_create_other = await codex_tools.create_codex(
                filename="other_script.py",
                content="print('isolated')\n",
                workspace="sandbox",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(r_create_other["status"]["status"], "success")
            self.assertEqual(
                sandbox_repo.read_file("dlg_test01/other_script.py")["content"],
                "print('isolated')\n",
            )

            r_create_homonym = await codex_tools.create_codex(
                filename="build_reco_dossier.py",
                content="print('sub_v1')\n",
                workspace="sandbox",
                __user__=self.user,
                __metadata__=self.meta,
            )
            self.assertEqual(r_create_homonym["status"]["status"], "success")
            self.assertEqual(
                sandbox_repo.read_file("dlg_test01/build_reco_dossier.py")["content"],
                "print('sub_v1')\n",
            )
            self.assertEqual(
                sandbox_repo.read_file("build_reco_dossier.py")["content"],
                "print('root_v2')\n",
            )
            self.assertEqual(
                sandbox_repo.read_file("dlg_peer/foo.py")["content"],
                "print('peer')\n",
            )
        finally:
            echo_constants.ECHO_SUBAGENT_CONTEXT.reset(token)


if __name__ == "__main__":
    unittest.main()
