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
