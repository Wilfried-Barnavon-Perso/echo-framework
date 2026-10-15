# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER - CORE UTILS
VERSION : 5.136
================================================================================
"""
import os

__all__ = [
    'human_size',
    'get_cpu_model_name',
    'get_dir_stats',
    'get_echo_version',
    '_format_bytes',
    '_get_tree_size',
]

def human_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def get_cpu_model_name():
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if "model name" in line: return line.split(':')[1].strip()
    except Exception: return "N/A"

def get_dir_stats(path, filter_ext=None):
    if not os.path.exists(path): return {"count": 0, "size": 0, "size_fmt": "0 B"}
    t_size, t_count = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            if filter_ext and not f.endswith(filter_ext): continue
            try:
                filepath = os.path.join(root, f)
                if os.path.islink(filepath):
                    t_size += os.lstat(filepath).st_size
                else:
                    t_size += os.path.getsize(filepath)
                t_count += 1
            except Exception: pass
    return {"count": t_count, "size": t_size, "size_fmt": human_size(t_size)}

def get_echo_version():
    try:
        if os.path.exists('/app/ECHO_VERSION'):
            with open('/app/ECHO_VERSION', 'r', encoding='utf-8') as f: return f.read().strip()
    except Exception: pass
    return "v?.?"

def _format_bytes(size_in_bytes: int) -> str:
    """Formate une taille en octets vers une unité lisible (Ko, Mo, Go)."""
    if size_in_bytes < 1024:
        return f"{size_in_bytes} o"
    elif size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.1f} Ko"
    elif size_in_bytes < 1024 * 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.1f} Mo"
    else:
        return f"{size_in_bytes / (1024 * 1024 * 1024):.1f} Go"

def _get_tree_size(path: str) -> int:
    """Retourne la taille totale d'un dossier ou fichier en octets."""
    total_size = 0
    if os.path.isfile(path) or os.path.islink(path):
        try:
            total_size = os.path.getsize(path)
        except Exception:
            pass
    elif os.path.isdir(path):
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    try:
                        total_size += os.path.getsize(fp)
                    except Exception:
                        pass
    return total_size
