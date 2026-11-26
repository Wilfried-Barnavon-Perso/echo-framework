# -*- coding: utf-8 -*-
"""Tests unitaires pour le schéma context_checkpoint v2 (Lot 1 & correctifs v6.22.1)."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBS_DIR = REPO_ROOT / "14-owui-libs"
if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))

from echo_constants import (  # noqa: E402
    ECHO_COMPACTION_CHECKPOINT_VERSION,
    ECHO_COMPACTION_MAX_CHECKPOINTS,
    normalize_checkpoint_payload,
    prune_checkpoint_payload,
)
from echo_session_gc import DeadSet, purge_dead_set  # noqa: E402


class TestCompactionCheckpointsV2(unittest.TestCase):
    """Couvre la normalisation v1->v2, l'élagage fork/GC, la purge anti-fuite racine et la lecture sans DB fantôme."""

    def test_normalize_v1_to_v2(self) -> None:
        v1 = {
            "version": 1,
            "compaction_count": 3,
            "pivot_turn_idx": 12,
            "pivot_msg_id": "m_pivot_3",
            "head_last_msg_id": "m_head_3",
            "keep_recent_turns": 4,
            "tail_anchor_ids": ["m_t1", "m_t2"],
            "summary_md": "Résumé v1",
            "summary_xml": '<artifact id="AEC_context_checkpoint">v1</artifact>',
            "checkpoint_tokens": 420,
            "tail_tokens_est": 180,
            "created_at": 1700000300,
        }
        res = normalize_checkpoint_payload(json.dumps(v1))
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res["version"], ECHO_COMPACTION_CHECKPOINT_VERSION)
        self.assertEqual(res["summary_md"], "Résumé v1")
        self.assertEqual(len(res["checkpoints"]), 1)
        entry = res["checkpoints"][0]
        self.assertEqual(entry["checkpoint_id"], "ckpt_3_1700000300")
        self.assertEqual(entry["pivot_msg_id"], "m_pivot_3")
        self.assertEqual(entry["head_last_msg_id"], "m_head_3")
        self.assertEqual(entry["tail_anchor_ids"], ["m_t1", "m_t2"])
        self.assertIsNone(normalize_checkpoint_payload("not-json{"))
        self.assertIsNone(normalize_checkpoint_payload(None))

    def test_normalize_sorts_and_caps_max_checkpoints(self) -> None:
        total = ECHO_COMPACTION_MAX_CHECKPOINTS + 10
        raw_list = [
            {
                "compaction_count": total - i,
                "pivot_turn_idx": (total - i) * 2,
                "pivot_msg_id": f"m_{total - i}",
                "created_at": 1700000000 + (total - i) * 10,
            }
            for i in range(total)
        ]
        res = normalize_checkpoint_payload({"version": 2, "checkpoints": raw_list})
        self.assertIsNotNone(res)
        assert res is not None
        ckpts = res["checkpoints"]
        self.assertEqual(len(ckpts), ECHO_COMPACTION_MAX_CHECKPOINTS)
        # Vérifie le tri chronologique croissant et la conservation des plus récents
        self.assertEqual(ckpts[0]["compaction_count"], 11)
        self.assertEqual(ckpts[-1]["compaction_count"], total)

    def test_prune_for_partial_fork_and_root_summary_invalidation(self) -> None:
        payload = {
            "version": 2,
            "compaction_count": 2,
            "pivot_turn_idx": 10,
            "pivot_msg_id": "m2",
            "head_last_msg_id": "h2",
            "tail_anchor_ids": ["t2"],
            "summary_md": "Résumé contaminé par m2",
            "summary_xml": "<artifact>Résumé contaminé par m2</artifact>",
            "checkpoint_tokens": 500,
            "tail_tokens_est": 250,
            "created_at": 200,
            "checkpoints": [
                {
                    "checkpoint_id": "ckpt_1_100",
                    "compaction_count": 1,
                    "pivot_turn_idx": 4,
                    "pivot_msg_id": "m1",
                    "head_last_msg_id": "h1",
                    "created_at": 100,
                },
                {
                    "checkpoint_id": "ckpt_2_200",
                    "compaction_count": 2,
                    "pivot_turn_idx": 10,
                    "pivot_msg_id": "m2",
                    "head_last_msg_id": "h2",
                    "created_at": 200,
                },
            ],
        }
        # Si cutoff_ts < created_at racine, le summary_xml racine agrège du post-fork -> None
        self.assertIsNone(
            prune_checkpoint_payload(payload, cutoff_ts=150, live_msg_ids={"m1", "m2"})
        )
        # Si cutoff_ts >= created_at racine mais m2/h2 absents de live_msg_ids, ckpt_2 est élagué
        # et summary_md / summary_xml racine DOIVENT être purgés et réalignés sur ckpt_1
        pruned = prune_checkpoint_payload(payload, cutoff_ts=250, live_msg_ids={"m1", "h1"})
        self.assertIsNotNone(pruned)
        assert pruned is not None
        self.assertEqual(len(pruned["checkpoints"]), 1)
        self.assertEqual(pruned["checkpoints"][0]["pivot_msg_id"], "m1")
        self.assertEqual(pruned["summary_md"], "")
        self.assertEqual(pruned["summary_xml"], "")
        self.assertEqual(pruned["checkpoint_tokens"], 0)
        self.assertEqual(pruned["tail_tokens_est"], 0)
        self.assertEqual(pruned["tail_anchor_ids"], [])
        self.assertEqual(pruned["compaction_count"], 1)
        self.assertEqual(pruned["pivot_turn_idx"], 4)
        self.assertEqual(pruned["pivot_msg_id"], "m1")
        self.assertEqual(pruned["head_last_msg_id"], "h1")
        self.assertEqual(pruned["created_at"], 100)

        # Idem lors de l'élagage par dead_msg_ids (head_last_msg_id ou pivot_msg_id mort)
        pruned_dead = prune_checkpoint_payload(payload, dead_msg_ids={"h2"})
        self.assertIsNotNone(pruned_dead)
        assert pruned_dead is not None
        self.assertEqual(len(pruned_dead["checkpoints"]), 1)
        self.assertEqual(pruned_dead["summary_md"], "")
        self.assertEqual(pruned_dead["summary_xml"], "")
        self.assertEqual(pruned_dead["pivot_msg_id"], "m1")

        # Si le dernier checkpoint survit, summary_md / summary_xml racine sont préservés
        preserved = prune_checkpoint_payload(payload, cutoff_ts=250, live_msg_ids={"m1", "m2"})
        self.assertIsNotNone(preserved)
        assert preserved is not None
        self.assertEqual(preserved["summary_md"], "Résumé contaminé par m2")
        self.assertEqual(preserved["summary_xml"], "<artifact>Résumé contaminé par m2</artifact>")

        # Si aucune entrée ne survit -> None
        self.assertIsNone(
            prune_checkpoint_payload(payload, cutoff_ts=200, live_msg_ids={"m_other"})
        )

    def test_purge_dead_set_dry_run_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "session.db")
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE message_shadows (message_id TEXT PRIMARY KEY);
                    CREATE TABLE rag_index_state (message_id TEXT PRIMARY KEY);
                    CREATE TABLE call_bridge (call_id TEXT PRIMARY KEY, message_id TEXT);
                    CREATE TABLE echo_settings (key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER);
                    """
                )
                ckpt_v2 = {
                    "version": 2,
                    "compaction_count": 2,
                    "pivot_msg_id": "dead_m2",
                    "head_last_msg_id": "dead_h2",
                    "summary_md": "Résumé ckpt 2",
                    "summary_xml": "<artifact>ckpt 2</artifact>",
                    "created_at": 200,
                    "checkpoints": [
                        {
                            "checkpoint_id": "ckpt_1_100",
                            "compaction_count": 1,
                            "pivot_msg_id": "live_m1",
                            "head_last_msg_id": "live_h1",
                            "created_at": 100,
                        },
                        {
                            "checkpoint_id": "ckpt_2_200",
                            "compaction_count": 2,
                            "pivot_msg_id": "dead_m2",
                            "head_last_msg_id": "dead_h2",
                            "created_at": 200,
                        },
                    ],
                }
                orig_json = json.dumps(ckpt_v2)
                conn.execute(
                    "INSERT INTO echo_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    ("context_checkpoint", orig_json, 200),
                )
                conn.execute("INSERT INTO message_shadows (message_id) VALUES ('dead_m2')")
                conn.commit()

                dead = DeadSet(
                    message_ids={"dead_m2"},
                    call_ids=set(),
                    cumuls=set(),
                    rag_seeds=set(),
                )

                # 1. dry_run=True préserve intacte la DB
                purge_dead_set(conn, dead, dry_run=True)
                row_dry = conn.execute(
                    "SELECT value FROM echo_settings WHERE key = 'context_checkpoint'"
                ).fetchone()
                self.assertEqual(row_dry[0], orig_json)

                # 2. dry_run=False élague dead_m2, conserve live_m1 et purge summary_md/summary_xml racine
                purge_dead_set(conn, dead, dry_run=False)
                row_pruned = conn.execute(
                    "SELECT value FROM echo_settings WHERE key = 'context_checkpoint'"
                ).fetchone()
                self.assertIsNotNone(row_pruned)
                parsed = json.loads(row_pruned[0])
                self.assertEqual(len(parsed["checkpoints"]), 1)
                self.assertEqual(parsed["checkpoints"][0]["pivot_msg_id"], "live_m1")
                self.assertEqual(parsed["summary_md"], "")
                self.assertEqual(parsed["summary_xml"], "")
                self.assertEqual(parsed["pivot_msg_id"], "live_m1")

                # 3. purge du dernier checkpoint restant -> suppression de la clé
                purge_dead_set(
                    conn,
                    DeadSet(
                        message_ids={"live_m1"},
                        call_ids=set(),
                        cumuls=set(),
                        rag_seeds=set(),
                    ),
                    dry_run=False,
                )
                row_gone = conn.execute(
                    "SELECT value FROM echo_settings WHERE key = 'context_checkpoint'"
                ).fetchone()
                self.assertIsNone(row_gone)
            finally:
                conn.close()

    def test_get_session_checkpoints_no_phantom_db(self) -> None:
        from echo_core import EchoCompactionEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_db = os.path.join(tmpdir, "u1", "c1", "session.db")
            with mock.patch("echo_paths.get_echo_db_path", return_value=fake_db):
                res = EchoCompactionEngine.get_session_checkpoints("u1", "c1")
            self.assertEqual(res, [])
            self.assertEqual(os.listdir(tmpdir), [])


if __name__ == "__main__":
    unittest.main()
