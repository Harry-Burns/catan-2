/* ------------------------------------------------------------------ *
 *  board.js -- SVG rendering of the Catan board.
 *
 *  Geometry note: hex centres are derived from the engine's own (row, col)
 *  grid (catan/topology.py), where a row step is 1 and a column step is 2,
 *  so hex id N here is hex id N there. The server sends every hex's six
 *  corner and edge ids already rotated into *display* order (see the header
 *  of api_adapter.py), so this file never has to know the engine's corner
 *  numbering -- index d is simply the vertex at angle 60d + 30 degrees.
 * ------------------------------------------------------------------ */

const Board = (() => {
    'use strict';

    const NS = 'http://www.w3.org/2000/svg';

    // --- Geometry ---------------------------------------------------- //
    const SIZE = 50;                      // centre -> flat side
    const R = SIZE / Math.cos(Math.PI / 6); // centre -> corner (57.74)
    const HEX_W = 2 * SIZE;
    const ROW_STEP = R * 1.5;

    const SETT_R = SIZE * 0.19;
    const CITY_R = SIZE * 0.20;
    const ROAD_W = SIZE * 0.145;
    const ROBBER_R = SIZE * 0.26;
    const GHOST_R = SIZE * 0.085;

    const ROWS = 5, COLS = 9;
    const EXCLUDED = new Set(['0,0', '0,8', '4,0', '4,8']);
    const NEIGHBOURS = [[-1, 1], [0, 2], [1, 1], [1, -1], [0, -2], [-1, -1]];

    const RES_COLORS = {
        wood: '#2f7d45', brick: '#bd5334', sheep: '#8dc75c',
        wheat: '#e3b53c', ore: '#77869a', desert: '#cdbb8d',
        generic: '#c9d2de', water: '#123c58',
    };
    const PLAYER_COLORS = {
        red: '#ef4444', blue: '#4b8ef7', white: '#dfe6ef', orange: '#f7913a',
    };
    const PORT_ABBR = {
        wood: 'WO', brick: 'BR', sheep: 'SH', wheat: 'WH', ore: 'OR', generic: '3:1',
    };

    function playerColor(name) { return PLAYER_COLORS[name] || '#94a3b8'; }

    // --- Colour helpers ---------------------------------------------- //
    function shade(hex, amount) {
        const n = parseInt(hex.slice(1), 16);
        const clamp = v => Math.max(0, Math.min(255, Math.round(v)));
        const r = clamp(((n >> 16) & 255) + amount);
        const g = clamp(((n >> 8) & 255) + amount);
        const b = clamp((n & 255) + amount);
        return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, '0')}`;
    }

    // --- Element helper ---------------------------------------------- //
    function el(tag, attrs, parent) {
        const node = document.createElementNS(NS, tag);
        if (attrs) {
            for (const k in attrs) {
                if (attrs[k] === null || attrs[k] === undefined) continue;
                if (k === 'text') node.textContent = attrs[k];
                else node.setAttribute(k, attrs[k]);
            }
        }
        if (parent) parent.appendChild(node);
        return node;
    }

    // --- Static layout ------------------------------------------------ //
    function isHex(x, y) {
        if (x < 0 || x >= ROWS || y < 0 || y >= COLS) return false;
        if ((x + y) % 2 !== 0) return false;
        return !EXCLUDED.has(`${x},${y}`);
    }

    // Land hexes, enumerated exactly as generate_topology() does.
    const landCentres = [];
    {
        let id = 0;
        for (let x = 0; x < ROWS; x++) {
            for (let y = 0; y < COLS; y++) {
                if (isHex(x, y)) {
                    landCentres.push({ id: id++, row: x, col: y, x: (y / 2) * HEX_W, y: x * ROW_STEP });
                }
            }
        }
    }

    // The ring of sea hexes touching the island, drawn purely for looks.
    const waterCentres = [];
    for (let x = -1; x <= ROWS; x++) {
        for (let y = -2; y <= COLS + 1; y++) {
            if ((x + y) % 2 !== 0 || isHex(x, y)) continue;
            const touchesLand = NEIGHBOURS.some(([dx, dy]) => isHex(x + dx, y + dy));
            if (touchesLand) waterCentres.push({ x: (y / 2) * HEX_W, y: x * ROW_STEP });
        }
    }

    const VIEW = (() => {
        const all = landCentres.concat(waterCentres);
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        all.forEach(c => {
            minX = Math.min(minX, c.x - SIZE); maxX = Math.max(maxX, c.x + SIZE);
            minY = Math.min(minY, c.y - R);    maxY = Math.max(maxY, c.y + R);
        });
        const pad = SIZE * 0.35;
        return {
            x: minX - pad, y: minY - pad,
            w: (maxX - minX) + 2 * pad, h: (maxY - minY) + 2 * pad,
        };
    })();

    function hexPoints(cx, cy, radius) {
        const pts = [];
        for (let i = 0; i < 6; i++) {
            const a = Math.PI / 3 * i + Math.PI / 6;
            pts.push(`${(cx + radius * Math.cos(a)).toFixed(2)},${(cy + radius * Math.sin(a)).toFixed(2)}`);
        }
        return pts.join(' ');
    }

    function vertexXY(centre, d, radius) {
        const a = Math.PI / 3 * d + Math.PI / 6;
        return { x: centre.x + radius * Math.cos(a), y: centre.y + radius * Math.sin(a) };
    }

    // --- Camera ------------------------------------------------------- //
    const camera = { x: 0, y: 0, k: 1 };
    let svgRoot = null, cameraGroup = null, hud = null;

    function applyCamera() {
        if (!cameraGroup) return;
        cameraGroup.setAttribute('transform',
            `translate(${camera.x.toFixed(2)} ${camera.y.toFixed(2)}) scale(${camera.k.toFixed(4)})`);
        if (hud) hud.textContent = `${Math.round(camera.k * 100)}%`;
    }

    function resetCamera() {
        camera.x = 0; camera.y = 0; camera.k = 1;
        applyCamera();
    }

    function zoomBy(factor, originX, originY) {
        const next = Math.max(0.5, Math.min(5, camera.k * factor));
        const ratio = next / camera.k;
        // Keep the point under the cursor fixed while scaling.
        camera.x = originX - (originX - camera.x) * ratio;
        camera.y = originY - (originY - camera.y) * ratio;
        camera.k = next;
        applyCamera();
    }

    function svgPoint(evt) {
        const rect = svgRoot.getBoundingClientRect();
        const scale = VIEW.w / rect.width;
        return {
            x: (evt.clientX - rect.left) * scale + VIEW.x,
            y: (evt.clientY - rect.top) * scale + VIEW.y,
        };
    }

    function initCamera(svg, hudLabel, onEmptyClick) {
        svgRoot = svg;
        hud = hudLabel;

        svg.addEventListener('wheel', evt => {
            evt.preventDefault();
            const p = svgPoint(evt);
            zoomBy(evt.deltaY < 0 ? 1.12 : 1 / 1.12, p.x, p.y);
        }, { passive: false });

        let dragging = false, last = null, moved = 0;
        svg.addEventListener('pointerdown', evt => {
            if (evt.button !== 0) return;
            dragging = true; moved = 0;
            last = svgPoint(evt);
            svg.classList.add('panning');
            svg.setPointerCapture(evt.pointerId);
        });
        svg.addEventListener('pointermove', evt => {
            if (!dragging) return;
            const p = svgPoint(evt);
            camera.x += p.x - last.x;
            camera.y += p.y - last.y;
            moved += Math.abs(p.x - last.x) + Math.abs(p.y - last.y);
            applyCamera();
            last = p;
        });
        const endDrag = evt => {
            if (!dragging) return;
            dragging = false;
            svg.classList.remove('panning');
            try { svg.releasePointerCapture(evt.pointerId); } catch (_) { /* already gone */ }
            if (moved < 3 && onEmptyClick) onEmptyClick();
        };
        svg.addEventListener('pointerup', endDrag);
        svg.addEventListener('pointercancel', endDrag);
        svg.addEventListener('dblclick', () => resetCamera());
    }

    // --- Defs --------------------------------------------------------- //
    function buildDefs(parent) {
        const defs = el('defs', null, parent);
        Object.entries(RES_COLORS).forEach(([name, base]) => {
            const grad = el('linearGradient', {
                id: `g-${name}`, x1: '0', y1: '0', x2: '0', y2: '1',
            }, defs);
            el('stop', { offset: '0', 'stop-color': shade(base, 26) }, grad);
            el('stop', { offset: '1', 'stop-color': shade(base, -26) }, grad);
        });

        const token = el('radialGradient', { id: 'g-token', cx: '0.5', cy: '0.35', r: '0.75' }, defs);
        el('stop', { offset: '0', 'stop-color': '#fffdf6' }, token);
        el('stop', { offset: '1', 'stop-color': '#ddd3bb' }, token);

        const sea = el('radialGradient', { id: 'g-sea', cx: '0.5', cy: '0.5', r: '0.75' }, defs);
        el('stop', { offset: '0', 'stop-color': '#17496a' }, sea);
        el('stop', { offset: '1', 'stop-color': '#0d2c42' }, sea);

        const glow = el('filter', { id: 'f-glow', x: '-60%', y: '-60%', width: '220%', height: '220%' }, defs);
        el('feGaussianBlur', { stdDeviation: '3', result: 'b' }, glow);
        const merge = el('feMerge', null, glow);
        el('feMergeNode', { in: 'b' }, merge);
        el('feMergeNode', { in: 'SourceGraphic' }, merge);
        return defs;
    }

    // --- Piece drawing ------------------------------------------------ //
    function drawNumberToken(parent, cx, cy, number, pips, dimmed) {
        const g = el('g', { opacity: dimmed ? 0.45 : 1 }, parent);
        el('circle', { cx, cy, r: SIZE * 0.30, fill: 'rgba(0,0,0,0.22)' }, g);
        el('circle', { cx, cy, r: SIZE * 0.285, fill: 'url(#g-token)', stroke: 'rgba(40,30,15,0.45)', 'stroke-width': 1 }, g);

        const hot = number === 6 || number === 8;
        el('text', {
            x: cx, y: cy + SIZE * 0.045, 'text-anchor': 'middle',
            'dominant-baseline': 'middle',
            'font-size': SIZE * (number >= 10 ? 0.30 : 0.34),
            'font-weight': 800, fill: hot ? '#b02a2a' : '#22282f',
            'font-family': 'ui-monospace, Menlo, Consolas, monospace',
            text: String(number),
        }, g);

        // Probability dots, same convention as the physical tokens.
        const dotR = SIZE * 0.021;
        const gap = dotR * 3.1;
        const startX = cx - (pips - 1) * gap / 2;
        for (let i = 0; i < pips; i++) {
            el('circle', {
                cx: startX + i * gap, cy: cy + SIZE * 0.185, r: dotR,
                fill: hot ? '#b02a2a' : '#3a424c',
            }, g);
        }
    }

    function drawRobber(parent, cx, cy) {
        const g = el('g', { class: 'robber-shadow' }, parent);
        const w = ROBBER_R;
        el('path', {
            d: `M ${cx - w} ${cy + w * 1.15}
                Q ${cx - w * 0.9} ${cy - w * 0.15} ${cx - w * 0.42} ${cy - w * 0.5}
                L ${cx + w * 0.42} ${cy - w * 0.5}
                Q ${cx + w * 0.9} ${cy - w * 0.15} ${cx + w} ${cy + w * 1.15} Z`,
            fill: '#14181d', stroke: '#4b5664', 'stroke-width': 1.4, 'stroke-linejoin': 'round',
        }, g);
        el('circle', { cx, cy: cy - w * 0.82, r: w * 0.52, fill: '#14181d', stroke: '#4b5664', 'stroke-width': 1.4 }, g);
        el('circle', { cx: cx - w * 0.17, cy: cy - w * 0.95, r: w * 0.13, fill: 'rgba(255,255,255,0.35)' }, g);
    }

    function drawSettlement(parent, x, y, color, isNew) {
        const s = SETT_R;
        const g = el('g', { class: isNew ? 'pop-in' : null }, parent);
        el('polygon', {
            points: [
                `${x - s},${y + s}`, `${x + s},${y + s}`, `${x + s},${y - s * 0.35}`,
                `${x},${y - s * 1.55}`, `${x - s},${y - s * 0.35}`,
            ].join(' '),
            fill: color, stroke: '#0d1218', 'stroke-width': 1.6, 'stroke-linejoin': 'round',
        }, g);
        el('polyline', {
            points: `${x - s},${y - s * 0.35} ${x},${y - s * 1.55} ${x + s},${y - s * 0.35}`,
            fill: 'none', stroke: 'rgba(255,255,255,0.4)', 'stroke-width': 1.1,
        }, g);
        return g;
    }

    function drawCity(parent, x, y, color, isNew) {
        const c = CITY_R;
        const g = el('g', { class: isNew ? 'pop-in' : null }, parent);
        el('path', {
            d: `M ${x - c * 1.35} ${y + c}
                L ${x - c * 1.35} ${y - c * 0.2}
                L ${x - c * 0.15} ${y - c * 0.2}
                L ${x - c * 0.15} ${y - c * 0.95}
                L ${x + c * 0.62} ${y - c * 1.6}
                L ${x + c * 1.35} ${y - c * 0.95}
                L ${x + c * 1.35} ${y + c} Z`,
            fill: color, stroke: '#0d1218', 'stroke-width': 1.6, 'stroke-linejoin': 'round',
        }, g);
        el('rect', {
            x: x - c * 1.0, y: y + c * 0.1, width: c * 0.45, height: c * 0.9,
            fill: 'rgba(0,0,0,0.3)',
        }, g);
        el('rect', {
            x: x + c * 0.35, y: y - c * 0.72, width: c * 0.42, height: c * 0.55,
            fill: 'rgba(0,0,0,0.3)',
        }, g);
        return g;
    }

    function drawRoad(parent, geom, color, isNew) {
        const g = el('g', { class: isNew ? 'pop-in' : null }, parent);
        const shrink = 0.16;   // pull back from the corners so pieces don't collide
        const dx = geom.x2 - geom.x1, dy = geom.y2 - geom.y1;
        const p = {
            x1: geom.x1 + dx * shrink, y1: geom.y1 + dy * shrink,
            x2: geom.x2 - dx * shrink, y2: geom.y2 - dy * shrink,
        };
        el('line', { ...p, stroke: '#0d1218', 'stroke-width': ROAD_W + 3.2, 'stroke-linecap': 'round' }, g);
        el('line', { ...p, stroke: color, 'stroke-width': ROAD_W, 'stroke-linecap': 'round' }, g);
        el('line', {
            x1: p.x1, y1: p.y1 - ROAD_W * 0.22, x2: p.x2, y2: p.y2 - ROAD_W * 0.22,
            stroke: 'rgba(255,255,255,0.28)', 'stroke-width': ROAD_W * 0.28, 'stroke-linecap': 'round',
        }, g);
        return g;
    }

    // --- Main render -------------------------------------------------- //
    // Remembers which pieces existed last frame so new ones can animate in.
    let seenPieces = new Set();
    let lastBoardId = null;

    function render(state, opts, handlers) {
        const svg = document.getElementById('board-svg');
        svg.setAttribute('viewBox', `${VIEW.x.toFixed(2)} ${VIEW.y.toFixed(2)} ${VIEW.w.toFixed(2)} ${VIEW.h.toFixed(2)}`);
        while (svg.firstChild) svg.removeChild(svg.firstChild);

        buildDefs(svg);
        const cam = el('g', { id: 'board-camera' }, svg);
        cameraGroup = cam;
        applyCamera();

        if (!state || !state.hexes || !state.hexes.length) {
            el('text', {
                x: VIEW.x + VIEW.w / 2, y: VIEW.y + VIEW.h / 2, 'text-anchor': 'middle',
                fill: '#8d9bad', 'font-size': SIZE * 0.35,
                text: 'Waiting for the runner to publish a board...',
            }, cam);
            return;
        }

        const boardId = state.game_info ? state.game_info.board_id : null;
        if (boardId !== lastBoardId) { seenPieces = new Set(); lastBoardId = boardId; }
        const nowPieces = new Set();

        // Layer groups, back to front.
        const lSea = el('g', null, cam);
        const lHex = el('g', null, cam);
        const lTint = el('g', null, cam);
        const lHexId = el('g', null, cam);
        const lOwnRoad = el('g', null, cam);
        const lGhostEdge = el('g', null, cam);
        const lRoad = el('g', null, cam);
        const lLegal = el('g', null, cam);
        const lGhostNode = el('g', null, cam);
        const lNode = el('g', null, cam);
        const lPort = el('g', null, cam);
        const lLabel = el('g', null, cam);
        const lHit = el('g', null, cam);

        // ---- sea ring ------------------------------------------------- //
        waterCentres.forEach(c => {
            el('polygon', {
                points: hexPoints(c.x, c.y, R * 0.995),
                fill: 'url(#g-sea)', stroke: '#0a2133', 'stroke-width': 1,
            }, lSea);
        });

        // ---- index the data ------------------------------------------- //
        const hexById = new Map(state.hexes.map(h => [h.id, h]));
        const nodeById = new Map((state.nodes || []).map(n => [n.id, n]));
        const edgeById = new Map((state.edges || []).map(e => [e.id, e]));

        // Screen position of every node id and edge id, from hex geometry.
        const nodePos = new Map();
        const edgeGeom = new Map();
        landCentres.forEach(centre => {
            const hex = hexById.get(centre.id);
            if (!hex) return;
            for (let d = 0; d < 6; d++) {
                const nid = hex.nodes && hex.nodes[d];
                if (nid !== undefined && nid >= 0 && !nodePos.has(nid)) {
                    nodePos.set(nid, vertexXY(centre, d, R));
                }
                const eid = hex.edges && hex.edges[d];
                if (eid !== undefined && eid >= 0 && !edgeGeom.has(eid)) {
                    const a = vertexXY(centre, d, R);
                    const b = vertexXY(centre, (d + 1) % 6, R);
                    edgeGeom.set(eid, { x1: a.x, y1: a.y, x2: b.x, y2: b.y, hex: centre, d });
                }
            }
        });

        const legal = state.legal || {};
        const inSetup = state.game_info && state.game_info.in_setup;
        const legalNodes = new Set(inSetup ? (legal.setup_nodes || []) : (legal.settlement_nodes || []));
        const legalCities = new Set(legal.city_nodes || []);
        const legalEdges = new Set(inSetup ? (legal.setup_edges || []) : (legal.road_edges || []));
        const legalHexes = new Set(legal.robber_hexes || []);

        // ---- hexes ---------------------------------------------------- //
        landCentres.forEach(centre => {
            const hex = hexById.get(centre.id);
            if (!hex) return;
            const blocked = hex.hasRobber;

            el('polygon', {
                points: hexPoints(centre.x, centre.y, R * 0.99),
                fill: `url(#g-${hex.resource})`,
                stroke: '#0e1620', 'stroke-width': 1.6, 'stroke-linejoin': 'round',
                class: 'hex-face',
                opacity: blocked ? 0.62 : 1,
            }, lHex);

            if (hex.number) drawNumberToken(lHex, centre.x, centre.y, hex.number, hex.pips, blocked);
            if (blocked) drawRobber(lHex, centre.x, centre.y + SIZE * (hex.number ? 0.6 : 0.1));

            if (opts.legal && legalHexes.has(hex.id)) {
                el('polygon', {
                    points: hexPoints(centre.x, centre.y, R * 0.84),
                    fill: 'none', stroke: '#f2606b', 'stroke-width': 2.4,
                    'stroke-dasharray': '7 5', class: 'pulse',
                }, lLegal);
            }

            if (opts.ids.hexes) {
                const label = el('g', null, lHexId);
                el('rect', {
                    x: centre.x - SIZE * 0.30, y: centre.y - R * 0.78,
                    width: SIZE * 0.60, height: SIZE * 0.26, rx: 4,
                    fill: 'rgba(8,12,18,0.78)', stroke: 'rgba(78,161,255,0.5)', 'stroke-width': 0.8,
                }, label);
                el('text', {
                    x: centre.x, y: centre.y - R * 0.78 + SIZE * 0.13,
                    'text-anchor': 'middle', 'dominant-baseline': 'middle',
                    'font-size': SIZE * 0.19, fill: '#8fc6ff', class: 'id-label',
                    text: `H${hex.id}`,
                }, label);
            }

            // Hover target for the hex face.
            const hit = el('polygon', {
                points: hexPoints(centre.x, centre.y, R * 0.99), class: 'hit',
            }, lHit);
            wire(hit, { kind: 'hex', id: hex.id }, handlers);
        });

        // ---- ownership tint ------------------------------------------- //
        if (opts.ownership) {
            landCentres.forEach(centre => {
                const hex = hexById.get(centre.id);
                if (!hex || !hex.nodes) return;
                const score = {};
                hex.nodes.forEach(nid => {
                    const n = nodeById.get(nid);
                    if (n && n.owner) score[n.owner] = (score[n.owner] || 0) + (n.type === 'city' ? 2 : 1);
                });
                const ranked = Object.entries(score).sort((a, b) => b[1] - a[1]);
                if (!ranked.length) return;
                // An inset ring rather than a fill: tinting the whole face
                // makes the resource colour and the number token unreadable.
                ranked.slice(0, 2).forEach(([owner, weight], i) => {
                    el('polygon', {
                        points: hexPoints(centre.x, centre.y, R * (0.86 - i * 0.09)),
                        fill: 'none',
                        stroke: playerColor(owner),
                        'stroke-width': Math.min(5, 1.8 + weight),
                        'stroke-linejoin': 'round',
                        opacity: 0.5,
                    }, lTint);
                });
            });
        }

        // ---- edges ----------------------------------------------------- //
        const showSites = opts.sites || opts.ids.edges || opts.legal;
        (state.edges || []).forEach(edge => {
            const geom = edgeGeom.get(edge.id);
            if (!geom) return;

            if (edge.owner) {
                const key = `E${edge.id}:${edge.owner}`;
                nowPieces.add(key);
                drawRoad(lRoad, geom, playerColor(edge.owner), !seenPieces.has(key));

                if (opts.ownership) {
                    el('line', {
                        x1: geom.x1, y1: geom.y1, x2: geom.x2, y2: geom.y2,
                        stroke: playerColor(edge.owner), 'stroke-width': ROAD_W * 3,
                        'stroke-linecap': 'round', opacity: 0.18,
                    }, lOwnRoad);
                }
            } else if (showSites) {
                el('line', {
                    x1: geom.x1, y1: geom.y1, x2: geom.x2, y2: geom.y2,
                    stroke: 'rgba(220,235,255,0.16)', 'stroke-width': ROAD_W * 0.4,
                    'stroke-linecap': 'round', 'stroke-dasharray': '3 4',
                }, lGhostEdge);
            }

            if (opts.legal && legalEdges.has(edge.id)) {
                el('line', {
                    x1: geom.x1, y1: geom.y1, x2: geom.x2, y2: geom.y2,
                    stroke: '#37d399', 'stroke-width': ROAD_W * 0.9,
                    'stroke-linecap': 'round', 'stroke-dasharray': '5 4', class: 'pulse',
                }, lLegal);
            }

            if (opts.ids.edges) {
                const mx = (geom.x1 + geom.x2) / 2, my = (geom.y1 + geom.y2) / 2;
                el('circle', { cx: mx, cy: my, r: SIZE * 0.115, fill: 'rgba(8,12,18,0.82)', stroke: 'rgba(247,145,58,0.55)', 'stroke-width': 0.7 }, lLabel);
                el('text', {
                    x: mx, y: my, 'text-anchor': 'middle', 'dominant-baseline': 'middle',
                    'font-size': SIZE * 0.13, fill: '#ffc48f', class: 'id-label',
                    text: String(edge.id),
                }, lLabel);
            }

            const hit = el('line', {
                x1: geom.x1, y1: geom.y1, x2: geom.x2, y2: geom.y2,
                class: 'hit', 'stroke-width': ROAD_W * 2.2, 'stroke-linecap': 'round',
            }, lHit);
            wire(hit, { kind: 'edge', id: edge.id }, handlers);
        });

        // ---- nodes ------------------------------------------------------ //
        (state.nodes || []).forEach(node => {
            const p = nodePos.get(node.id);
            if (!p) return;

            if (node.owner) {
                const key = `N${node.id}:${node.owner}:${node.type}`;
                nowPieces.add(key);
                const isNew = !seenPieces.has(key);
                if (node.type === 'city') drawCity(lNode, p.x, p.y, playerColor(node.owner), isNew);
                else drawSettlement(lNode, p.x, p.y, playerColor(node.owner), isNew);

                if (opts.ownership) {
                    el('circle', {
                        cx: p.x, cy: p.y, r: SETT_R * 2.1, fill: 'none',
                        stroke: playerColor(node.owner), 'stroke-width': 1.6, opacity: 0.4,
                    }, lOwnRoad);
                }
            } else if (showSites || opts.ids.nodes) {
                el('circle', {
                    cx: p.x, cy: p.y, r: GHOST_R,
                    fill: 'rgba(220,235,255,0.22)', stroke: 'rgba(220,235,255,0.3)', 'stroke-width': 0.7,
                }, lGhostNode);
            }

            if (opts.legal && legalNodes.has(node.id)) {
                el('circle', {
                    cx: p.x, cy: p.y, r: SETT_R * 1.75, fill: 'none',
                    stroke: '#37d399', 'stroke-width': 2.2, 'stroke-dasharray': '4 3', class: 'pulse',
                }, lLegal);
            }
            if (opts.legal && legalCities.has(node.id)) {
                el('circle', {
                    cx: p.x, cy: p.y, r: SETT_R * 2.3, fill: 'none',
                    stroke: '#f6c454', 'stroke-width': 2.2, 'stroke-dasharray': '4 3', class: 'pulse',
                }, lLegal);
            }

            if (opts.ids.nodes) {
                el('circle', {
                    cx: p.x, cy: p.y - SIZE * 0.30, r: SIZE * 0.125,
                    fill: 'rgba(8,12,18,0.85)', stroke: 'rgba(55,211,153,0.55)', 'stroke-width': 0.7,
                }, lLabel);
                el('text', {
                    x: p.x, y: p.y - SIZE * 0.30, 'text-anchor': 'middle', 'dominant-baseline': 'middle',
                    'font-size': SIZE * 0.14, fill: '#8ff0c8', class: 'id-label',
                    text: String(node.id),
                }, lLabel);
            }

            const hit = el('circle', { cx: p.x, cy: p.y, r: SIZE * 0.22, class: 'hit' }, lHit);
            wire(hit, { kind: 'node', id: node.id }, handlers);
        });

        // ---- ports ------------------------------------------------------ //
        (state.ports || []).forEach(port => {
            const centre = landCentres[port.location.hexId];
            if (!centre) return;
            const a = vertexXY(centre, port.location.edge, R);
            const b = vertexXY(centre, (port.location.edge + 1) % 6, R);
            const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
            // Push the badge out into the water, away from the hex centre.
            const len = Math.hypot(mx - centre.x, my - centre.y) || 1;
            const ox = mx + (mx - centre.x) / len * SIZE * 0.52;
            const oy = my + (my - centre.y) / len * SIZE * 0.52;

            const g = el('g', null, lPort);
            [a, b].forEach(corner => {
                el('line', {
                    x1: ox, y1: oy, x2: corner.x, y2: corner.y,
                    stroke: 'rgba(220,200,160,0.55)', 'stroke-width': 2.2, 'stroke-linecap': 'round',
                }, g);
            });

            const isGeneric = port.ratio === 3;
            el('circle', {
                cx: ox, cy: oy, r: SIZE * 0.26,
                fill: isGeneric ? '#e8eef6' : shade(RES_COLORS[port.type] || '#ccc', 30),
                stroke: '#10161f', 'stroke-width': 1.6,
            }, g);
            el('text', {
                x: ox, y: oy - SIZE * 0.045, 'text-anchor': 'middle', 'dominant-baseline': 'middle',
                'font-size': SIZE * 0.15, 'font-weight': 800, fill: '#1a2029',
                'font-family': 'ui-monospace, Menlo, Consolas, monospace',
                text: isGeneric ? '3:1' : '2:1',
            }, g);
            el('text', {
                x: ox, y: oy + SIZE * 0.115, 'text-anchor': 'middle', 'dominant-baseline': 'middle',
                'font-size': SIZE * 0.115, 'font-weight': 800, fill: 'rgba(26,32,41,0.8)',
                'font-family': 'ui-monospace, Menlo, Consolas, monospace',
                text: isGeneric ? 'ANY' : PORT_ABBR[port.type] || '',
            }, g);

            if (opts.ids.ports) {
                el('text', {
                    x: ox, y: oy - SIZE * 0.40, 'text-anchor': 'middle',
                    'font-size': SIZE * 0.15, fill: '#c9a6ff', class: 'id-label',
                    text: `P${port.id}`,
                }, lLabel);
            }

            const hit = el('circle', { cx: ox, cy: oy, r: SIZE * 0.3, class: 'hit' }, lHit);
            wire(hit, { kind: 'port', id: port.id }, handlers);
        });

        seenPieces = nowPieces;
    }

    function wire(node, ref, handlers) {
        if (!handlers) return;
        node.addEventListener('mouseenter', e => handlers.onHover && handlers.onHover(ref, e));
        node.addEventListener('mousemove', e => handlers.onHover && handlers.onHover(ref, e));
        node.addEventListener('mouseleave', () => handlers.onLeave && handlers.onLeave());
    }

    return {
        render, initCamera, resetCamera, zoomBy, playerColor,
        RES_COLORS, PLAYER_COLORS, camera,
    };
})();
