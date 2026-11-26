# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ECHO V6 chat windowing & lazy message details."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "16-docker-echo-webui" / "backend"
LIBS_DIR = REPO_ROOT / "14-owui-libs"
if str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))

_MOD_PATH = BACKEND_DIR / "open_webui" / "echo" / "chat_windowing.py"
_SPEC = importlib.util.spec_from_file_location("echo_chat_windowing", _MOD_PATH)
assert _SPEC and _SPEC.loader
chat_windowing = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chat_windowing)


def _make_linear_messages(count: int) -> dict[str, dict]:
    msgs: dict[str, dict] = {}
    prev_id: str | None = None
    for idx in range(count):
        mid = f"m{idx}"
        role = "user" if idx % 2 == 0 else "assistant"
        msg: dict = {
            "id": mid,
            "parentId": prev_id,
            "childrenIds": [f"m{idx + 1}"] if idx + 1 < count else [],
            "role": role,
            "timestamp": 1000 + idx,
            "content": f"Message {idx}",
            "done": True,
        }
        if role == "assistant":
            heavy_reason = "R" * 700
            heavy_tool = "T" * 700
            heavy_args = "A" * 700
            msg["content"] = (
                f'<details type="reasoning" done="true"><summary>Thought</summary>\n{heavy_reason}\n</details>\n'
                f'<details type="tool_calls" arguments="{heavy_args}" done="true">'
                f"<summary>Tool</summary>\n{heavy_tool}\n</details>\n"
                f"Visible answer {idx}."
            )
            msg["output"] = [
                {
                    "type": "reasoning",
                    "id": f"rs_{idx}",
                    "summary": [{"type": "summary_text", "text": heavy_reason}],
                    "content": [{"type": "output_text", "text": heavy_reason}],
                },
                {
                    "type": "function_call",
                    "id": f"fc_{idx}",
                    "call_id": f"call_{idx}",
                    "name": "bash",
                    "status": "completed",
                    "arguments": heavy_args,
                },
                {
                    "type": "function_call_output",
                    "id": f"fco_{idx}",
                    "call_id": f"call_{idx}",
                    "name": "bash",
                    "status": "completed",
                    "output": [
                        {"type": "input_text", "text": "O" * 700},
                        {"type": "input_image", "image_url": "/api/v1/files/img-1/content"},
                    ],
                },
            ]
        msgs[mid] = msg
        prev_id = mid
    return msgs


