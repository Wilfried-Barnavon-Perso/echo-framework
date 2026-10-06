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

const get_ui_payload = (window_id = "default") => {
    const filename = process.env.ECHO_UI_PAYLOAD_FILE || '.echo_ui_payload.json';
    const payloadFile = `/sandbox/${filename}`;
    if (fs.existsSync(payloadFile)) {
        try {
            const data = JSON.parse(fs.readFileSync(payloadFile, 'utf8'));
            return data[window_id] || null;
        } catch (e) {
            return null;
        }
    }
    return null;
};

const is_window_closed = (window_id = "default") => {
    const payload = get_ui_payload(window_id);
    if (payload && typeof payload === 'object') {
        return payload._is_closed === true;
    }
    return false;
};

module.exports = { display, get_ui_payload, is_window_closed };
