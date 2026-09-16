/* ------------------------------------------------------------------ *
 *  panels.js -- everything outside the SVG: player cards, bank, legal
 *  moves, the trade offer on the table, the move log and the inspector.
 * ------------------------------------------------------------------ */

const Panels = (() => {
    'use strict';

    const RES_ORDER = ['wood', 'brick', 'sheep', 'wheat', 'ore'];
    const RES_ABBR = { wood: 'Wood', brick: 'Brick', sheep: 'Sheep', wheat: 'Wheat', ore: 'Ore' };
    const DEV_ORDER = ['knight', 'year_of_plenty', 'monopoly', 'road_building', 'victory_point'];
    const DEV_ABBR = {
        knight: 'Knight', year_of_plenty: 'YoP', monopoly: 'Mono',
        road_building: 'RoadB', victory_point: 'VP',
    };
    const DEV_FULL = {
        knight: 'Knight', year_of_plenty: 'Year of Plenty', monopoly: 'Monopoly',
        road_building: 'Road Building', victory_point: 'Victory Point',
    };
    const VP_PARTS = [
        ['settlements', 'Settlements', '#5eead4'],
        ['cities', 'Cities', '#38bdf8'],
        ['longest_road', 'Longest Road', '#37d399'],
        ['largest_army', 'Largest Army', '#f7913a'],
        ['dev_cards', 'VP cards', '#a98cf0'],
    ];

    function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

    function h(tag, cls, html) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (html !== undefined && html !== null) node.innerHTML = html;
        return node;
    }

    function esc(s) {
        return String(s).replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    }

    function resDot(res) {
        return `<span class="dot" style="background:${Board.RES_COLORS[res] || '#888'}"></span>`;
    }

    // --- Status bar ---------------------------------------------------- //
    function renderStatus(state) {
        const chips = document.getElementById('status-chips');
        chips.innerHTML = '';
        const info = state && state.game_info;
        if (!info) {
            chips.appendChild(h('span', 'chip offline', 'No game state yet'));
            return;
        }

        if (info.winner) {
            chips.appendChild(h('span', 'chip winner', `${cap(info.winner)} wins`));
        }
        chips.appendChild(h('span', 'chip phase', `Prompt <b>${esc(info.phase)}</b>`));
        chips.appendChild(h('span', 'chip', `Turn <b>${info.turn_index}</b>`));

        const actingSame = info.current_player === info.turn_player;
        const turnColor = Board.playerColor(info.turn_player);
        chips.appendChild(h('span', 'chip',
            `Turn: <b style="color:${turnColor}">${cap(info.turn_player)}</b>`));
        if (!actingSame) {
            chips.appendChild(h('span', 'chip',
                `Acting: <b style="color:${Board.playerColor(info.current_player)}">${cap(info.current_player)}</b>`));
        }

        if (info.in_setup) {
            chips.appendChild(h('span', 'chip setup', `Setup (${info.setup_turn_idx})`));
        }
        chips.appendChild(h('span', 'chip', `Moves <b>${info.moves_played}</b>`));

        const roll = state.last_dice_roll;
        if (roll && roll.length === 2) {
            const total = roll[0] + roll[1];
            const hot = total === 7 ? ' hot' : '';
            const chip = h('span', 'chip');
            chip.innerHTML = `<span class="dice">` +
                `<span class="die${hot}">${roll[0]}</span>` +
                `<span class="die${hot}">${roll[1]}</span>` +
                `<span class="total">${total}</span></span>`;
            chips.appendChild(chip);
        }

        const discarding = (state.players_data || []).filter(p => p.discards_required > 0);
        if (discarding.length) {
            chips.appendChild(h('span', 'chip alert',
                `Discarding: ${discarding.map(p => `${cap(p.player_id)} ${p.discards_required}`).join(', ')}`));
        }
    }

    // --- Player cards --------------------------------------------------- //
    function renderPlayer(p, container, info) {
        const color = Board.playerColor(p.player_id);
        container.className = 'player-card';
        container.style.borderLeftColor = color;
        if (p.is_turn_player) container.classList.add('turn');
        if (p.is_current_player && !p.is_turn_player) container.classList.add('prompted');
        if (!p.is_turn_player && !p.is_current_player) container.classList.add('dim');
        container.innerHTML = '';

        // Header
        const head = h('div', 'pc-head');
        const name = h('div', 'pc-name');
        name.innerHTML = `<span class="pc-swatch" style="background:${color}"></span>` +
            `<span>${cap(p.player_id)}</span>`;
        if (p.is_turn_player) name.appendChild(h('span', 'tag turn', 'Turn'));
        else if (p.is_current_player) name.appendChild(h('span', 'tag acting', 'Acting'));
        head.appendChild(name);

        const vpTotal = info ? info.vp_to_win : 10;
        const vp = h('div', 'vp-badge' + (p.victory_points >= vpTotal ? ' winning' : ''));
        const parts = VP_PARTS
            .filter(([key]) => p.vp_breakdown && p.vp_breakdown[key])
            .map(([key, label]) => `${label}: ${p.vp_breakdown[key]}`);
        vp.title = parts.length
            ? `${parts.join('\n')}\nPublic (visible to opponents): ${p.victory_points_public}`
            : 'No victory points yet';
        vp.innerHTML = `<span class="vp-num">${p.victory_points}</span><span class="vp-lbl">VP</span>`;
        head.appendChild(vp);
        container.appendChild(head);

        // VP breakdown bar
        const bar = h('div', 'vp-bar');
        VP_PARTS.forEach(([key, label, col]) => {
            const v = (p.vp_breakdown && p.vp_breakdown[key]) || 0;
            if (!v) return;
            const seg = h('span');
            seg.style.width = `${(v / vpTotal) * 100}%`;
            seg.style.background = col;
            seg.title = `${label}: ${v}`;
            bar.appendChild(seg);
        });
        container.appendChild(bar);

        // Resources
        const resLabel = h('div', 'section-label',
            `Resources <span class="count">${p.resource_total}</span>`);
        container.appendChild(resLabel);
        const resRow = h('div', 'pill-row');
        RES_ORDER.forEach(r => {
            const n = (p.resources && p.resources[r]) || 0;
            const pill = h('div', 'pill' + (n === 0 ? ' zero' : ''));
            pill.title = RES_ABBR[r];
            pill.innerHTML = `${resDot(r)}<span class="n">${n}</span>`;
            resRow.appendChild(pill);
        });
        container.appendChild(resRow);

        // Dev cards
        container.appendChild(h('div', 'section-label',
            `Dev cards <span class="count">${p.dev_card_count}</span>`));
        const devRow = h('div', 'pill-row');
        DEV_ORDER.forEach(d => {
            const held = (p.dev_cards && p.dev_cards[d]) || 0;
            const fresh = (p.new_dev_cards && p.new_dev_cards[d]) || 0;
            const total = held + fresh;
            const pill = h('div', 'pill' + (total === 0 ? ' zero' : ''));
            pill.title = DEV_FULL[d] + (fresh ? ` (${fresh} bought this turn, not yet playable)` : '');
            pill.innerHTML = `${DEV_ABBR[d]} <span class="n">${total}</span>` +
                (fresh ? ` <span class="new">+${fresh}</span>` : '');
            devRow.appendChild(pill);
        });
        container.appendChild(devRow);

        // Stats
        const stats = h('div', 'stat-grid');
        const stat = (num, sub, title) => {
            const s = h('div', 'stat', `<div class="num">${num}</div><div class="sub">${sub}</div>`);
            if (title) s.title = title;
            return s;
        };
        stats.appendChild(stat(`${p.settlements_built}<small>/5</small>`, 'Setts',
            `${p.settlements_remaining} left in supply`));
        stats.appendChild(stat(`${p.cities_built}<small>/4</small>`, 'Cities',
            `${p.cities_remaining} left in supply`));
        stats.appendChild(stat(`${p.roads_built}<small>/15</small>`, 'Roads',
            `${p.roads_remaining} left in supply`));
        stats.appendChild(stat(p.knights_played, 'Knights', 'Knights played (largest army counter)'));
        stats.appendChild(stat(p.longest_road_length, 'Road len', 'Longest continuous road'));
        const owned = p.owned_nodes || [];
        stats.appendChild(stat(owned.length, 'Nodes', `Occupied: ${owned.join(', ') || 'none'}`));
        container.appendChild(stats);

        // Badges
        const badges = h('div', 'badge-row');
        if (p.is_winner) badges.appendChild(h('span', 'badge win', 'Winner'));
        if (p.has_longest_road) badges.appendChild(h('span', 'badge lr', `Longest Road ${p.longest_road_length}`));
        if (p.has_largest_army) badges.appendChild(h('span', 'badge la', `Largest Army ${p.knights_played}`));
        if (p.discards_required > 0) badges.appendChild(h('span', 'badge discard', `Discard ${p.discards_required}`));
        (p.ports || []).forEach(port => {
            const lbl = port.ratio === 3 ? '3:1' : `2:1 ${cap(port.type)}`;
            badges.appendChild(h('span', 'badge port', lbl));
        });
        container.appendChild(badges);
    }

    function renderPlayers(state) {
        const info = state.game_info;
        (state.players_data || []).forEach(p => {
            const node = document.getElementById(`player-${p.index}`);
            if (node) renderPlayer(p, node, info);
        });
    }

    // --- Bank / awards --------------------------------------------------- //
    function renderBank(state, showSpoilers) {
        const info = state.game_info;
        const bank = document.getElementById('bank-resources');
        const awards = document.getElementById('awards');
        bank.innerHTML = '';
        awards.innerHTML = '';
        if (!info) return;

        RES_ORDER.forEach(r => {
            const n = info.bank ? info.bank[r] : 0;
            const cell = h('div', 'res-cell' + (n === 0 ? ' zero' : n <= 3 ? ' low' : ''));
            cell.title = n === 0 ? `Bank is out of ${r}` : `${n} ${r} left in the bank`;
            cell.innerHTML = `${resDot(r)}${RES_ABBR[r]} <span class="n">${n}</span>`;
            bank.appendChild(cell);
        });

        const deck = h('div', 'res-cell');
        deck.innerHTML = `Dev deck <span class="n">${info.dev_cards_remaining}</span>`;
        if (showSpoilers) {
            const left = Object.entries(info.dev_deck_breakdown || {});
            deck.title = left.length
                ? left.map(([k, v]) => `${DEV_FULL[k] || k}: ${v}`).join('\n')
                : 'Deck is empty';
            if (left.length) {
                deck.innerHTML += ` <span style="color:var(--text-faint);font-size:0.68rem">(${
                    left.map(([k, v]) => `${DEV_ABBR[k] || k} ${v}`).join(' ')})</span>`;
            }
        } else {
            deck.title = 'Turn on Deck spoilers to see what is left';
        }
        bank.appendChild(deck);

        const holder = (owner, value, unclaimed) => owner
            ? `<span class="holder" style="color:${Board.playerColor(owner)}">${cap(owner)}</span> <span style="color:var(--text-dim)">(${value})</span>`
            : `<span class="holder" style="color:var(--text-faint)">${unclaimed}</span>`;

        awards.appendChild(h('div', 'award-line',
            `<span class="icon">R</span> Longest Road: ${holder(info.longest_road_owner, info.longest_road_length, 'unclaimed')}`));
        awards.appendChild(h('div', 'award-line',
            `<span class="icon">A</span> Largest Army: ${holder(info.largest_army_owner, info.largest_army_size, 'unclaimed')}`));
        awards.appendChild(h('div', 'award-line',
            `<span class="icon">W</span> Target: <b>${info.vp_to_win} VP</b>`));
        awards.appendChild(h('div', 'award-line',
            `<span class="icon">H</span> Robber on <b style="font-family:var(--mono)">H${info.robber_hex}</b>`));
    }

    // --- Legal moves ----------------------------------------------------- //
    function renderLegal(state, showList) {
        const box = document.getElementById('legal-list');
        const head = document.getElementById('legal-count');
        const legal = state.legal || {};
        box.innerHTML = '';
        head.textContent = legal.total || 0;

        if (legal.error) {
            box.appendChild(h('div', 'legal-empty', esc(legal.error)));
            return;
        }
        const entries = Object.entries(legal.action_counts || {});
        if (!entries.length) {
            box.appendChild(h('div', 'legal-empty', 'No legal moves'));
            return;
        }
        if (showList) { renderLegalList(box, legal); return; }

        entries.sort((a, b) => b[1] - a[1]);
        entries.forEach(([name, n]) => {
            const row = h('div', 'legal-row');
            row.innerHTML = `<span class="name">${esc(name)}</span><span class="n">${n}</span>`;
            box.appendChild(row);
        });
    }

    // Every playable move, grouped under its action so a 60-move turn still
    // reads as a handful of groups rather than one long wall.
    function renderLegalList(box, legal) {
        const moves = legal.moves || [];
        if (!moves.length) {
            box.appendChild(h('div', 'legal-empty', 'No move detail available'));
            return;
        }
        const groups = new Map();
        moves.forEach(m => {
            if (!groups.has(m.action)) groups.set(m.action, []);
            groups.get(m.action).push(m);
        });

        [...groups.entries()]
            .sort((a, b) => b[1].length - a[1].length)
            .forEach(([name, items]) => {
                const grp = h('div', 'legal-group');
                const head = h('div', 'legal-group-head');
                head.innerHTML = `<span class="name">${esc(name)}</span><span class="n">${items.length}</span>`;
                grp.appendChild(head);
                items.forEach(m => {
                    const row = h('div', 'legal-move', m.text);
                    // The packed int is the thing you paste into a test.
                    row.title = `0x${(m.raw >>> 0).toString(16)}  (${m.raw})`;
                    grp.appendChild(row);
                });
                box.appendChild(grp);
            });

        if (legal.moves_truncated) {
            box.appendChild(h('div', 'legal-empty',
                `+${legal.moves_truncated} more not listed`));
        }
    }

    // --- Trade on the table ----------------------------------------------- //
    function renderTrade(state) {
        const panel = document.getElementById('trade-panel');
        const body = document.getElementById('trade-body');
        const trade = state.trade || {};
        if (!trade.pending) { panel.style.display = 'none'; return; }
        panel.style.display = '';
        body.innerHTML = '';

        const side = (label, bag) => {
            const wrap = h('div', 'trade-side');
            wrap.appendChild(h('div', 'lbl', label));
            const row = h('div', 'pill-row');
            const any = RES_ORDER.filter(r => (bag && bag[r]) > 0);
            if (!any.length) row.appendChild(h('div', 'pill zero', 'nothing'));
            any.forEach(r => {
                const pill = h('div', 'pill');
                pill.innerHTML = `${resDot(r)}<span class="n">${bag[r]}</span>`;
                pill.title = RES_ABBR[r];
                row.appendChild(pill);
            });
            wrap.appendChild(row);
            return wrap;
        };

        const flow = h('div', 'trade-flow');
        flow.appendChild(side(`${cap(trade.proposer)} gives`, trade.give));
        flow.appendChild(h('div', 'trade-arrow', '&rarr;'));
        flow.appendChild(side('and wants', trade.take));
        body.appendChild(flow);

        const votes = h('div', 'trade-votes');
        (state.players_data || []).forEach(p => {
            if (p.player_id === trade.proposer) return;
            const yes = (trade.accepted_by || []).includes(p.player_id);
            const waiting = trade.awaiting === p.player_id;
            const v = h('div', 'vote ' + (yes ? 'yes' : 'wait'));
            v.innerHTML = `<span class="dot" style="width:8px;height:8px;border-radius:2px;background:${Board.playerColor(p.player_id)}"></span>` +
                `${cap(p.player_id)}: ${yes ? 'accepted' : waiting ? 'deciding...' : 'no'}`;
            votes.appendChild(v);
        });
        body.appendChild(votes);
    }

    // --- Move log ---------------------------------------------------------- //
    function renderLog(state, filters) {
        const list = document.getElementById('log-list');
        const entries = state.move_log || [];
        const shown = entries.filter(e => filters.has(e.category));

        // Only stick to the bottom if the user was already there.
        const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
        list.innerHTML = '';
        document.getElementById('log-count').textContent = `${shown.length}/${entries.length}`;

        if (!shown.length) {
            list.appendChild(h('div', 'log-empty',
                entries.length ? 'Nothing matches the current filters.' : 'No moves recorded yet.'));
            return;
        }

        shown.forEach(e => {
            const row = h('div', `log-entry cat-${e.category}`);
            const color = Board.playerColor(e.player);
            const deltas = Object.entries(e.deltas || {})
                .map(([who, phrase]) => `${cap(who)} ${phrase}`)
                .join('  |  ');
            row.innerHTML =
                `<div class="n">${e.n}</div>` +
                `<div><span class="who" style="color:${color}">${cap(e.player)}</span> ` +
                `<span class="what">${esc(e.text)}</span>` +
                (deltas ? `<div class="meta">${esc(deltas)}</div>` : '') +
                `</div>`;
            row.title = `move ${e.n} | turn ${e.turn} | prompt ${e.prompt} | action ${e.action}`;
            list.appendChild(row);
        });

        if (atBottom) list.scrollTop = list.scrollHeight;
    }

    // --- Inspector ----------------------------------------------------------- //
    function inspectorHTML(ref, state) {
        const rows = [];
        const add = (k, v) => rows.push(
            `<div class="ins-row"><div class="ins-key">${k}</div><div class="ins-val">${v}</div></div>`);

        const hexById = new Map((state.hexes || []).map(x => [x.id, x]));
        const nodeById = new Map((state.nodes || []).map(x => [x.id, x]));
        const hexLabel = id => {
            const x = hexById.get(id);
            return x ? `H${id} ${x.resource}${x.number ? ' ' + x.number : ''}` : `H${id}`;
        };

        if (ref.kind === 'hex') {
            const x = hexById.get(ref.id);
            if (!x) return '';
            const owners = {};
            (x.nodes || []).forEach(nid => {
                const n = nodeById.get(nid);
                if (n && n.owner) owners[n.owner] = (owners[n.owner] || 0) + (n.type === 'city' ? 2 : 1);
            });
            add('resource', x.resource);
            add('number', x.number ? `${x.number}  (${x.pips} pips, ${(x.pips / 36 * 100).toFixed(1)}%)` : 'none');
            add('robber', x.hasRobber ? 'YES - production blocked' : 'no');
            add('corners', (x.nodes || []).join(', '));
            add('edges', (x.edges || []).join(', '));
            add('buildings', Object.keys(owners).length
                ? Object.entries(owners).map(([o, n]) => `${o}:${n}`).join(', ') : 'none');
            return head(`H${ref.id}`, 'hex') + rows.join('');
        }

        if (ref.kind === 'node') {
            const n = nodeById.get(ref.id);
            if (!n) return '';
            add('state', n.owner ? `${n.owner} ${n.type}` : 'empty');
            add('port', n.port ? `${n.port_ratio}:1 ${n.port}` : 'none');
            add('hexes', (n.adj_hexes || []).map(hexLabel).join('  '));
            add('pips', `${n.pip_total} total across adjacent hexes`);
            add('adj nodes', (n.adj_nodes || []).join(', ') || 'none');
            add('adj roads', (n.adj_roads || []).join(', ') || 'none');
            add('draw at', `hex ${n.location.hexId}, vertex ${n.location.vertex}`);
            return head(`N${ref.id}`, 'settlement node') + rows.join('');
        }

        if (ref.kind === 'edge') {
            const e = (state.edges || []).find(x => x.id === ref.id);
            if (!e) return '';
            add('state', e.owner ? `${e.owner} road` : 'empty');
            add('endpoints', (e.nodes || []).map(v => `N${v}`).join('  -  '));
            add('hexes', (e.adj_hexes || []).map(hexLabel).join('  ') || 'coast only');
            add('adj roads', (e.adj_roads || []).join(', ') || 'none');
            add('draw at', `hex ${e.location.hexId}, edge ${e.location.edge}`);
            return head(`E${ref.id}`, 'road edge') + rows.join('');
        }

        if (ref.kind === 'port') {
            const p = (state.ports || []).find(x => x.id === ref.id);
            if (!p) return '';
            add('type', p.ratio === 3 ? '3:1 any resource' : `2:1 ${p.type}`);
            add('nodes', (p.nodes || []).map(v => `N${v}`).join('  '));
            const holders = (state.players_data || [])
                .filter(pl => (p.nodes || []).some(nid => pl.owned_nodes.includes(nid)))
                .map(pl => pl.player_id);
            add('claimed by', holders.length ? holders.join(', ') : 'nobody');
            return head(`P${ref.id}`, 'port') + rows.join('');
        }
        return '';

        function head(title, tag) {
            return `<div class="ins-head">${title}<span class="ins-tag">${tag}</span></div>`;
        }
    }

    return {
        renderStatus, renderPlayers, renderBank, renderLegal, renderTrade,
        renderLog, inspectorHTML, cap,
    };
})();
