/**
 * ================================================================================
 * MODULE : ECHO - ECHO MONITOR (Node.js)
 * VERSION : 5.234.1.1
 * ================================================================================
 */
// Règle : Conserver uniquement les 5 dernières versions dans l'historique.
// Historique des versions :
// 5.234.1.1: Isolation IPC par variables d'environnement (ECHO_MONITOR_FILE, ECHO_UI_PAYLOAD_FILE, ECHO_UI_INBOX_FILE) et ajout de push_event, get_ui_requests et BidiRouter.
// 5.226.18.0: Alignement sur le versioning global ECHO.
const fs = require('fs');
const path = require('path');

const _inboxOffsets = {};

function _getMonitorPath() {
    const filename = process.env.ECHO_MONITOR_FILE || '.echo_monitor.jsonl';
    return path.isAbsolute(filename) ? filename : `/sandbox/${filename}`;
}

function _getPayloadPath() {
    const filename = process.env.ECHO_UI_PAYLOAD_FILE || '.echo_ui_payload.json';
    return path.isAbsolute(filename) ? filename : `/sandbox/${filename}`;
}

function _getInboxPath() {
    const filename = process.env.ECHO_UI_INBOX_FILE || '.echo_ui_inbox.jsonl';
    return path.isAbsolute(filename) ? filename : `/sandbox/${filename}`;
}

function display(htmlContent, title = "ECHO Sandbox Monitor", width = "100%", height = "400px", window_id = "default") {
    const payloadHtml = Buffer.from(htmlContent, 'utf-8').toString('base64');
    const meta = {
        type: "display",
        window_id: window_id,
        title: title,
        width: width,
        height: height,
        html: payloadHtml
    };

    try {
        fs.appendFileSync(_getMonitorPath(), JSON.stringify(meta) + "\n", 'utf-8');
    } catch (e) {
        console.error("Failed to write to ECHO Monitor:", e);
    }
}

function push_event(data, window_id = "default", req_id = null) {
    const meta = {
        type: "event",
        window_id: window_id,
        req_id: req_id,
        data: data
    };
    try {
        fs.appendFileSync(_getMonitorPath(), JSON.stringify(meta) + "\n", 'utf-8');
    } catch (e) {
        console.error("Failed to push event to ECHO Monitor:", e);
    }
}

function get_ui_payload(window_id = "default") {
    const p = _getPayloadPath();
    if (fs.existsSync(p)) {
        try {
            const raw = fs.readFileSync(p, 'utf-8');
            const data = JSON.parse(raw);
            return (data && typeof data === 'object') ? (data[window_id] || null) : null;
        } catch (e) {
            return null;
        }
    }
    return null;
}

function get_ui_requests(window_id = null) {
    const p = _getInboxPath();
    if (!fs.existsSync(p)) return [];
    const cursorKey = window_id || '__all__';
    const offset = _inboxOffsets[cursorKey] || 0;
    const events = [];
    try {
        const buf = fs.readFileSync(p);
        if (buf.length <= offset) return [];
        const slice = buf.subarray(offset);
        const lastNl = slice.lastIndexOf(10); // '\n'
        if (lastNl !== -1) {
            const valid = slice.subarray(0, lastNl + 1);
            _inboxOffsets[cursorKey] = offset + valid.length;
            const lines = valid.toString('utf-8').split('\n');
            for (const rawLine of lines) {
                const line = rawLine.trim();
                if (!line) continue;
                try {
                    const item = JSON.parse(line);
                    if (item && typeof item === 'object') {
                        if (window_id === null || (item.window_id || 'default') === window_id) {
                            events.push(item);
                        }
                    }
                } catch (_) {}
            }
        }
    } catch (_) {}
    return events;
}

function is_window_closed(window_id = "default") {
    const payload = get_ui_payload(window_id);
    if (payload && typeof payload === 'object') {
        return Boolean(payload._is_closed);
    }
    return false;
}

class BidiRouter {
    constructor(window_id = "default") {
        this.window_id = window_id;
        this._routes = {};
    }

    route(action, fn) {
        this._routes[action] = fn;
        return this;
    }

    async dispatch_once() {
        const reqs = get_ui_requests(this.window_id);
        let handled = 0;
        for (const req of reqs) {
            const action = req.action;
            const reqId = req.req_id || null;
            const payload = req.payload;
            if (typeof this._routes[action] === 'function') {
                try {
                    const res = await this._routes[action](payload);
                    push_event({ status: "ok", action: action, result: res }, this.window_id, reqId);
                } catch (err) {
                    push_event({ status: "error", action: action, error: String(err && err.message ? err.message : err) }, this.window_id, reqId);
                }
                handled += 1;
            }
        }
        return handled;
    }

    serve_forever(pollIntervalMs = 200) {
        const timer = setInterval(async () => {
            if (is_window_closed(this.window_id)) {
                clearInterval(timer);
                return;
            }
            await this.dispatch_once();
        }, pollIntervalMs);
        return timer;
    }
}

module.exports = { display, push_event, get_ui_payload, get_ui_requests, is_window_closed, BidiRouter };