class TestEchoChatWindowing(unittest.TestCase):
    def test_build_tree_index_and_extract_branch_ids(self) -> None:
        msgs = {
            "m0": {"id": "m0", "parentId": None, "childrenIds": ["m1", "m2"], "role": "user", "timestamp": 1},
            "m1": {"id": "m1", "parentId": "m0", "childrenIds": [], "role": "assistant", "timestamp": 2},
            "m2": {"id": "m2", "parentId": "m0", "childrenIds": ["m3"], "role": "assistant", "timestamp": 3},
            "m3": {"id": "m3", "parentId": "m2", "childrenIds": [], "role": "user", "timestamp": 4},
        }
        idx = chat_windowing.build_tree_index(msgs)
        self.assertEqual(set(idx.keys()), {"m0", "m1", "m2", "m3"})
        self.assertEqual(idx["m0"]["childrenIds"], ["m1", "m2"])
        self.assertEqual(chat_windowing.extract_branch_ids(msgs, "m3"), ["m0", "m2", "m3"])

        # Anti-cycle guard
        cyclic = {
            "a": {"id": "a", "parentId": "b", "childrenIds": ["b"], "role": "user", "timestamp": 1},
            "b": {"id": "b", "parentId": "a", "childrenIds": ["a"], "role": "assistant", "timestamp": 2},
        }
        self.assertEqual(chat_windowing.extract_branch_ids(cyclic, "b"), ["a", "b"])

    def test_strip_heavy_message_details_preserves_list_schemas_and_images(self) -> None:
        msgs = _make_linear_messages(4)
        user_msg = msgs["m0"]
        self.assertIs(chat_windowing.strip_heavy_message_details(user_msg), user_msg)

        streaming_msg = {**msgs["m1"], "done": False}
        self.assertIs(chat_windowing.strip_heavy_message_details(streaming_msg), streaming_msg)

        stripped = chat_windowing.strip_heavy_message_details(msgs["m1"])
        self.assertTrue(stripped.get("_echoLazyDetails"))
        self.assertIn("Visible answer 1.", stripped["content"])
        self.assertIn('<details type="reasoning" done="true"><summary>Thought</summary>', stripped["content"])
        self.assertIn('arguments="<!-- echo-lazy -->"', stripped["content"])
        self.assertIn("<!-- echo-lazy -->", stripped["content"])
        self.assertNotIn("R" * 100, stripped["content"])
        self.assertNotIn("T" * 100, stripped["content"])
        self.assertNotIn("A" * 100, stripped["content"])

        # Reasoning preserves list[dict] for both summary and content
        self.assertEqual(
            stripped["output"][0]["summary"],
            [{"type": "summary_text", "text": "<!-- echo-lazy -->"}],
        )
        self.assertEqual(
            stripped["output"][0]["content"],
            [{"type": "output_text", "text": "<!-- echo-lazy -->"}],
        )

        # Function call & output preserve metadata, list[dict] structure, and input_image parts
        self.assertEqual(stripped["output"][1]["type"], "function_call")
        self.assertEqual(stripped["output"][1]["call_id"], "call_1")
        self.assertEqual(stripped["output"][1]["name"], "bash")
        self.assertEqual(stripped["output"][1]["arguments"], "<!-- echo-lazy -->")

        fco_out = stripped["output"][2]["output"]
        self.assertIsInstance(fco_out, list)
        self.assertEqual(
            fco_out,
            [
                {"type": "input_text", "text": "<!-- echo-lazy -->"},
                {"type": "input_image", "image_url": "/api/v1/files/img-1/content"},
            ],
        )

    def test_strip_heavy_details_nested_blocks_and_exemptions(self) -> None:
        nested_reason = (
            "Outer start\n"
            '<details><summary>Inner code note</summary>\nInner body\n</details>\n'
            + ("Z" * 700)
        )
        html_content = (
            f'<details type="reasoning" attr="a > b"><summary>Deep</summary>{nested_reason}</details>\n'
            f'<details type="tool_calls" name="display_file" arguments="{"D" * 650}">'
            f"<summary>File</summary>{'F' * 650}</details>\n"
            f'<details type="tool_calls" name="render_ui" embeds="echo-embed://abc?chat_id=1">'
            f"<summary>Embed</summary>{'E' * 650}</details>\n"
            "Final visible paragraph."
        )
        msg = {
            "id": "m_nested",
            "role": "assistant",
            "done": True,
            "content": html_content,
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_disp",
                    "call_id": "call_disp",
                    "name": "display_file",
                    "status": "completed",
                    "arguments": json.dumps({"path": "/tmp/" + ("x" * 650)}),
                },
                {
                    "type": "function_call_output",
                    "id": "fco_disp",
                    "call_id": "call_disp",
                    "output": [{"type": "input_text", "text": json.dumps({"path": "/tmp/" + ("y" * 650)})}],
                },
                {
                    "type": "function_call",
                    "id": "fc_emb",
                    "call_id": "call_emb",
                    "name": "render_widget",
                    "status": "completed",
                    "arguments": "W" * 650,
                },
                {
                    "type": "function_call_output",
                    "id": "fco_emb",
                    "call_id": "call_emb",
                    "output": [{"type": "input_text", "text": "echo-embed://wid_1?chat_id=c1 " + ("M" * 650)}],
                },
            ],
        }
        stripped = chat_windowing.strip_heavy_message_details(msg)
        self.assertTrue(stripped.get("_echoLazyDetails"))
        # Nested </details> did not leak trailing ZZZ... outside the reasoning block
        self.assertNotIn("Z" * 50, stripped["content"])
        self.assertIn("Final visible paragraph.", stripped["content"])
        # display_file and echo-embed:// blocks are preserved verbatim in content and output
        self.assertIn("F" * 650, stripped["content"])
        self.assertIn("E" * 650, stripped["content"])
        self.assertEqual(stripped["output"], msg["output"])

    def test_restore_lazy_message_fields_and_missing_stored_purge(self) -> None:
        msgs = _make_linear_messages(2)
        stored = msgs["m1"]
        stripped = chat_windowing.strip_heavy_message_details(stored)
        edited_lazy = {**stripped, "timestamp": 9999}
        restored = chat_windowing.restore_lazy_message_fields(stored, edited_lazy)
        self.assertNotIn("_echoLazyDetails", restored)
        self.assertEqual(restored["content"], stored["content"])
        self.assertEqual(restored["output"], stored["output"])
        self.assertEqual(restored["timestamp"], 9999)

        # Fallback when stored_msg lacks "output" (or is None): never leak <!-- echo-lazy -->
        partial_stored = {"id": "m1", "role": "assistant", "content": "Clean content"}
        restored_partial = chat_windowing.restore_lazy_message_fields(partial_stored, stripped)
        self.assertNotIn("_echoLazyDetails", restored_partial)
        self.assertEqual(restored_partial["content"], "Clean content")
        self.assertNotIn("<!-- echo-lazy -->", json.dumps(restored_partial))

        restored_none = chat_windowing.restore_lazy_message_fields(None, stripped)
        self.assertNotIn("_echoLazyDetails", restored_none)
        self.assertNotIn("<!-- echo-lazy -->", json.dumps(restored_none))

    def test_build_windowed_chat_payload_without_db_creates_no_phantom_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_db = os.path.join(tmpdir, "nonexistent", "session.db")
            msgs = _make_linear_messages(14)
            payload = {
                "id": "chat-1",
                "user_id": "user-1",
                "chat": {
                    "messages": list(msgs.values()),
                    "history": {"messages": msgs, "currentId": "m13"},
                },
            }
            with patch.object(chat_windowing, "get_echo_db_path", return_value=missing_db):
                out = chat_windowing.build_windowed_chat_payload(
                    payload, user_id="user-1", chat_id="chat-1", limit=10
                )

            self.assertFalse(os.path.exists(missing_db))
            self.assertNotIn("messages", out["chat"])
            loaded = out["chat"]["history"]["messages"]
            self.assertEqual(len(loaded), 10)
            self.assertEqual(out["window_meta"]["has_older"], True)
            self.assertEqual(out["window_meta"]["oldest_loaded_id"], "m4")
            self.assertEqual(out["window_meta"]["total_branch_messages"], 14)
            self.assertEqual(out["window_meta"]["loaded_branch_messages"], 10)
            # Last ECHO_CHAT_EAGER_DETAILS_TAIL (8) messages [m6..m13] remain unstripped, m5 is stripped
            self.assertTrue(loaded["m5"].get("_echoLazyDetails"))
            self.assertNotIn("_echoLazyDetails", loaded["m7"])
            self.assertNotIn("_echoLazyDetails", loaded["m13"])

    def test_legacy_messages_list_and_orphan_current_id_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_db = os.path.join(tmpdir, "nonexistent", "session.db")
            legacy_list = [
                {"id": "u0", "role": "user", "content": "Hello", "timestamp": 100},
                {"id": "a1", "role": "assistant", "content": "Hi there", "timestamp": 200, "done": True},
            ]
            legacy_payload = {
                "id": "chat-legacy",
                "user_id": "user-1",
                "chat": {"history": None, "messages": legacy_list},
            }
            with patch.object(chat_windowing, "get_echo_db_path", return_value=missing_db):
                out = chat_windowing.build_windowed_chat_payload(
                    legacy_payload, user_id="user-1", chat_id="chat-legacy"
                )
            self.assertNotIn("messages", out["chat"])
            self.assertEqual(out["chat"]["history"]["currentId"], "a1")
            self.assertEqual(set(out["chat"]["history"]["messages"].keys()), {"u0", "a1"})

            # Orphan currentId falls back to message with max timestamp
            msgs = _make_linear_messages(4)
            orphan_payload = {
                "id": "chat-orphan",
                "user_id": "user-1",
                "current_message_id": "deleted_ghost_id",
                "chat": {"history": {"messages": msgs, "currentId": "deleted_ghost_id"}},
            }
            with patch.object(chat_windowing, "get_echo_db_path", return_value=missing_db):
                out_orphan = chat_windowing.build_windowed_chat_payload(
                    orphan_payload, user_id="user-1", chat_id="chat-orphan"
                )
            self.assertEqual(out_orphan["chat"]["history"]["currentId"], "m3")
            self.assertEqual(len(out_orphan["chat"]["history"]["messages"]), 4)

    def test_build_windowed_chat_payload_with_checkpoint_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "session.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE echo_settings (key TEXT PRIMARY KEY, value TEXT)")
            ckpt_payload = {
                "version": 2,
                "checkpoints": [
                    {
                        "checkpoint_id": "ckpt_1",
                        "compaction_count": 1,
                        "pivot_msg_id": "m6",
                        "head_last_msg_id": "m5",
                        "created_at": 100,
                    },
                    {
                        "checkpoint_id": "ckpt_orphan",
                        "compaction_count": 2,
                        "pivot_msg_id": "missing_id",
                        "created_at": 200,
                    },
                ],
            }
            conn.execute(
                "INSERT INTO echo_settings (key, value) VALUES ('context_checkpoint', ?)",
                (json.dumps(ckpt_payload),),
            )
            conn.commit()
            conn.close()

            msgs = _make_linear_messages(16)
            payload = {
                "id": "chat-2",
                "user_id": "user-1",
                "chat": {"history": {"messages": msgs, "currentId": "m15"}},
            }
            with patch.object(chat_windowing, "get_echo_db_path", return_value=db_path):
                out = chat_windowing.build_windowed_chat_payload(
                    payload, user_id="user-1", chat_id="chat-2", limit=12
                )

            # Checkpoint at m6 (index 6) > 16 - 12 = 4 => starts at m6
            loaded = out["chat"]["history"]["messages"]
            self.assertEqual(out["window_meta"]["oldest_loaded_id"], "m6")
            self.assertEqual(len(loaded), 10)
            ckpts = out["window_meta"]["checkpoints"]
            self.assertEqual(len(ckpts), 2)
            self.assertEqual(ckpts[0]["branch_index"], 6)
            self.assertIsNone(ckpts[1]["branch_index"])

    def test_extract_older_window_slice_checkpoint_unknown_before_and_target_inclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "session.db")
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE echo_settings (key TEXT PRIMARY KEY, value TEXT)")
            ckpt_payload = {
                "version": 2,
                "checkpoints": [
                    {
                        "checkpoint_id": "ckpt_early",
                        "compaction_count": 1,
                        "pivot_msg_id": "m2",
                        "created_at": 100,
                    }
                ],
            }
            conn.execute(
                "INSERT INTO echo_settings (key, value) VALUES ('context_checkpoint', ?)",
                (json.dumps(ckpt_payload),),
            )
            conn.commit()
            conn.close()

            msgs = _make_linear_messages(10)
            # Add sibling branch at m4 -> m4_alt -> m4_alt_reply -> m4_alt_a2 -> m4_alt_u3
            msgs["m4"]["childrenIds"].append("m4_alt")
            msgs["m4_alt"] = {
                "id": "m4_alt",
                "parentId": "m4",
                "childrenIds": ["m4_alt_reply"],
                "role": "assistant",
                "timestamp": 2000,
                "content": '<details type="reasoning"><summary>S</summary>' + ("X" * 700) + "</details>\nAlt reply",
                "done": True,
            }
            msgs["m4_alt_reply"] = {
                "id": "m4_alt_reply",
                "parentId": "m4_alt",
                "childrenIds": ["m4_alt_a2"],
                "role": "user",
                "timestamp": 2001,
                "content": "Follow-up",
                "done": True,
            }
            msgs["m4_alt_a2"] = {
                "id": "m4_alt_a2",
                "parentId": "m4_alt_reply",
                "childrenIds": ["m4_alt_u3"],
                "role": "assistant",
                "timestamp": 2002,
                "content": "Second alt reply",
                "done": True,
            }
            msgs["m4_alt_u3"] = {
                "id": "m4_alt_u3",
                "parentId": "m4_alt_a2",
                "childrenIds": [],
                "role": "user",
                "timestamp": 2003,
                "content": "Third follow-up",
                "done": True,
            }
            chat_dict = {"history": {"messages": msgs, "currentId": "m9"}}

            with patch.object(chat_windowing, "get_echo_db_path", return_value=db_path):
                unknown = chat_windowing.extract_older_window_slice(
                    chat_dict,
                    user_id="user-1",
                    chat_id="chat-3",
                    before_id="nonexistent_before_id",
                )
                self.assertEqual(unknown["messages"], {})
                self.assertFalse(unknown["has_older"])
                self.assertIsNone(unknown["oldest_loaded_id"])

                older = chat_windowing.extract_older_window_slice(
                    chat_dict,
                    user_id="user-1",
                    chat_id="chat-3",
                    before_id="m6",
                    to_checkpoint_id="ckpt_early",
                )
                self.assertEqual(list(older["messages"].keys()), ["m2", "m3", "m4", "m5"])
                self.assertTrue(older["has_older"])
                self.assertEqual(older["oldest_loaded_id"], "m2")

                # Small limit=2 on long sibling branch still explicitly includes target_id ("m4_alt")
                sibling = chat_windowing.extract_older_window_slice(
                    chat_dict,
                    user_id="user-1",
                    chat_id="chat-3",
                    target_id="m4_alt",
                    limit=2,
                )
                self.assertIn("m4_alt", sibling["messages"])
                self.assertIn("m4_alt_a2", sibling["messages"])
                self.assertIn("m4_alt_u3", sibling["messages"])
                self.assertTrue(sibling["has_older"])

    def test_extract_full_message_details(self) -> None:
        msgs = _make_linear_messages(3)
        msgs["m1"]["_echoLazyDetails"] = True
        details = chat_windowing.extract_full_message_details(
            {"history": {"messages": msgs}},
            ["m1", "missing_id"],
        )
        self.assertEqual(list(details.keys()), ["m1"])
        self.assertNotIn("_echoLazyDetails", details["m1"])


if __name__ == "__main__":
    unittest.main()
