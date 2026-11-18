// Scout knowledge map: every note as a node, every [[wikilink]] as a line,
// laid out by d3-force and drawn on a canvas.
//
// Swift → page:  load {data, mode, focus?}, focus {id}, details {id, excerpt}
// Page → Swift:  ready, open {id}, details {id}, jsError
(function () {
  'use strict';

  const canvas = document.getElementById('canvas');
  const ctx = canvas.getContext('2d');
  const $ = (id) => document.getElementById(id);

  // ---------- state ----------
  let mode = 'full';            // 'full' (overview) | 'local' (a note's neighbourhood)
  let nodes = [];
  let links = [];
  let byId = new Map();
  let neighbours = new Map();   // node -> Set(node)
  let groups = [];              // [{key,label,count}]
  let centerNode = null;        // local mode's note
  let sim = null;
  let W = 0, H = 0, dpr = 1;
  const view = { k: 1, x: 0, y: 0 };
  let autoFit = true;           // keep framing the graph until the user moves it
  let hover = null;
  let selected = null;
  let pointer = null;           // active pointer gesture
  const hiddenGroups = new Set();
  let showUnlinked = false;
  let colorMode = 'type';
  let scopeRoot = null;         // a node whose direct connections are all that's shown
  let scopeSet = null;
  let rings = [];               // [{key, r, count}] while scoped to one note
  let searchHits = null;        // Set of nodes matching the search, or null
  let results = [];
  let resultIndex = -1;
  const details = new Map();    // id -> excerpt
  let theme = {};
  let byDegree = [];            // visible nodes, most connected first
  let hubCut = Infinity;        // links touching a note above this degree are drawn only on focus
  let tween = null;
  let frameRequested = false;
  let reduced = scout.reducedMotion();
  const now = Date.now() / 1000;

  // ---------- theme ----------
  function readTheme() {
    theme = {
      paper: scout.token('paper') || '#fbfbf9',
      ink1: scout.token('ink-1') || '#202232',
      ink3: scout.token('ink-3') || '#5f5f6a',
      ink4: scout.token('ink-4') || '#66666c',
      decorative: scout.token('ink-decorative') || '#a8a8b3',
      rule: scout.token('rule-hard') || '#ddd',
      accent: scout.token('accent-fill') || '#be8a3d',
      accentInk: scout.token('accent-ink') || '#885d1c',
      groups: {},
    };
    for (const g of ['people', 'projects', 'issues', 'channels', 'ontology', 'research', 'topics',
                     'sources', 'personal', 'logs', 'audits', 'other']) {
      theme.groups[g] = scout.token('g-' + g) || theme.ink3;
    }
    theme.dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    requestDraw();
  }
  for (const q of ['(prefers-color-scheme: dark)', '(prefers-contrast: more)']) {
    window.matchMedia(q).addEventListener('change', () => { readTheme(); renderLegend(); renderCard(); });
  }
  window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', (e) => { reduced = e.matches; });

  function groupColor(key) { return theme.groups[key] || theme.ink3; }

  // Freshness: accent for today, fading to the decorative ink past a month.
  function freshness(n) {
    if (!n.modified) return 1;
    const days = Math.max(0, (now - n.modified) / 86400);
    if (days <= 1) return 0;
    if (days <= 7) return 0.33;
    if (days <= 30) return 0.66;
    return 1;
  }
  function nodeColor(n) {
    if (colorMode === 'fresh') {
      const t = freshness(n);
      return `color-mix(in srgb, ${theme.decorative} ${Math.round(t * 100)}%, ${theme.accent})`;
    }
    return groupColor(n.group);
  }
  // Canvas can't take color-mix(); resolve it once per colour through a probe element.
  const colorCache = new Map();
  const probe = document.createElement('span');
  probe.style.display = 'none';
  document.body.appendChild(probe);
  function resolveColor(css) {
    if (!css.startsWith('color-mix')) return css;
    const key = css;
    if (!colorCache.has(key)) {
      probe.style.color = css;
      colorCache.set(key, getComputedStyle(probe).color);
    }
    return colorCache.get(key);
  }

  // ---------- geometry ----------
  function radius(n) {
    if (mode === 'local') return n === centerNode ? 8 : Math.min(7, 3.5 + 1.1 * Math.sqrt(n.degree));
    return Math.min(15, 2.4 + 1.45 * Math.sqrt(n.degree));
  }
  const toWorld = (sx, sy) => [(sx - view.x) / view.k, (sy - view.y) / view.k];
  const toScreen = (n) => [n.x * view.k + view.x, n.y * view.k + view.y];

  function resize() {
    const r = canvas.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    const first = W === 0;
    W = r.width; H = r.height;
    canvas.width = Math.max(1, Math.round(W * dpr));
    canvas.height = Math.max(1, Math.round(H * dpr));
    if (first && nodes.length) fit(false);
    else if (autoFit) fit(false);
    requestDraw();
  }
  new ResizeObserver(resize).observe(canvas);

  // The part of the canvas not covered by the floating panels.
  function freeArea() {
    let left = 0, right = W, top = 0, bottom = H;
    if (mode === 'full') {
      top = 56;
      if (document.body.classList.contains('card-open') && W > 760) right = W - 324;
      if (W > 760) left = 240;
    }
    return { left, right, top, bottom };
  }

  function visibleBounds() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
      if (!n.visible || n.x == null) continue;
      const r = radius(n);
      x0 = Math.min(x0, n.x - r); y0 = Math.min(y0, n.y - r);
      x1 = Math.max(x1, n.x + r); y1 = Math.max(y1, n.y + r);
    }
    return x0 === Infinity ? null : { x0, y0, x1, y1 };
  }

  function fit(animate) {
    const b = visibleBounds();
    if (!b || W === 0) return;
    const a = freeArea();
    const pad = mode === 'local' ? 18 : 36;
    const bw = Math.max(40, b.x1 - b.x0), bh = Math.max(40, b.y1 - b.y0);
    const k = clamp(Math.min((a.right - a.left - 2 * pad) / bw, (a.bottom - a.top - 2 * pad) / bh), 0.06, mode === 'local' ? 2.4 : 2);
    const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
    moveTo(k, (a.left + a.right) / 2 - cx * k, (a.top + a.bottom) / 2 - cy * k, animate);
  }

  function centerOn(n, minK) {
    const a = freeArea();
    const k = Math.max(view.k, minK || 0);
    moveTo(k, (a.left + a.right) / 2 - n.x * k, (a.top + a.bottom) / 2 - n.y * k, true);
  }

  function moveTo(k, x, y, animate) {
    if (!animate || reduced) {
      tween = null;
      view.k = k; view.x = x; view.y = y;
      requestDraw();
      return;
    }
    tween = { from: { ...view }, to: { k, x, y }, start: performance.now(), dur: 420 };
    requestDraw();
  }

  function zoomAt(sx, sy, factor) {
    const k = clamp(view.k * factor, 0.05, 8);
    const f = k / view.k;
    view.x = sx - (sx - view.x) * f;
    view.y = sy - (sy - view.y) * f;
    view.k = k;
    tween = null;
    autoFit = false;
    requestDraw();
  }

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

  // ---------- data ----------
  // `message.data` is `{mode, focus, payload}` (Swift's `KBMapLoad`).
  function load(message) {
    const m = message.data || {};
    mode = m.mode === 'local' ? 'local' : 'full';
    document.body.className = 'mode-' + mode;
    const data = m.payload || { nodes: [], links: [], groups: [] };
    const saved = m.positions || {};
    message = { focus: m.focus };
    const previous = byId;
    nodes = data.nodes.map((n) => {
      const old = previous.get(n.id);
      const keep = old && old.x != null ? [old.x, old.y] : saved[n.id];
      return Object.assign({}, n, keep ? { x: keep[0], y: keep[1], vx: 0, vy: 0 } : {});
    });
    byId = new Map(nodes.map((n) => [n.id, n]));
    links = data.links.map(([a, b]) => ({ source: nodes[a], target: nodes[b] }));
    neighbours = new Map(nodes.map((n) => [n, new Set()]));
    for (const l of links) {
      neighbours.get(l.source).add(l.target);
      neighbours.get(l.target).add(l.source);
    }
    groups = data.groups || [];
    centerNode = data.center ? byId.get(data.center) || null : null;
    if (selected) selected = byId.get(selected.id) || null;
    if (hover) hover = byId.get(hover.id) || null;
    if (scopeRoot) {
      scopeRoot = byId.get(scopeRoot.id) || null;
      scopeSet = scopeRoot ? neighbourhood(scopeRoot, 1) : null;
    }
    for (const id of [...details.keys()]) if (!byId.has(id)) details.delete(id);

    const fresh = nodes.every((n) => n.x == null);
    if (fresh) seedPositions();
    else for (const n of nodes) if (n.x == null) {
      // A note new since the last load starts beside something it links to.
      const near = [...neighbours.get(n)].find((m) => m.x != null);
      n.x = (near ? near.x : 0) + (seeded(n.id, 1) - 0.5) * 30;
      n.y = (near ? near.y : 0) + (seeded(n.id, 2) - 0.5) * 30;
    }

    $('empty').hidden = nodes.length > 0;
    if (!nodes.length) $('empty').textContent = mode === 'local' ? 'No links yet.' : 'No notes yet — notes Scout writes will appear here as a map.';
    if (centerNode) { centerNode.fx = 0; centerNode.fy = 0; }

    renderLegend();
    // A local map re-centres on a new note: let it re-settle and re-frame.
    rebuild(fresh ? 1 : (mode === 'local' ? 0.6 : 0.12));
    renderCard();
    renderA11y();
    if (fresh || mode === 'local' || previous.size === 0) { autoFit = true; fit(false); }
    if (message.focus) focusId(message.focus, false);
  }

  // A stable pseudo-random number in [0, 1) for a note: the same vault lays
  // out the same way every time (d3-force's own jiggle is seeded too).
  function seeded(id, salt) {
    let h = 2166136261 ^ salt;
    for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619);
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    return ((h ^ (h >>> 13)) >>> 0) / 4294967296;
  }

  // Start each group in its own sector, so the layout untangles faster and
  // groups settle as neighbourhoods rather than a uniform hairball.
  function seedPositions() {
    const keys = groups.map((g) => g.key);
    const R = 30 + 9 * Math.sqrt(nodes.length);
    nodes.forEach((n, i) => {
      if (mode === 'local') {
        const a = (i / Math.max(1, nodes.length)) * Math.PI * 2;
        n.x = Math.cos(a) * 60; n.y = Math.sin(a) * 60;
        return;
      }
      const gi = Math.max(0, keys.indexOf(n.group));
      const a = (gi / Math.max(1, keys.length)) * Math.PI * 2 + (seeded(n.id, 3) - 0.5) * 0.8;
      const r = R * (0.35 + seeded(n.id, 4) * 0.65);
      n.x = Math.cos(a) * r; n.y = Math.sin(a) * r;
    });
  }

  function anchors() {
    const keys = groups.map((g) => g.key).filter((k) => !hiddenGroups.has(k));
    const vis = nodes.filter((n) => n.visible).length;
    const R = 40 + 11 * Math.sqrt(vis);
    const out = {};
    keys.forEach((k, i) => {
      const a = (i / Math.max(1, keys.length)) * Math.PI * 2 - Math.PI / 2;
      out[k] = { x: Math.cos(a) * R, y: Math.sin(a) * R };
    });
    return out;
  }

  function isVisible(n) {
    if (mode === 'local') return true;
    if (n === selected) return true;
    if (hiddenGroups.has(n.group)) return false;
    if (scopeSet) return scopeSet.has(n);
    return showUnlinked || n.degree > 0;
  }

  function rebuild(alpha) {
    for (const n of nodes) n.visible = isVisible(n);
    const vis = nodes.filter((n) => n.visible);
    const vlinks = links.filter((l) => l.source.visible && l.target.visible);
    byDegree = vis.slice().sort((a, b) => b.degree - a.degree || a.label.localeCompare(b.label));
    // The top 1% most-connected notes (and never fewer than 60 links) are hubs.
    hubCut = Math.max(60, (byDegree[Math.floor(byDegree.length * 0.01)] || { degree: Infinity }).degree);
    const anchor = anchors();
    const pull = mode === 'local' ? 0.06 : (scopeSet ? 0.03 : 0.07);
    // Links to a hub (the mistake audit links to hundreds of notes) are weak
    // and long, so one hub doesn't drag everything it touches into a ball.
    const linkStrength = (l) => 0.9 / Math.min(l.source.degree || 1, l.target.degree || 1) *
      (Math.max(l.source.degree, l.target.degree) > 60 ? 0.35 : 1);

    if (sim) sim.stop();
    sim = d3.forceSimulation(vis)
      .force('link', d3.forceLink(vlinks).strength(linkStrength).distance((l) =>
        mode === 'local' ? 46 : 30 + 5 * Math.sqrt(Math.min(l.source.degree, l.target.degree))))
      .force('charge', d3.forceManyBody()
        .strength((n) => (mode === 'local' ? -170 : -34 - 9 * Math.sqrt(n.degree)))
        .distanceMax(mode === 'local' ? 400 : 650).theta(0.9))
      .force('collide', d3.forceCollide((n) => radius(n) + (mode === 'local' ? 10 : 2.5)).iterations(1))
      .force('x', d3.forceX((n) => (anchor[n.group] && !scopeSet && mode === 'full' ? anchor[n.group].x : 0)).strength(pull))
      .force('y', d3.forceY((n) => (anchor[n.group] && !scopeSet && mode === 'full' ? anchor[n.group].y : 0)).strength(pull))
      .velocityDecay(0.45)
      .alphaDecay(mode === 'local' ? 0.04 : 0.03)
      .force('radial', null)
      .alpha(alpha)
      .on('tick', () => { if (autoFit) fit(false); requestDraw(); })
      .on('end', savePositions);

    // Showing one note's connections: the note sits still in the middle and
    // its connections settle on rings around it, one ring per type.
    rings = [];
    if (scopeSet && scopeRoot && scopeRoot.x != null) {
      const counts = new Map();
      for (const n of vis) if (n !== scopeRoot) counts.set(n.group, (counts.get(n.group) || 0) + 1);
      const keys = [...counts.keys()].sort((a, b) => counts.get(a) - counts.get(b));
      let r = 0;
      const targets = new Map();
      for (const key of keys) {
        // Enough circumference for every note on the ring, 16 px apiece.
        r = Math.max(r + 54, (counts.get(key) * 16) / (2 * Math.PI), 80);
        rings.push({ key, r, count: counts.get(key) });
        // Spread the ring's notes evenly, keeping the order they already had
        // around the note so the move reads as an unfolding, not a shuffle.
        const members = vis.filter((n) => n !== scopeRoot && n.group === key)
          .map((n) => [n, Math.atan2(n.y - scopeRoot.y, n.x - scopeRoot.x)])
          .sort((p, q) => p[1] - q[1]);
        const start = members.length ? members[0][1] : 0;
        members.forEach(([n], i) => {
          const a = start + (i / members.length) * Math.PI * 2;
          targets.set(n, [scopeRoot.x + Math.cos(a) * r, scopeRoot.y + Math.sin(a) * r]);
        });
      }
      scopeRoot.fx = scopeRoot.x; scopeRoot.fy = scopeRoot.y;
      sim.force('charge', null).force('collide', null).force('radial', null)
        .force('link', d3.forceLink(vlinks).strength(0))
        .force('x', d3.forceX((n) => (targets.get(n) || [scopeRoot.x])[0]).strength(0.28))
        .force('y', d3.forceY((n) => (targets.get(n) || [0, scopeRoot.y])[1]).strength(0.28));
    }

    if (reduced) {
      sim.stop();
      for (let i = 0, n = Math.ceil(Math.log(sim.alphaMin()) / Math.log(1 - sim.alphaDecay())); i < n; i++) sim.tick();
      if (autoFit) fit(false);
      savePositions();
    }
    updateFooter();
    requestDraw();
  }

  // Hand the settled layout to Swift, which gives it back on the next load,
  // so the map opens where you left it instead of re-settling.
  function savePositions() {
    if (mode !== 'full' || scopeSet || hiddenGroups.size) return;
    const out = {};
    for (const n of nodes) if (n.x != null) out[n.id] = [Math.round(n.x * 10) / 10, Math.round(n.y * 10) / 10];
    scout.post('positions', { positions: out });
  }

  function neighbourhood(root, depth) {
    const seen = new Set([root]);
    let frontier = [root];
    for (let d = 0; d < depth; d++) {
      const next = [];
      for (const n of frontier) for (const m of neighbours.get(n) || []) {
        if (!seen.has(m)) { seen.add(m); next.push(m); }
      }
      frontier = next;
    }
    return seen;
  }

  // ---------- drawing ----------
  function requestDraw() {
    if (frameRequested) return;
    frameRequested = true;
    requestAnimationFrame(draw);
  }

  const labelWidths = new Map();
  function measure(text, font) {
    const key = font + '|' + text;
    let w = labelWidths.get(key);
    if (w == null) { ctx.font = font; w = ctx.measureText(text).width; labelWidths.set(key, w); }
    return w;
  }

  function draw(time) {
    frameRequested = false;
    if (tween) {
      const t = Math.min(1, (time - tween.start) / tween.dur);
      const e = ease(t);
      view.k = tween.from.k + (tween.to.k - tween.from.k) * e;
      view.x = tween.from.x + (tween.to.x - tween.from.x) * e;
      view.y = tween.from.y + (tween.to.y - tween.from.y) * e;
      if (t >= 1) tween = null;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!nodes.length) return;

    const focus = hover || selected;
    const lit = focus ? new Set([focus, ...(neighbours.get(focus) || [])]) : null;
    const k = view.k;

    ctx.save();
    ctx.translate(view.x, view.y);
    ctx.scale(k, k);

    // Ring guides while showing one note's connections.
    if (rings.length && scopeRoot) {
      ctx.setLineDash([3 / k, 5 / k]);
      ctx.lineWidth = 1 / k;
      ctx.strokeStyle = theme.decorative;
      ctx.globalAlpha = 0.45;
      for (const g of rings) {
        ctx.beginPath();
        ctx.arc(scopeRoot.x, scopeRoot.y, g.r, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

    // Links: everything faint, the focused note's links strong.
    ctx.lineCap = 'round';
    ctx.beginPath();
    for (const l of links) {
      if (!l.source.visible || !l.target.visible) continue;
      if (lit && (l.source === focus || l.target === focus)) continue;
      // A hub's hundreds of spokes cross the whole map and bury everything
      // else; they show when the hub is hovered or selected.
      if (!scopeSet && mode === 'full' && (l.source.degree > hubCut || l.target.degree > hubCut)) continue;
      ctx.moveTo(l.source.x, l.source.y);
      ctx.lineTo(l.target.x, l.target.y);
    }
    // Thin and faint: a vault has ~10,000 links, and at full strength they
    // fuse into one grey mass behind the notes.
    ctx.strokeStyle = theme.decorative;
    ctx.globalAlpha = lit || searchHits ? 0.08 : (theme.dark ? 0.2 : 0.22);
    ctx.lineWidth = 0.6 / k;
    ctx.stroke();

    if (lit) {
      ctx.beginPath();
      for (const m of neighbours.get(focus)) {
        if (!m.visible) continue;
        ctx.moveTo(focus.x, focus.y);
        ctx.lineTo(m.x, m.y);
      }
      ctx.strokeStyle = theme.accent;
      ctx.globalAlpha = 0.85;
      ctx.lineWidth = 1.5 / k;
      ctx.stroke();
    }

    // Nodes.
    for (const n of byDegree.length ? byDegree.slice().reverse() : nodes) {
      if (!n.visible) continue;
      const dim = (lit && !lit.has(n)) || (searchHits && !searchHits.has(n));
      const r = radius(n);
      ctx.globalAlpha = dim ? 0.16 : 1;
      if (n === selected || n === centerNode) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5 / k, 0, Math.PI * 2);
        ctx.fillStyle = theme.accent;
        ctx.globalAlpha = 0.22;
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = resolveColor(nodeColor(n));
      ctx.fill();
      if (n === selected || n === hover || n === centerNode) {
        ctx.lineWidth = 2 / k;
        ctx.strokeStyle = theme.ink1;
        ctx.stroke();
      } else if (!dim) {
        ctx.lineWidth = 1 / k;
        ctx.strokeStyle = theme.paper;
        ctx.stroke();
      }
    }

    // Moving dots along the selected note's links.
    let animating = false;
    if (selected && selected.visible && !reduced) {
      animating = true;
      ctx.fillStyle = theme.accent;
      ctx.globalAlpha = 0.95;
      let i = 0;
      for (const m of neighbours.get(selected)) {
        if (!m.visible) continue;
        const t = ((time / 1600) + (i++ * 0.137)) % 1;
        ctx.beginPath();
        ctx.arc(selected.x + (m.x - selected.x) * t, selected.y + (m.y - selected.y) * t, 1.9 / k, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
    ctx.globalAlpha = 1;

    drawLabels(focus, lit);
    if (rings.length && scopeRoot) {
      const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--scale')) || 1;
      ctx.font = `600 ${10 * scale}px ${getComputedStyle(document.body).fontFamily}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      for (const g of rings) {
        const [sx, sy] = toScreen({ x: scopeRoot.x, y: scopeRoot.y - g.r });
        const text = `${groupLabel(g.key).toUpperCase()} · ${g.count}`;
        ctx.lineWidth = 3; ctx.strokeStyle = theme.paper; ctx.strokeText(text, sx, sy - 3);
        ctx.fillStyle = groupColor(g.key); ctx.fillText(text, sx, sy - 3);
      }
    }
    if (tween || animating || (sim && sim.alpha() > sim.alphaMin() && !reduced)) requestDraw();
  }

  function shortLabel(text, full) {
    if (full || text.length <= 26) return text;
    return text.slice(0, 24).trimEnd() + '…';
  }

  function drawLabels(focus, lit) {
    const placed = [];
    const forced = [];
    if (centerNode) forced.push(centerNode);
    if (selected) forced.push(selected);
    if (hover) forced.push(hover);
    // A hub can have hundreds of neighbours: only its best-connected ones are
    // promised a label; the rest compete for room like everything else.
    if (lit) for (const m of [...lit].sort((a, b) => b.degree - a.degree).slice(0, 24)) forced.push(m);
    if (searchHits) for (const m of [...searchHits].slice(0, 30)) forced.push(m);
    const budget = mode === 'local' ? 200 : Math.round(clamp(8 + 16 * view.k * view.k, 8, 180));
    const seen = new Set();
    let drawn = 0;
    const sans = getComputedStyle(document.body).fontFamily;
    const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--scale')) || 1;

    const order = forced.concat(byDegree);
    for (const n of order) {
      if (seen.has(n) || !n.visible) continue;
      seen.add(n);
      const isForced = forced.includes(n);
      if (!isForced && drawn >= budget) break;
      if (!isForced && lit && !lit.has(n)) continue;
      if (!isForced && searchHits && !searchHits.has(n)) continue;
      const [sx, sy] = toScreen(n);
      if (sx < -80 || sx > W + 80 || sy < -20 || sy > H + 20) continue;
      const strong = n === selected || n === hover || n === centerNode;
      const size = (strong ? 12 : 10.5) * scale;
      const font = `${strong ? 650 : 520} ${size}px ${sans}`;
      const text = shortLabel(n.label, strong);
      const w = measure(text, font);
      const r = radius(n) * view.k;
      const box = { x: sx - w / 2 - 3, y: sy + r + 2, w: w + 6, h: size + 4 };
      if (!isForced || !strong) {
        if (placed.some((p) => p.x < box.x + box.w && box.x < p.x + p.w && p.y < box.y + box.h && box.y < p.y + p.h)) continue;
      }
      placed.push(box);
      drawn++;
      ctx.font = font;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.lineJoin = 'round';
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = theme.paper;
      ctx.globalAlpha = 0.92;
      ctx.strokeText(text, sx, sy + r + 3);
      ctx.globalAlpha = 1;
      ctx.fillStyle = strong ? theme.ink1 : (lit && !lit.has(n) ? theme.ink4 : theme.ink1);
      ctx.fillText(text, sx, sy + r + 3);
    }
  }

  // ---------- picking & gestures ----------
  function nodeAt(sx, sy) {
    const [wx, wy] = toWorld(sx, sy);
    let best = null, bestD = Infinity;
    const slop = 5 / view.k;
    for (const n of nodes) {
      if (!n.visible) continue;
      const dx = n.x - wx, dy = n.y - wy;
      const d = Math.sqrt(dx * dx + dy * dy) - radius(n);
      if (d < slop && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  function localPoint(e) {
    const r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  canvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    canvas.setPointerCapture(e.pointerId);
    const [sx, sy] = localPoint(e);
    const n = nodeAt(sx, sy);
    pointer = { id: e.pointerId, sx, sy, node: n, moved: false, vx: view.x, vy: view.y };
  });

  canvas.addEventListener('pointermove', (e) => {
    const [sx, sy] = localPoint(e);
    if (pointer && pointer.id === e.pointerId) {
      const dx = sx - pointer.sx, dy = sy - pointer.sy;
      if (!pointer.moved && Math.hypot(dx, dy) > 3) {
        pointer.moved = true;
        autoFit = false;
        hideTooltip();
        if (!pointer.node) canvas.classList.add('panning');
      }
      if (!pointer.moved) return;
      if (pointer.node && pointer.node !== centerNode) {
        const [wx, wy] = toWorld(sx, sy);
        pointer.node.fx = wx; pointer.node.fy = wy;
        if (sim && !reduced) sim.alphaTarget(0.18).restart();
        else { pointer.node.x = wx; pointer.node.y = wy; }
        requestDraw();
      } else if (!pointer.node) {
        tween = null;
        view.x = pointer.vx + dx; view.y = pointer.vy + dy;
        requestDraw();
      }
      return;
    }
    const n = nodeAt(sx, sy);
    if (n !== hover) {
      hover = n;
      canvas.classList.toggle('on-node', !!n);
      requestDraw();
    }
    if (n && mode === 'local') showTooltip(n, sx, sy); else hideTooltip();
  });

  function endPointer(e) {
    if (!pointer || pointer.id !== e.pointerId) return;
    const p = pointer;
    pointer = null;
    canvas.classList.remove('panning');
    if (p.node && p.moved) {
      if (p.node !== centerNode) { p.node.fx = null; p.node.fy = null; }
      if (sim) sim.alphaTarget(0);
      return;
    }
    if (p.moved) return;
    if (p.node) {
      if (mode === 'local') { scout.post('open', { id: p.node.id }); return; }
      select(p.node, false);
    } else if (mode === 'full') {
      select(null);
    }
  }
  canvas.addEventListener('pointerup', endPointer);
  canvas.addEventListener('pointercancel', endPointer);
  canvas.addEventListener('pointerleave', () => {
    if (!pointer && hover) { hover = null; canvas.classList.remove('on-node'); hideTooltip(); requestDraw(); }
  });
  canvas.addEventListener('dblclick', (e) => {
    const [sx, sy] = localPoint(e);
    const n = nodeAt(sx, sy);
    if (n) scout.post('open', { id: n.id });
    else zoomAt(sx, sy, 1.8);
  });

  // Two-finger scroll pans; pinch (or ⌘/ctrl-scroll) zooms.
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const [sx, sy] = localPoint(e);
    if (e.ctrlKey || e.metaKey) {
      zoomAt(sx, sy, Math.exp(-e.deltaY * (e.ctrlKey ? 0.012 : 0.003)));
    } else {
      tween = null;
      autoFit = false;
      view.x -= e.deltaX;
      view.y -= e.deltaY;
      requestDraw();
    }
  }, { passive: false });
  let gestureScale = 1;
  canvas.addEventListener('gesturestart', (e) => { e.preventDefault(); gestureScale = 1; });
  canvas.addEventListener('gesturechange', (e) => {
    e.preventDefault();
    const [sx, sy] = localPoint(e);
    zoomAt(sx, sy, e.scale / gestureScale);
    gestureScale = e.scale;
  });
  canvas.addEventListener('gestureend', (e) => e.preventDefault());

  function showTooltip(n, sx, sy) {
    const t = $('tooltip');
    t.textContent = n.label + (n === centerNode ? ' — this note' : ' — click to open');
    t.style.left = sx + 'px';
    t.style.top = (sy - radius(n) * view.k) + 'px';
    t.hidden = false;
  }
  function hideTooltip() { $('tooltip').hidden = true; }

  // ---------- selection & the side card ----------
  function select(n, recenter) {
    const reveal = n && !n.visible;
    // Selected first: `isVisible` keeps the selected note on the map, so an
    // unlinked note picked from search stays drawn through the rebuild.
    selected = n;
    if (reveal) {
      // Reveal a hidden note picked from search: clear whatever hid it.
      hiddenGroups.delete(n.group);
      if (scopeSet && !scopeSet.has(n)) clearScope(false);
      renderLegend();
      rebuild(0.2);
    }
    if (n && !details.has(n.id)) scout.post('details', { id: n.id });
    renderCard();
    if (n && recenter) centerOn(n, mode === 'full' ? 1.4 : 0);
    requestDraw();
  }

  function focusId(id, animate) {
    const n = byId.get(id);
    if (!n) return;
    autoFit = false;
    select(n, false);
    // Wait for the layout to give the node a place before flying to it.
    const go = () => centerOn(n, 1.6);
    if (n.x == null) setTimeout(go, 300); else if (animate === false) setTimeout(go, 60); else go();
  }

  function relTime(seconds) {
    if (!seconds) return '';
    const days = Math.floor((now - seconds) / 86400);
    if (days <= 0) return 'updated today';
    if (days === 1) return 'updated yesterday';
    if (days < 14) return `updated ${days} days ago`;
    const d = new Date(seconds * 1000);
    const sameYear = d.getFullYear() === new Date().getFullYear();
    return 'updated ' + d.toLocaleDateString(undefined, sameYear ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function groupLabel(key) {
    const g = groups.find((x) => x.key === key);
    return g ? g.label : key;
  }

  function nodeButton(n, extra) {
    return `<li><button type="button" data-id="${scout.escape(n.id)}" title="${scout.escape(n.label)}">` +
      `<span class="dot" style="--c:${groupColor(n.group)}"></span><span>${scout.escape(n.label)}</span>` +
      `<span class="n">${extra != null ? scout.escape(extra) : n.degree}</span></button></li>`;
  }

  function renderCard() {
    const card = $('card');
    if (mode !== 'full' || !nodes.length) { document.body.classList.remove('card-open'); card.innerHTML = ''; return; }
    document.body.classList.add('card-open');
    if (!selected) {
      const hubs = nodes.filter((n) => n.degree > 0).sort((a, b) => b.degree - a.degree).slice(0, 6);
      const recent = nodes.filter((n) => n.modified).sort((a, b) => b.modified - a.modified).slice(0, 6);
      card.innerHTML =
        `<h2>Explore</h2>` +
        `<p class="hint">Click a note to see what it connects to, double-click to open it. ` +
        `Drag to pan, pinch to zoom, <kbd>/</kbd> to search.</p>` +
        (hubs.length ? `<h3><span>Most connected</span><span>links</span></h3><ul>${hubs.map((n) => nodeButton(n)).join('')}</ul>` : '') +
        (recent.length ? `<h3><span>Recently updated</span></h3><ul>${recent.map((n) => nodeButton(n, shortAgo(n.modified))).join('')}</ul>` : '');
      wireCardButtons();
      return;
    }
    const n = selected;
    const near = [...(neighbours.get(n) || [])].sort((a, b) => b.degree - a.degree || a.label.localeCompare(b.label));
    const excerpt = details.get(n.id);
    card.innerHTML =
      `<button class="close" type="button" aria-label="Close" title="Close (Esc)">×</button>` +
      `<div class="eyebrow"><span class="pill" style="--c:${groupColor(n.group)}">${scout.escape(groupLabel(n.group))}</span>` +
      `<span class="folder" title="${scout.escape(n.id)}">${scout.escape(n.folder || 'knowledge-base')}</span></div>` +
      `<h2>${scout.escape(n.label)}</h2>` +
      `<div class="meta">${n.degree} link${n.degree === 1 ? '' : 's'}${n.modified ? ' · ' + relTime(n.modified) : ''}</div>` +
      `<p class="excerpt${excerpt == null ? ' loading' : ''}">${excerpt == null ? 'Loading…' : scout.escape(excerpt || 'No summary yet.')}</p>` +
      `<div class="actions"><button class="btn primary" type="button" data-act="open">Open note</button>` +
      (near.length ? `<button class="btn" type="button" data-act="scope">${scopeRoot === n ? 'Show everything' : 'Only its connections'}</button>` : '') +
      `</div>` +
      (near.length ? `<h3><span>Connected notes</span><span>${near.length}</span></h3><ul>${near.slice(0, 40).map((m) => nodeButton(m)).join('')}</ul>` : '');
    card.querySelector('.close').addEventListener('click', () => select(null));
    card.querySelector('[data-act="open"]').addEventListener('click', () => scout.post('open', { id: n.id }));
    const scope = card.querySelector('[data-act="scope"]');
    if (scope) scope.addEventListener('click', () => (scopeRoot === n ? clearScope(true) : setScope(n)));
    wireCardButtons();
  }

  function shortAgo(seconds) {
    const days = Math.floor((now - seconds) / 86400);
    if (days <= 0) return 'today';
    if (days < 30) return days + 'd';
    return Math.floor(days / 30) + 'mo';
  }

  function wireCardButtons() {
    for (const b of $('card').querySelectorAll('li button[data-id]')) {
      const n = byId.get(b.dataset.id);
      b.addEventListener('click', () => { autoFit = false; select(n, true); });
      b.addEventListener('dblclick', () => scout.post('open', { id: n.id }));
      b.addEventListener('mouseenter', () => { hover = n; requestDraw(); });
      b.addEventListener('mouseleave', () => { if (hover === n) { hover = null; requestDraw(); } });
    }
  }

  function setScope(n) {
    scopeRoot = n;
    // One hop: the vault is a small world, and two hops from any hub is
    // nearly every note.
    scopeSet = neighbourhood(n, 1);
    $('scope').hidden = false;
    $('scope-text').innerHTML = `Connections of <b>${scout.escape(n.label)}</b> · ${scopeSet.size} notes`;
    autoFit = false;
    rebuild(0.9);
    renderCard();
    // Frame the outermost ring now; the notes fly onto it under the camera.
    const outer = rings.length ? rings[rings.length - 1].r + 40 : 200;
    const a = freeArea();
    const k = clamp(Math.min((a.right - a.left) / (2 * outer), (a.bottom - a.top) / (2 * outer)), 0.1, 2);
    moveTo(k, (a.left + a.right) / 2 - n.x * k, (a.top + a.bottom) / 2 - n.y * k, true);
  }
  function clearScope(refit) {
    if (scopeRoot && scopeRoot !== centerNode) { scopeRoot.fx = null; scopeRoot.fy = null; }
    scopeRoot = null;
    scopeSet = null;
    $('scope').hidden = true;
    rebuild(0.5);
    renderCard();
    if (refit) { autoFit = true; setTimeout(() => { autoFit = false; fit(true); }, reduced ? 0 : 900); }
  }
  $('scope-clear').addEventListener('click', () => clearScope(true));

  // ---------- legend ----------
  function renderLegend() {
    const ul = $('groups');
    ul.innerHTML = groups.map((g) =>
      `<li><button type="button" data-key="${g.key}" aria-pressed="${!hiddenGroups.has(g.key)}" ` +
      `title="Click to hide or show · ⌥-click to show only this type">` +
      `<span class="dot" style="--c:${groupColor(g.key)}"></span><span>${scout.escape(g.label)}</span>` +
      `<span class="count">${g.count}</span></button></li>`).join('');
    for (const b of ul.querySelectorAll('button')) {
      b.addEventListener('click', (e) => {
        const key = b.dataset.key;
        if (e.altKey) {
          const solo = groups.every((g) => g.key === key || hiddenGroups.has(g.key)) && !hiddenGroups.has(key);
          hiddenGroups.clear();
          if (!solo) for (const g of groups) if (g.key !== key) hiddenGroups.add(g.key);
        } else if (hiddenGroups.has(key)) hiddenGroups.delete(key);
        else hiddenGroups.add(key);
        if (selected && hiddenGroups.has(selected.group)) selected = null;
        renderLegend();
        autoFit = true;
        rebuild(0.5);
        renderCard();
        setTimeout(() => { autoFit = false; }, reduced ? 0 : 1400);
      });
    }
    const unlinked = nodes.filter((n) => n.degree === 0).length;
    $('unlinked-count').textContent = unlinked;
    $('unlinked').checked = showUnlinked;
    $('unlinked').parentElement.style.display = unlinked ? '' : 'none';
    for (const b of document.querySelectorAll('.seg button')) {
      b.setAttribute('aria-checked', String(b.dataset.color === colorMode));
    }
    $('fresh-key').hidden = colorMode !== 'fresh';
    updateFooter();
  }
  $('unlinked').addEventListener('change', (e) => {
    showUnlinked = e.target.checked;
    autoFit = true;
    rebuild(0.4);
    setTimeout(() => { autoFit = false; }, reduced ? 0 : 1400);
  });
  for (const b of document.querySelectorAll('.seg button')) {
    b.addEventListener('click', () => { colorMode = b.dataset.color; renderLegend(); requestDraw(); });
  }
  function updateFooter() {
    const shown = nodes.filter((n) => n.visible).length;
    $('shown').textContent = shown === nodes.length ? `${nodes.length} notes` : `Showing ${shown} of ${nodes.length} notes`;
    canvas.setAttribute('aria-label', `Knowledge map, ${shown} notes shown`);
  }

  // ---------- search ----------
  function score(label, term) {
    const l = label.toLowerCase();
    if (l === term) return 0;
    if (l.startsWith(term)) return 1;
    if (l.split(/[\s\-_/·:]+/).some((w) => w.startsWith(term))) return 2;
    if (l.includes(term)) return 3;
    return -1;
  }
  function runSearch(raw) {
    const term = raw.trim().toLowerCase();
    if (term.length < 1) { results = []; searchHits = null; renderResults(); requestDraw(); return; }
    const scored = [];
    for (const n of nodes) {
      let s = score(n.label, term);
      if (s < 0 && n.id.toLowerCase().includes(term)) s = 4;
      if (s >= 0) scored.push([s, n]);
    }
    scored.sort((a, b) => a[0] - b[0] || b[1].degree - a[1].degree || a[1].label.localeCompare(b[1].label));
    results = scored.slice(0, 8).map((x) => x[1]);
    searchHits = term.length >= 2 ? new Set(scored.slice(0, 60).map((x) => x[1])) : null;
    resultIndex = results.length ? 0 : -1;
    renderResults();
    requestDraw();
  }
  function highlight(label, term) {
    const i = label.toLowerCase().indexOf(term);
    if (i < 0 || !term) return scout.escape(label);
    return scout.escape(label.slice(0, i)) + '<mark>' + scout.escape(label.slice(i, i + term.length)) + '</mark>' + scout.escape(label.slice(i + term.length));
  }
  function renderResults() {
    const ul = $('results');
    const term = $('q').value.trim().toLowerCase();
    if (!term) { ul.hidden = true; $('q').setAttribute('aria-expanded', 'false'); return; }
    ul.hidden = false;
    ul.classList.add('surface');
    $('q').setAttribute('aria-expanded', 'true');
    if (!results.length) { ul.innerHTML = '<li class="none">No notes match</li>'; return; }
    ul.innerHTML = results.map((n, i) =>
      `<li role="option" data-id="${scout.escape(n.id)}" aria-selected="${i === resultIndex}">` +
      `<span class="dot" style="--c:${groupColor(n.group)}"></span>` +
      `<span class="name">${highlight(n.label, term)}</span><span class="where">${scout.escape(n.folder || '')}</span></li>`).join('');
    for (const li of ul.querySelectorAll('li[data-id]')) {
      li.addEventListener('mousedown', (e) => { e.preventDefault(); choose(byId.get(li.dataset.id)); });
    }
  }
  function choose(n) {
    if (!n) return;
    $('q').value = '';
    runSearch('');
    $('q').blur();
    autoFit = false;
    select(n, true);
  }
  $('q').addEventListener('input', (e) => runSearch(e.target.value));
  $('q').addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!results.length) return;
      resultIndex = (resultIndex + (e.key === 'ArrowDown' ? 1 : -1) + results.length) % results.length;
      renderResults();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (results[resultIndex]) {
        if (e.metaKey) scout.post('open', { id: results[resultIndex].id }); else choose(results[resultIndex]);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      $('q').value = '';
      runSearch('');
      $('q').blur();
    }
  });
  $('q').addEventListener('blur', () => setTimeout(() => { $('results').hidden = true; }, 120));
  $('q').addEventListener('focus', () => { if ($('q').value) renderResults(); });

  // ---------- zoom buttons & keys ----------
  $('zoom-in').addEventListener('click', () => zoomAt(W / 2, H / 2, 1.4));
  $('zoom-out').addEventListener('click', () => zoomAt(W / 2, H / 2, 1 / 1.4));
  $('zoom-fit').addEventListener('click', () => { autoFit = false; fit(true); });

  document.addEventListener('keydown', (e) => {
    if (e.target === $('q')) return;
    if ((e.key === '/' || (e.key === 'f' && e.metaKey)) && mode === 'full') {
      e.preventDefault();
      $('q').focus();
      $('q').select();
    } else if (e.key === 'Escape') {
      if (scopeRoot) clearScope(true); else if (selected) select(null);
    } else if (e.key === '+' || e.key === '=') zoomAt(W / 2, H / 2, 1.4);
    else if (e.key === '-' || e.key === '_') zoomAt(W / 2, H / 2, 1 / 1.4);
    else if (e.key === '0') { autoFit = false; fit(true); }
    else if (e.key === 'Enter' && selected) scout.post('open', { id: selected.id });
  });

  // ---------- VoiceOver ----------
  function renderA11y() {
    const list = nodes.slice().sort((a, b) =>
      (b === centerNode) - (a === centerNode) || b.degree - a.degree || a.label.localeCompare(b.label)).slice(0, 60);
    $('a11y').innerHTML = list.map((n) =>
      `<button type="button" data-id="${scout.escape(n.id)}" aria-label="${scout.escape(n.a11y || n.label)}"></button>`).join('');
    for (const b of $('a11y').querySelectorAll('button')) {
      b.addEventListener('click', () => scout.post('open', { id: b.dataset.id }));
    }
  }

  // ---------- messages ----------
  scout.on('load', load);
  scout.on('focus', (m) => focusId(m.id, true));
  scout.on('details', (m) => {
    details.set(m.id, m.excerpt || '');
    if (selected && selected.id === m.id) renderCard();
  });

  readTheme();
})();
