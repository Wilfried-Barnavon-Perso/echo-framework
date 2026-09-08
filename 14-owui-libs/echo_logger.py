# -*- coding: utf-8 -*-
"""
title: ECHO Echo Logger
author: Wilfried BARNAVON
version: 1.0
description: Système de journalisation et télémétrie.
"""
import os
from datetime import datetime
from typing import Any, Dict
import orjson as std_json

class DebugLogger:
    def __init__(self, data_dir: str, chat_id: str):
        self.log_dir = os.path.join(data_dir, "debug_logs")
        os.makedirs(self.log_dir, exist_ok=True)
        safe_id = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_") if chat_id else "unknown_chat"
        self.log_path = os.path.join(self.log_dir, f"debug_{safe_id}.json")

    def log(self, event_type: str, payload: Any, metadata: Dict = None):
        import orjson as std_json
        from datetime import datetime
        entry = {"timestamp": datetime.now().isoformat(), "type": event_type, "metadata": metadata or {}, "data": payload}
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(std_json.dumps(entry).decode('utf-8') + "\n")
        except Exception: pass

