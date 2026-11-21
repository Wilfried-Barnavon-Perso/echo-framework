/**
 * ================================================================================
 * MODULE : ECHO - ECHO MONITOR (Node.js)
 * VERSION : 6.16.0.2
 * ================================================================================
 */
// Règle : Conserver uniquement les 5 dernières versions dans l'historique.
// Historique des versions :
// 6.16.0.2: Ajout de get_app_uds, get_app_port, wait_until_closed et expose_app (Niveau B2 UDS/TCP).
// 6.16.0.1: Bus mémoire Socket AF_UNIX/TCP (ECHO_IPC_SOCK), framing binaire/JSON et BidiRouter événementiel en remplacement du JSONL.
// 6.15.2.1: Ajout de push_ready(), exécution concurrente bornée (maxWorkers) dans BidiRouter et support natif __echo_ping__ / rejet RPC inconnu.
// 5.234.1.1: Isolation IPC par env vars et ajout de push_event, get_ui_requests et BidiRouter.
// 5.226.18.0: Alignement sur le versioning global ECHO.
const fs = require('fs');
const net = require('net');

const DEFAULT_IPC_SOCK = '/run/echo_ipc.sock';

let _sock = null;
let _connectedTarget = null;
let _socketClosed = false;
let _hasConnected = false;
let _recvBuffer = Buffer.alloc(0);
let _pendingWrites = 0;
let _keepAliveRefCount = 0;

const _uiPayloads = {};
const _closedWindows = new Set();
const _rpcQueue = [];
const _listeners = new Set();

function _getIpcTarget() {
    return process.env.ECHO_IPC_SOCK || DEFAULT_IPC_SOCK;
}

function _updateSocketRef() {
    if (!_sock || _sock.destroyed) return;
    if (_keepAliveRefCount > 0 || _pendingWrites > 0) {
        _sock.ref();
    } else {
        _sock.unref();
    }
}

function _notifyListeners() {
    for (const listener of Array.from(_listeners)) {
        try {
            listener();
        } catch (_) { }
    }
}

function _handleIncomingMessage(msg, binBuf) {
    const msgType = msg.type;
    const wid = msg.window_id || 'default';
    if (msgType === 'payload_sync') {
        const payload = msg.payload;
        _uiPayloads[wid] = payload;
        if (payload && typeof payload === 'object' && payload._is_closed) {
            _closedWindows.add(wid);
        }
        _notifyListeners();
    } else if (msgType === 'window_closed') {
        _closedWindows.add(wid);
        if (!_uiPayloads[wid] || typeof _uiPayloads[wid] !== 'object') {
            _uiPayloads[wid] = {};
        }
        _uiPayloads[wid]._is_closed = true;
        _notifyListeners();
    } else if (msgType === 'rpc') {
        _rpcQueue.push({
            window_id: wid,
            req_id: msg.req_id !== undefined ? msg.req_id : null,
            action: msg.action,
            payload: msg.payload,
            binary: binBuf
        });
        _notifyListeners();
    }
}

function _onSocketData(chunk) {
    _recvBuffer = _recvBuffer.length === 0 ? chunk : Buffer.concat([_recvBuffer, chunk]);
    while (_recvBuffer.length >= 8) {
        const metaLen = _recvBuffer.readUInt32BE(0);
        const binLen = _recvBuffer.readUInt32BE(4);
        const totalLen = 8 + metaLen + binLen;
        if (_recvBuffer.length < totalLen) {
            break;
        }
        const metaRaw = _recvBuffer.subarray(8, 8 + metaLen).toString('utf-8');
        const binBuf = binLen > 0 ? Buffer.from(_recvBuffer.subarray(8 + metaLen, totalLen)) : null;
        _recvBuffer = _recvBuffer.subarray(totalLen);
        try {
            const msg = JSON.parse(metaRaw);
            if (msg && typeof msg === 'object') {
                _handleIncomingMessage(msg, binBuf);
            }
        } catch (_) { }
    }
}

