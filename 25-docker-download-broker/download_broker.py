import asyncio, os, shutil, sys
from pathlib import Path

# Dépendances ECHO (injectées via bind-mount readonly par stack-echo.yml)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoStateManager
from echo_constants import FILE_INGESTION_STATUS, ECHO_USERS_ROOT, get_gemini_mime

async def process_downloads():
    dl_root = Path("/app/browser-data/downloads")
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
            
            for file_path in cid_dir.iterdir():
                if not file_path.is_file() or file_path.name.endswith(".part"): 
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
                    id=fid, name=filename, resource_type="binary", # Provisoire
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
