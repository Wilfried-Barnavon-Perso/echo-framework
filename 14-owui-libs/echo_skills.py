"""
title: ECHO Skills Manager
author: ECHO Framework
version: 1.3
description: Composant système interne : ECHO Skills Manager.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.3: Alignement avec le Hotfix Core 6.9.

import os
import re
from typing import List, Dict, Optional
from echo_utils import get_echo_global_path

def get_skills_dir(user_id: str) -> str:
    """Retourne le chemin du répertoire des skills de l'utilisateur."""
    skills_dir = get_echo_global_path(user_id, "skills")
    os.makedirs(skills_dir, exist_ok=True)
    return skills_dir

def parse_skill_metadata(content: str) -> Dict:
    """Extrait manuellement le frontmatter YAML sans dépendance externe."""
    metadata = {"name": "Inconnu", "description": "Aucune description."}
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if match:
        yaml_block = match.group(1)
        for line in yaml_block.split('\n'):
            if ':' in line:
                key, val = line.split(':', 1)
                metadata[key.strip().lower()] = val.strip()
    return metadata

def get_all_skills(user_id: str) -> List[Dict]:
    """Liste tous les skills disponibles pour un utilisateur."""
    skills_dir = get_skills_dir(user_id)
    skills = []
    
    if not os.path.exists(skills_dir):
        return skills
        
    for skill_folder in os.listdir(skills_dir):
        folder_path = os.path.join(skills_dir, skill_folder)
        if os.path.isdir(folder_path):
            skill_file = os.path.join(folder_path, "SKILL.md")
            if os.path.exists(skill_file):
                try:
                    with open(skill_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    metadata = parse_skill_metadata(content)
                    metadata['id'] = skill_folder
                    skills.append(metadata)
                except Exception as e:
                    print(f"Error reading skill {skill_folder}: {e}")
                    
    return skills

def get_skill_content(user_id: str, skill_id: str) -> Optional[str]:
    """Récupère le contenu complet d'un skill (incluant les instructions)."""
    skills_dir = get_skills_dir(user_id)
    skill_file = os.path.join(skills_dir, skill_id, "SKILL.md")
    
    if os.path.exists(skill_file):
        try:
            with open(skill_file, 'r', encoding='utf-8') as f:
                return f.read()
        except:
            pass
    return None

def save_skill(user_id: str, skill_id: str, name: str, description: str, instructions: str):
    """Sauvegarde ou modifie un skill au format SKILL.md."""
    skills_dir = get_skills_dir(user_id)
    skill_path = os.path.join(skills_dir, skill_id)
    os.makedirs(skill_path, exist_ok=True)
    
    skill_file = os.path.join(skill_path, "SKILL.md")
    
    content = f"""---
name: {name}
description: {description}
---

{instructions}
"""
    
    with open(skill_file, 'w', encoding='utf-8') as f:
        f.write(content.strip() + "\n")
    
    return True