function _ensureConnected() {
    const target = _getIpcTarget();
    if (_sock && !_sock.destroyed && _connectedTarget === target) {
        return _sock;
    }
    if (_sock) {
        try {
            _sock.destroy();
        } catch (_) { }
        _sock = null;
    }

    _socketClosed = false;
    _hasConnected = false;
    _recvBuffer = Buffer.alloc(0);
    _pendingWrites = 0;
    _connectedTarget = target;

    let sock;
    if (target.startsWith('tcp://')) {
        const hostPort = target.slice(6);
        const sepIdx = hostPort.lastIndexOf(':');
        const host = sepIdx !== -1 ? hostPort.slice(0, sepIdx) : hostPort;
        const port = sepIdx !== -1 ? Number(hostPort.slice(sepIdx + 1)) : 0;
        sock = net.createConnection({ host, port });
    } else {
        sock = net.createConnection(target);
    }

    _sock = sock;

    sock.on('connect', () => {
        if (_sock !== sock) return;
        _hasConnected = true;
    });

    sock.on('data', (chunk) => {
        if (_sock !== sock) return;
        _onSocketData(chunk);
    });

    sock.on('end', () => {
        if (_sock !== sock) return;
        if (_hasConnected) {
            _socketClosed = true;
        }
        _notifyListeners();
    });

    sock.on('close', () => {
        if (_sock !== sock) return;
        if (_hasConnected) {
            _socketClosed = true;
        }
        _pendingWrites = 0;
        _sock = null;
        _notifyListeners();
    });

    sock.on('error', (err) => {
        if (_sock !== sock) return;
        if (_hasConnected) {
            _socketClosed = true;
        } else {
            console.error('Failed to connect to ECHO IPC Socket:', err.message || err);
        }
        _pendingWrites = 0;
        _notifyListeners();
        _updateSocketRef();
    });

    _updateSocketRef();
    return sock;
}

function _toBuffer(binary) {
    if (binary === null || binary === undefined) {
        return Buffer.alloc(0);
    }
    if (Buffer.isBuffer(binary)) {
        return binary;
    }
    if (binary instanceof Uint8Array || binary instanceof ArrayBuffer) {
        return Buffer.from(binary);
    }
    return Buffer.alloc(0);
}

function _send_ipc_frame(meta, binary = null) {
    const metaBuf = Buffer.from(JSON.stringify(meta), 'utf-8');
    const binBuf = _toBuffer(binary);
    const header = Buffer.allocUnsafe(8);
    header.writeUInt32BE(metaBuf.length, 0);
    header.writeUInt32BE(binBuf.length, 4);
    const frame = Buffer.concat([header, metaBuf, binBuf]);

    const sock = _ensureConnected();
    if (!sock || sock.destroyed) return;

    _pendingWrites += 1;
    _updateSocketRef();
    sock.write(frame, () => {
        _pendingWrites = Math.max(0, _pendingWrites - 1);
        _updateSocketRef();
    });
}

const send_ipc_frame = _send_ipc_frame;

function display(htmlContent, title = 'ECHO Sandbox Monitor', width = '100%', height = '400px', window_id = 'default') {
    const meta = {
        type: 'display',
        window_id: window_id,
        title: title,
        width: width,
        height: height
    };
    _send_ipc_frame(meta, Buffer.from(String(htmlContent), 'utf-8'));
}

function push_event(data, window_id = 'default', req_id = null, binary = null) {
    const meta = {
        type: 'event',
        window_id: window_id,
        req_id: req_id,
        data: data
    };
    _send_ipc_frame(meta, binary);
}

function push_ready(window_id = 'default', actions = []) {
    const meta = {
        type: 'ready',
        window_id: window_id,
        actions: Array.isArray(actions) ? [...actions] : []
    };
    _send_ipc_frame(meta, null);
}

function get_ui_payload(window_id = 'default') {
    if (Object.prototype.hasOwnProperty.call(_uiPayloads, window_id)) {
        const val = _uiPayloads[window_id];
        return val && typeof val === 'object' ? val : null;
    }
    return null;
}

function get_ui_requests(window_id = null) {
    if (_rpcQueue.length === 0) return [];
    if (window_id === null || window_id === undefined) {
        return _rpcQueue.splice(0, _rpcQueue.length);
    }
    const matched = [];
    const remaining = [];
    for (const item of _rpcQueue) {
        if ((item.window_id || 'default') === window_id) {
            matched.push(item);
        } else {
            remaining.push(item);
        }
    }
    _rpcQueue.length = 0;
    for (const item of remaining) {
        _rpcQueue.push(item);
    }
    return matched;
}

function is_window_closed(window_id = 'default') {
    if (_socketClosed || _closedWindows.has(window_id)) {
        return true;
    }
    const payload = get_ui_payload(window_id);
    if (payload && typeof payload === 'object') {
        return Boolean(payload._is_closed);
    }
    return false;
}

function get_app_uds() {
    return process.env.ECHO_APP_UDS || '/run/echo_app/http.sock';
}

