import base64
import json
import os

def display(html_content: str, title: str = "ECHO Sandbox Monitor", width: str = "100%", height: str = "400px", window_id: str = "default"):
    """
    Transmet une interface HTML (base64) à Open WebUI via le canal dédié JSONL.
    Prend en charge le multi-fenêtrage via l'identifiant 'window_id'.
    """
    payload_html = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')
    meta = {
        "window_id": window_id,
        "title": title,
        "width": width,
        "height": height,
        "html": payload_html
    }
    
    # Ecriture dans le canal dédié en mode Append (Multiplexage)
    with open('/sandbox/.echo_monitor.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(meta) + "\n")

def get_ui_payload(window_id: str = "default") -> dict:
    """
    Récupère le payload massif (json/base64) injecté par le Frontend.
    Multiplexé : Retourne uniquement les données associées à 'window_id'.
    """
    filename = os.environ.get("ECHO_UI_PAYLOAD_FILE", ".echo_ui_payload.json")
    path = f'/sandbox/{filename}'
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.loads(f.read())
                return data.get(window_id) if isinstance(data, dict) else None
        except Exception:
            pass
    return None

def is_window_closed(window_id: str = "default") -> bool:
    """
    Indique si l'interface (ECHO Monitor) a été fermée par l'utilisateur.
    Utile pour interrompre proprement un script synchrone ou asynchrone.
    """
    payload = get_ui_payload(window_id)
    if payload and isinstance(payload, dict):
        return payload.get("_is_closed", False)
    # Par défaut, on ne considère pas la fenêtre comme fermée si le payload est juste absent
    return False
