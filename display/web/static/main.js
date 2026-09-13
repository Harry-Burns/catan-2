/* ------------------------------------------------------------------ *
 *  main.js -- polling, controls, overlay toggles, keyboard.
 *
 *  The page polls /state/version (a few bytes) on a short interval and only
 *  refetches the full board when the version moves. Runner status rides along
 *  with the version response, so the control bar stays live even while the
 *  board is unchanged.
 * ------------------------------------------------------------------ */

(() => {
    'use strict';

    const POLL_MS = 130;
    const HIDDEN_POLL_MS = 1500;
    const STALE_MS = 2500;      // no runner heartbeat for this long -> offline
    const STORE_KEY = 'catan.inspector.opts.v1';

    const DEFAULT_OPTS = {
        ids: { hexes: false, nodes: false, edges: false, ports: false },
        sites: false, legal: true, ownership: false, inspect: true,
        spoilers: false, log: false,
    };

    let opts = loadOpts();
    let state = null;
    let lastVersion = -1;
    let runner = { connected: false, playing: false, delay: 0.5, steps: 0, finished: false };
    let runnerStale = true;
    const logFilters = new Set(['setup', 'roll', 'build', 'dev', 'trade', 'robber', 'turn']);

    // --- Options persistence --------------------------------------------- //
    function loadOpts() {
        try {
            const raw = localStorage.getItem(STORE_KEY);
            if (!raw) return structuredClone(DEFAULT_OPTS);
            const saved = JSON.parse(raw);
            return { ...structuredClone(DEFAULT_OPTS), ...saved, ids: { ...DEFAULT_OPTS.ids, ...(saved.ids || {}) } };
        } catch (_) {
            return structuredClone(DEFAULT_OPTS);
        }
    }

    function saveOpts() {
        try { localStorage.setItem(STORE_KEY, JSON.stringify(opts)); } catch (_) { /* private mode */ }
    }

    // --- Server calls ------------------------------------------------------ //
    async function sendCommand(cmd, value = 0) {
        try {
            await fetch('/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cmd, value }),
            });
        } catch (err) {
            console.warn('control failed', err);
        }
    }

    async function poll() {
        try {
            const resp = await fetch('/state/version', { cache: 'no-store' });
            if (!resp.ok) throw new Error(resp.status);
            const info = await resp.json();

            runner = info.runner || runner;
            runnerStale = (info.runner_age === null || info.runner_age === undefined)
                ? true : (info.runner_age * 1000 > STALE_MS);
            paintControls();

            if (info.version !== lastVersion) {
                lastVersion = info.version;
                const full = await fetch('/state', { cache: 'no-store' });
                if (full.ok) {
                    state = await full.json();
                    renderAll();
                }
            }
        } catch (err) {
            runnerStale = true;
            paintControls();
        }
    }

    // --- Rendering ---------------------------------------------------------- //
    function renderAll() {
        if (!state) return;
        Board.render(state, opts, {
            onHover: (ref, evt) => {
                if (!opts.inspect) return;
                showInspector(ref, evt);
            },
            onLeave: () => hideInspector(),
        });
        Panels.renderStatus(state);
        Panels.renderPlayers(state);
        Panels.renderBank(state, opts.spoilers);
        Panels.renderLegal(state);
        Panels.renderTrade(state);
        Panels.renderLog(state, logFilters);
        paintLegend();
    }

    function rerenderBoardOnly() {
        if (!state) return;
        Board.render(state, opts, {
            onHover: (ref, evt) => {
                if (!opts.inspect) return;
                showInspector(ref, evt);
            },
            onLeave: () => hideInspector(),
        });
        paintLegend();
    }

    // The legend only lists the overlays that are actually switched on,
    // otherwise it advertises colours that aren't on the board.
    function paintLegend() {
        const box = document.getElementById('board-legend');
        const rows = [];
        if (opts.legal) {
            rows.push(['#37d399', 'legal settlement / road']);
            rows.push(['#f6c454', 'legal city upgrade']);
            rows.push(['#f2606b', 'legal robber hex']);
        }
        if (opts.ownership) {
            rows.push(['#8d9bad', 'hex ring / road glow = owner']);
        }
        box.innerHTML = rows
            .map(([c, label]) => `<div class="row"><span class="sw" style="background:${c}"></span>${label}</div>`)
            .join('');
        box.classList.toggle('show', rows.length > 0);
    }

    // --- Inspector ------------------------------------------------------------ //
    const inspector = () => document.getElementById('inspector');

    function showInspector(ref, evt) {
        const box = inspector();
        const html = Panels.inspectorHTML(ref, state);
        if (!html) { hideInspector(); return; }
        box.innerHTML = html;
        box.classList.add('show');

        // Keep the card on screen near the cursor.
        const pad = 14;
        const rect = box.getBoundingClientRect();
        let x = evt.clientX + pad;
        let y = evt.clientY + pad;
        if (x + rect.width > window.innerWidth - 8) x = evt.clientX - rect.width - pad;
        if (y + rect.height > window.innerHeight - 8) y = evt.clientY - rect.height - pad;
        box.style.left = `${Math.max(8, x)}px`;
        box.style.top = `${Math.max(8, y)}px`;
    }

    function hideInspector() { inspector().classList.remove('show'); }

    // --- Control bar ------------------------------------------------------------ //
    function paintControls() {
        const offline = runnerStale || !runner.connected;
        const playBtn = document.getElementById('btn-play');
        playBtn.textContent = runner.playing ? 'Pause' : 'Play';
        playBtn.classList.toggle('primary', !runner.playing);

        document.getElementById('btn-step').disabled = offline || runner.finished;
        document.getElementById('btn-step10').disabled = offline || runner.finished;
        playBtn.disabled = offline || runner.finished;
        document.getElementById('btn-restart').disabled = offline;

        const status = document.getElementById('runner-status');
        if (offline) {
            status.className = 'chip offline';
            status.innerHTML = 'Runner offline';
            status.title = 'Nothing is driving the game. Start it with: python run_game.py';
        } else {
            status.className = 'chip';
            const label = runner.finished ? 'finished' : runner.playing ? 'playing' : 'paused';
            status.innerHTML = `Runner <b>${label}</b> &middot; move ${runner.steps}` +
                (runner.seed !== null && runner.seed !== undefined ? ` &middot; seed ${runner.seed}` : '');
            status.title = (runner.players || []).length
                ? `Controllers: ${runner.players.join(', ')}` : '';
        }

        const slider = document.getElementById('speed-slider');
        if (document.activeElement !== slider) slider.value = String(runner.delay);
        document.getElementById('speed-val').textContent = `${Number(runner.delay).toFixed(2)}s`;
    }

    // --- Toggles ------------------------------------------------------------------ //
    function optValue(path) {
        return path.startsWith('ids.') ? opts.ids[path.slice(4)] : opts[path];
    }

    function setOpt(path, value) {
        if (path.startsWith('ids.')) opts.ids[path.slice(4)] = value;
        else opts[path] = value;
        saveOpts();
    }

    function toggleOpt(path) {
        setOpt(path, !optValue(path));
        paintToggles();
        if (path === 'log') { paintDrawer(); return; }
        if (path === 'spoilers') { if (state) Panels.renderBank(state, opts.spoilers); return; }
        if (path === 'inspect') { hideInspector(); }
        rerenderBoardOnly();
    }

    function paintToggles() {
        document.querySelectorAll('[data-opt]').forEach(btn => {
            btn.classList.toggle('on', !!optValue(btn.dataset.opt));
        });
    }

    function paintDrawer() {
        document.getElementById('log-drawer').classList.toggle('open', opts.log);
        document.getElementById('scrim').classList.toggle('show', opts.log);
        document.body.classList.toggle('log-open', opts.log);
        if (opts.log && state) Panels.renderLog(state, logFilters);
    }

    // --- Wiring ---------------------------------------------------------------------- //
    function wireUp() {
        Board.initCamera(
            document.getElementById('board-svg'),
            document.getElementById('zoom-val'),
            () => hideInspector(),
        );

        document.getElementById('btn-step').onclick = () => sendCommand('step', 1);
        document.getElementById('btn-step10').onclick = () => sendCommand('step', 10);
        document.getElementById('btn-play').onclick = () => sendCommand(runner.playing ? 'pause' : 'play');
        document.getElementById('btn-restart').onclick = () => {
            if (confirm('Abandon this game and deal a new board?')) sendCommand('restart');
        };

        const slider = document.getElementById('speed-slider');
        slider.oninput = () => {
            document.getElementById('speed-val').textContent = `${Number(slider.value).toFixed(2)}s`;
        };
        slider.onchange = () => sendCommand('speed', Number(slider.value));

        document.querySelectorAll('[data-opt]').forEach(btn => {
            btn.onclick = () => toggleOpt(btn.dataset.opt);
        });

        document.getElementById('zoom-in').onclick = () => Board.zoomBy(1.2, 0, 0);
        document.getElementById('zoom-out').onclick = () => Board.zoomBy(1 / 1.2, 0, 0);
        document.getElementById('zoom-reset').onclick = () => Board.resetCamera();

        document.getElementById('log-close').onclick = () => toggleOpt('log');
        document.getElementById('scrim').onclick = () => toggleOpt('log');

        document.querySelectorAll('[data-filter]').forEach(btn => {
            btn.classList.toggle('on', logFilters.has(btn.dataset.filter));
            btn.onclick = () => {
                const cat = btn.dataset.filter;
                if (logFilters.has(cat)) logFilters.delete(cat); else logFilters.add(cat);
                btn.classList.toggle('on', logFilters.has(cat));
                if (state) Panels.renderLog(state, logFilters);
            };
        });

        const sheet = document.getElementById('shortcuts');
        document.getElementById('btn-help').onclick = () => sheet.classList.add('show');
        sheet.onclick = () => sheet.classList.remove('show');

        document.addEventListener('keydown', onKey);
    }

    const KEY_TO_OPT = {
        h: 'ids.hexes', n: 'ids.nodes', e: 'ids.edges', p: 'ids.ports',
        g: 'sites', l: 'legal', o: 'ownership', i: 'inspect', m: 'log', k: 'spoilers',
    };

    function onKey(evt) {
        const tag = (evt.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
        if (evt.ctrlKey || evt.metaKey || evt.altKey) return;

        if (evt.key === 'Escape') {
            document.getElementById('shortcuts').classList.remove('show');
            if (opts.log) toggleOpt('log');
            return;
        }
        if (evt.key === '?') { document.getElementById('shortcuts').classList.toggle('show'); return; }

        const lower = evt.key.toLowerCase();
        if (KEY_TO_OPT[lower] && !evt.shiftKey) {
            evt.preventDefault();
            toggleOpt(KEY_TO_OPT[lower]);
            return;
        }

        switch (evt.key) {
            case ' ':
                evt.preventDefault();
                sendCommand(runner.playing ? 'pause' : 'play');
                break;
            case 'ArrowRight':
                evt.preventDefault();
                sendCommand('step', evt.shiftKey ? 10 : 1);
                break;
            case 's': case 'S':
                sendCommand('step', evt.shiftKey ? 10 : 1);
                break;
            case '+': case '=':
                sendCommand('speed', Math.min(2, Number(runner.delay) + 0.1));
                break;
            case '-': case '_':
                sendCommand('speed', Math.max(0, Number(runner.delay) - 0.1));
                break;
            case '0':
                Board.resetCamera();
                break;
            case 'R':
                if (confirm('Abandon this game and deal a new board?')) sendCommand('restart');
                break;
            default:
                break;
        }
    }

    // --- Boot -------------------------------------------------------------------------- //
    document.addEventListener('DOMContentLoaded', () => {
        wireUp();
        paintToggles();
        paintDrawer();
        paintControls();
        Board.render(null, opts, null);

        // Poll fast while visible, lazily when the tab is in the background.
        let timer = null;
        function schedule() {
            if (timer) clearInterval(timer);
            timer = setInterval(poll, document.hidden ? HIDDEN_POLL_MS : POLL_MS);
        }
        document.addEventListener('visibilitychange', () => {
            schedule();
            if (!document.hidden) poll();
        });
        poll();
        schedule();
    });
})();