function get_app_port() {
    const raw = process.env.ECHO_APP_PORT;
    const parsed = raw ? Number(raw) : 8000;
    return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : 8000;
}

function wait_until_closed(window_id = 'default') {
    _ensureConnected();
    _keepAliveRefCount += 1;
    _updateSocketRef();

    return new Promise((resolve) => {
        let finished = false;
        const cleanup = () => {
            if (finished) return;
            finished = true;
            _listeners.delete(onEvent);
            _keepAliveRefCount = Math.max(0, _keepAliveRefCount - 1);
            _updateSocketRef();
            resolve();
        };
        const onEvent = () => {
            if (is_window_closed(window_id)) {
                cleanup();
            }
        };
        _listeners.add(onEvent);
        onEvent();
    });
}

function _parseAppTarget(target) {
    if (target === null || target === undefined) {
        return null;
    }
    if (typeof target === 'number' && Number.isFinite(target)) {
        return { kind: 'tcp', port: Math.trunc(target) };
    }
    const tStr = String(target).trim();
    if (tStr.startsWith('tcp:')) {
        return { kind: 'tcp', port: parseInt(tStr.slice(4).trim(), 10) };
    }
    if (
        tStr.startsWith('http://127.0.0.1:') ||
        tStr.startsWith('https://127.0.0.1:') ||
        tStr.startsWith('http://localhost:') ||
        tStr.startsWith('https://localhost:')
    ) {
        const hostPart = tStr.split('://', 2)[1].split('/', 1)[0];
        return { kind: 'tcp', port: parseInt(hostPart.slice(hostPart.lastIndexOf(':') + 1), 10) };
    }
    if (/^\d+$/.test(tStr)) {
        return { kind: 'tcp', port: parseInt(tStr, 10) };
    }
    if (tStr.startsWith('uds:')) {
        return { kind: 'uds', path: tStr.slice(4).trim() };
    }
    if (tStr.endsWith('.sock') || tStr.includes('/') || tStr.includes('\\')) {
        return { kind: 'uds', path: tStr };
    }
    throw new Error(`Cible expose_app invalide: ${String(target)}`);
}

function _probeSocket(connectOpts) {
    return new Promise((resolve) => {
        let settled = false;
        const done = (ok) => {
            if (settled) return;
            settled = true;
            try {
                sock.destroy();
            } catch (_) { }
            resolve(ok);
        };
        let sock;
        try {
            sock = net.createConnection(connectOpts);
        } catch (_) {
            resolve(false);
            return;
        }
        sock.setTimeout(500);
        sock.once('connect', () => done(true));
        sock.once('timeout', () => done(false));
        sock.once('error', () => done(false));
    });
}

async function _probeUds(udsPath) {
    if (!udsPath || !fs.existsSync(udsPath)) {
        return false;
    }
    return _probeSocket(udsPath);
}

async function _probeTcp(port) {
    if (!Number.isFinite(port) || port <= 0) {
        return false;
    }
    return _probeSocket({ host: '127.0.0.1', port });
}

async function expose_app(
    optionsOrTarget = null,
    title = 'Application Sandbox',
    width = '100%',
    height = '600px',
    window_id = 'default',
    health_path = '/',
    wait_timeout = 15.0,
    block = true
) {
    let target = optionsOrTarget === undefined ? null : optionsOrTarget;
    if (optionsOrTarget !== null && typeof optionsOrTarget === 'object' && !Array.isArray(optionsOrTarget)) {
        const opts = optionsOrTarget;
        target = opts.target !== undefined ? opts.target : null;
        title = opts.title !== undefined ? opts.title : 'Application Sandbox';
        width = opts.width !== undefined ? opts.width : '100%';
        height = opts.height !== undefined ? opts.height : '600px';
        window_id = opts.window_id !== undefined ? opts.window_id : 'default';
        health_path = opts.health_path !== undefined ? opts.health_path : '/';
        wait_timeout = opts.wait_timeout !== undefined ? opts.wait_timeout : 15.0;
        block = opts.block !== undefined ? Boolean(opts.block) : true;
    }

    const parsed = _parseAppTarget(target);
    const timeoutMs = Math.max(0, Number(wait_timeout) || 0) * 1000;
    const deadline = Date.now() + timeoutMs;
    let resolvedTarget = null;

    while (true) {
        if (parsed === null) {
            const udsPath = get_app_uds();
            if (await _probeUds(udsPath)) {
                resolvedTarget = `uds:${udsPath}`;
                break;
            }
            const port = get_app_port();
            if (await _probeTcp(port)) {
                resolvedTarget = `tcp:${port}`;
                break;
            }
        } else if (parsed.kind === 'uds') {
            if (await _probeUds(parsed.path)) {
                resolvedTarget = `uds:${parsed.path}`;
                break;
            }
        } else if (await _probeTcp(parsed.port)) {
            resolvedTarget = `tcp:${parsed.port}`;
            break;
        }

        if (Date.now() >= deadline) {
            const err = new Error(
                `TimeoutError: Serveur sandbox injoignable après ${wait_timeout}s ` +
                `(ECHO_APP_UDS=${get_app_uds()}, ECHO_APP_PORT=${get_app_port()}, target=${String(target)}).`
            );
            err.name = 'TimeoutError';
            throw err;
        }
        await new Promise((r) => setTimeout(r, 50));
    }

    _send_ipc_frame(
        {
            type: 'expose_app',
            window_id: window_id,
            title: title,
            width: width,
            height: height,
            target: resolvedTarget,
            health_path: health_path
        },
        null
    );
    push_ready(window_id, []);
    if (block) {
        await wait_until_closed(window_id);
    }
    return resolvedTarget;
}

