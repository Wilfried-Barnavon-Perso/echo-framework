"""
title: ECHO Download Broker
author: Wilfried BARNAVON
version: 1.0
description: Composant du système ECHO : Download Broker.
"""
import asyncio, os, shutil, sys
from pathlib import Path

# Dépendances ECHO (injectées via bind-mount readonly par stack-echo.yml)
sys.path.append("/app/backend/echo_libs")
from echo_state_manager import EchoStateManager
from echo_constants import FILE_INGESTION_STATUS, ECHO_USERS_ROOT, get_gemini_mime

async def process_downloads():
    dl_root = Path("/app/downloads")
    if not dl_root.exists(): 
        dl_root.mkdir(parents=True, exist_ok=True)
        
    try:
        os.chmod(str(dl_root), 0o777)
    except:
        pass
    
    for uid_dir in dl_root.iterdir():
        if not uid_dir.is_dir(): continue
        try:
            os.chmod(str(uid_dir), 0o777)
        except:
            pass
            
        uid = uid_dir.name
        
        for cid_dir in uid_dir.iterdir():
            if not cid_dir.is_dir(): continue
            try:
                os.chmod(str(cid_dir), 0o777)
            except:
                pass
            
            cid = cid_dir.name
            
            # Garbage Collection : Vérification Vault
            vault_cid_dir = Path(ECHO_USERS_ROOT) / uid / "chats" / cid
            if not vault_cid_dir.exists():
                shutil.rmtree(cid_dir) # Purge de la branche morte
                continue
                
            global_files_dir = Path(ECHO_USERS_ROOT) / uid / "files"
            global_files_dir.mkdir(parents=True, exist_ok=True)
            
            chat_files_dir = vault_cid_dir / "files"
            chat_files_dir.mkdir(parents=True, exist_ok=True)
            
            import time
            import uuid

            # 1. GESTION DES FLUX N8N
            n8n_dir = cid_dir / "n8n"
            if n8n_dir.exists() and n8n_dir.is_dir():
                for wf_dir in n8n_dir.iterdir():
                    if not wf_dir.is_dir(): continue
                    wf_id = wf_dir.name
                    for file_path in wf_dir.iterdir():
                        if not file_path.is_file(): continue
                        
                        # Sécurité MTime (Fichier complètement fermé par N8N, délai de 3.0s)
                        if time.time() - file_path.stat().st_mtime < 3.0:
                            continue
                            
                        fid = uuid.uuid4().hex[:8]
                        safe_name = f"{fid}_{wf_id}_{file_path.name}"
                        dest_path = global_files_dir / safe_name
                        
                        # Politique de non-écrasement
                        while dest_path.exists():
                            fid = uuid.uuid4().hex[:8]
                            safe_name = f"{fid}_{wf_id}_{file_path.name}"
                            dest_path = global_files_dir / safe_name
                            
                        try:
                            shutil.move(str(file_path), str(dest_path))
                            chat_symlink = chat_files_dir / safe_name
                            if not chat_symlink.exists():
                                os.symlink(str(dest_path), str(chat_symlink))
                        except Exception as e:
                            print(f"Erreur déplacement fichier N8N {file_path.name}: {e}")
                            continue
                            
                        mime, _ = get_gemini_mime(str(dest_path))
                        state = EchoStateManager(user_id=uid, chat_id=cid)
                        state.save_resource(
                            id=fid, name=safe_name, resource_type="binary",
                            status=FILE_INGESTION_STATUS.get('PENDING_INGESTION', 'pending_ingestion'),
                            mime=mime, storage_path=str(dest_path)
                        )
                        print(f"Fichier N8N {safe_name} ingéré.")

            # 2. GESTION DES FLUX BROWSER
            browser_dir = cid_dir / "browser"
            if browser_dir.exists() and browser_dir.is_dir():
                for file_path in browser_dir.iterdir():
                    if not file_path.is_file() or file_path.name.endswith(".part"): 
                        continue
                        
                    # Sécurité MTime (Fichier complètement fermé par Playwright, délai de 3.0s)
                    if time.time() - file_path.stat().st_mtime < 3.0:
                        continue
                        
                    filename = file_path.name
                    if "_" not in filename: continue
                    fid = filename.split("_", 1)[0]
                    
                    # Déplacement atomique vers le Vault Global
                    dest_path = global_files_dir / filename
                    try:
                        shutil.move(str(file_path), str(dest_path))
                        # Création du leurre symbolique dans le chat
                        chat_symlink = chat_files_dir / filename
                        if not chat_symlink.exists():
                            os.symlink(str(dest_path), str(chat_symlink))
                    except Exception as e:
                        print(f"Erreur déplacement fichier {filename}: {e}")
                        continue # Réessai au prochain cycle
                        
                    # Sérialisation SQLite
                    mime, _ = get_gemini_mime(str(dest_path))
                    state = EchoStateManager(user_id=uid, chat_id=cid)
                    state.save_resource(
                        id=fid, name=filename, resource_type="binary",
                        status=FILE_INGESTION_STATUS.get('PENDING_INGESTION', 'pending_ingestion'),
                        mime=mime, storage_path=str(dest_path)
                    )
                    print(f"Fichier {filename} ingéré et enregistré pour {uid}/{cid}.")

async def main():
    print("ECHO Download Broker démarré...")
    while True:
        await process_downloads()
        await asyncio.sleep(3) # Polling réactif mais doux

if __name__ == "__main__":
    asyncio.run(main())
