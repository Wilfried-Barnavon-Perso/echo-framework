const fs = require('fs');

/**
 * Transmet une interface HTML (base64) à Open WebUI via le canal dédié JSONL.
 * Prend en charge le multi-fenêtrage via l'identifiant 'window_id'.
 */
const display = (html_content, title = "ECHO Sandbox Monitor", width = "100%", height = "400px", window_id = "default") => {
    const payloadHtml = Buffer.from(html_content, 'utf8').toString('base64');
    const meta = { window_id, title, width, height, html: payloadHtml };
    
    // Ecriture dans le canal dédié en mode Append (Multiplexage)
    fs.appendFileSync('/sandbox/.echo_monitor.jsonl', JSON.stringify(meta) + '\n', 'utf8');
};

module.exports = { display };