class BidiRouter {
    constructor(window_id = 'default', maxWorkers = 4) {
        this.window_id = window_id;
        this.maxWorkers = Math.max(1, Number(maxWorkers) || 4);
        this._routes = {};
        this._routeAcceptsBinary = {};
        this._activeCount = 0;
        this._queue = [];
    }

    route(action, fn) {
        this._routes[action] = fn;
        this._routeAcceptsBinary[action] = typeof fn === 'function' && fn.length >= 2;
        return this;
    }

    async _executeRequest(req) {
        const action = req.action;
        const reqId = req.req_id !== undefined ? req.req_id : null;
        const payload = req.payload;
        try {
            if (typeof this._routes[action] === 'function') {
                const acceptsBinary = Boolean(this._routeAcceptsBinary[action]);
                const res = acceptsBinary
                    ? await this._routes[action](payload, req.binary !== undefined ? req.binary : null)
                    : await this._routes[action](payload);
                let resData = res;
                let resBytes = null;
                if (Buffer.isBuffer(res)) {
                    resData = null;
                    resBytes = res;
                } else if (
                    res &&
                    typeof res === 'object' &&
                    (Buffer.isBuffer(res.__echo_binary__) || res.__echo_binary__ instanceof Uint8Array)
                ) {
                    resData = res.data !== undefined ? res.data : null;
                    resBytes = Buffer.from(res.__echo_binary__);
                }
                push_event({ status: 'ok', action: action, result: resData }, this.window_id, reqId, resBytes);
            } else if (action === '__echo_ping__') {
                push_event(
                    {
                        status: 'ok',
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
                        status: 'error',
                        action: action,
                        error: `Unknown RPC action: ${action}`,
                        available_actions: Object.keys(this._routes).sort()
                    },
                    this.window_id,
                    reqId
                );
            }
        } catch (err) {
            push_event(
                { status: 'error', action: action, error: String(err && err.message ? err.message : err) },
                this.window_id,
                reqId
            );
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
        _ensureConnected();
        _keepAliveRefCount += 1;
        _updateSocketRef();
        push_ready(this.window_id, Object.keys(this._routes).sort());

        return new Promise((resolve) => {
            let finished = false;
            let safetyTimer = null;

            const cleanup = () => {
                if (finished) return;
                finished = true;
                _listeners.delete(onEvent);
                if (safetyTimer) {
                    clearInterval(safetyTimer);
                    safetyTimer = null;
                }
                _keepAliveRefCount = Math.max(0, _keepAliveRefCount - 1);
                _updateSocketRef();
                resolve();
            };

            const onEvent = () => {
                this.dispatch_once(true);
                if (is_window_closed(this.window_id)) {
                    cleanup();
                }
            };

            _listeners.add(onEvent);
            safetyTimer = setInterval(onEvent, Math.max(50, Number(pollIntervalMs) || 500));
            if (typeof safetyTimer.unref === 'function') {
                safetyTimer.unref();
            }
            onEvent();
        });
    }
}

module.exports = {
    display,
    push_event,
    push_ready,
    get_ui_payload,
    get_ui_requests,
    is_window_closed,
    get_app_uds,
    get_app_port,
    wait_until_closed,
    expose_app,
    BidiRouter,
    _send_ipc_frame,
    send_ipc_frame
};
