/**
 * ================================================================================
 * MODULE : ECHO - ECHO MONITOR (Node.js)
 * VERSION : 6.15.2.1
 * ================================================================================
 */
// Règle : Conserver uniquement les 5 dernières versions dans l'historique.
// Historique des versions :
// 6.15.2.1: Ajout de push_ready(), exécution concurrente bornée (maxWorkers) dans BidiRouter et support natif __echo_ping__ / rejet RPC inconnu.
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

function push_ready(window_id = "default", actions = []) {
    const meta = {
        type: "ready",
        window_id: window_id,
        actions: Array.isArray(actions) ? actions : []
    };
    try {
        fs.appendFileSync(_getMonitorPath(), JSON.stringify(meta) + "\n", 'utf-8');
    } catch (e) {
        console.error("Failed to push ready to ECHO Monitor:", e);
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
    constructor(window_id = "default", maxWorkers = 4) {
        this.window_id = window_id;
        this.maxWorkers = Math.max(1, Number(maxWorkers) || 4);
        this._routes = {};
        this._activeCount = 0;
        this._queue = [];
    }

    route(action, fn) {
        this._routes[action] = fn;
        return this;
    }

    async _executeRequest(req) {
        const action = req.action;
        const reqId = req.req_id || null;
        const payload = req.payload;
        try {
            if (typeof this._routes[action] === 'function') {
                const res = await this._routes[action](payload);
                push_event({ status: "ok", action: action, result: res }, this.window_id, reqId);
            } else if (action === "__echo_ping__") {
                push_event(
                    {
                        status: "ok",
                        action: action,
                        result: {
                            pong: true,
                            window_id: this.window_id,
                            ts: Date.now() / 1000,
                            actions: Object.keys(this._routes).sort()
                        }
                    },
                    this.window_id,
                    reqId
                );
            } else {
                push_event(
                    {
                        status: "error",
                        action: action,
                        error: `Unknown RPC action: ${action}`,
                        available_actions: Object.keys(this._routes).sort()
                    },
                    this.window_id,
                    reqId
                );
            }
        } catch (err) {
            push_event({ status: "error", action: action, error: String(err && err.message ? err.message : err) }, this.window_id, reqId);
        }
    }

    _drainQueue() {
        while (this._activeCount < this.maxWorkers && this._queue.length > 0) {
            const item = this._queue.shift();
            this._activeCount += 1;
            Promise.resolve(this._executeRequest(item.req))
                .then(item.resolve, item.reject)
                .finally(() => {
                    this._activeCount -= 1;
                    this._drainQueue();
                });
        }
    }

    _enqueue(req) {
        return new Promise((resolve, reject) => {
            this._queue.push({ req, resolve, reject });
            this._drainQueue();
        });
    }

    async dispatch_once(nonBlocking = false) {
        const reqs = get_ui_requests(this.window_id);
        if (nonBlocking === true) {
            for (const req of reqs) {
                this._enqueue(req);
            }
            return reqs.length;
        }
        const promises = reqs.map((req) => this._enqueue(req));
        await Promise.all(promises);
        return reqs.length;
    }

    serve_forever(pollIntervalMs = 50) {
        push_ready(this.window_id, Object.keys(this._routes).sort());
        const timer = setInterval(() => {
            if (is_window_closed(this.window_id)) {
                clearInterval(timer);
                return;
            }
            this.dispatch_once(true);
        }, pollIntervalMs);
        return timer;
    }
}

module.exports = { display, push_event, push_ready, get_ui_payload, get_ui_requests, is_window_closed, BidiRouter };
