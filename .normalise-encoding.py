import os

# Extensions à traiter
EXTENSIONS = {".py", ".sh", ".md", ".txt", ".json", ".yml", ".yaml", ".html", ".css", ".js"}

# Dossiers à ignorer
IGNORE_DIRS = {".git", ".vscode", "__pycache__", "venv", "node_modules"}

def is_text_file(filepath):
    return any(filepath.endswith(ext) for ext in EXTENSIONS)

def normalize_file(filepath):
    try:
        with open(filepath, "rb") as f:
            content = f.read()

        original_content = content
        
        # 1. Détection et suppression du BOM UTF-8 (EF BB BF)
        has_bom = content.startswith(b'\xef\xbb\xbf')
        if has_bom:
            content = content[3:]

        # 2. Décodage en texte (UTF-8)
        try:
            text_content = content.decode("utf-8")
        except UnicodeDecodeError:
            print(f"⚠️  Skipped (Not UTF-8): {filepath}")
            return

        # 3. Normalisation des fins de ligne (CRLF -> LF)
        if "\r\n" in text_content:
            text_content = text_content.replace("\r\n", "\n")
            has_crlf = True
        else:
            has_crlf = False

        # 4. Réécriture si nécessaire
        if has_bom or has_crlf:
            with open(filepath, "w", encoding="utf-8", newline="\n") as f:
                f.write(text_content)
            
            changes = []
            if has_bom: changes.append("BOM removed")
            if has_crlf: changes.append("CRLF -> LF")
            print(f"✅ Fixed {filepath}: {', '.join(changes)}")
            
    except Exception as e:
        print(f"❌ Error processing {filepath}: {e}")

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"🔍 Scanning directory: {root_dir}")

    for root, dirs, files in os.walk(root_dir):
        # Filtrer les dossiers ignorés
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for file in files:
            filepath = os.path.join(root, file)
            if is_text_file(filepath):
                normalize_file(filepath)

    print("✨ Normalization complete.")

if __name__ == "__main__":
    main()