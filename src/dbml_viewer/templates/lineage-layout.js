// Lineage view layout. Shared by the viewer and the Node tests; no DOM.
// Columns follow warehouse layer order; edges spanning several columns get
// dummy waypoints so they travel through gaps between cards, not across them.
const LineageLayout = (() => {
  const COLUMNS = [
    { key: 'stub', label: 'Outside DBML', layers: [] },
    { key: 'source', label: 'Sources & seeds', layers: ['source', 'seed'] },
    { key: 'prep', label: 'Prep', layers: ['prep'] },
    { key: 'intermediate', label: 'Intermediate', layers: ['intermediate'] },
    { key: 'mart', label: 'Marts', layers: ['mart'] },
    { key: 'bridge', label: 'Bridges', layers: ['bridge'] },
  ];
  // Model cards size to their name: nodeW..nodeMaxW wide, then the name
  // wraps and the card grows by lineH per extra line. textWidth(name) is the
  // rendered name width in px; the viewer measures it, the default estimates.
  const DEFAULTS = {
    nodeW: 180, nodeMaxW: 340, nodePadX: 28, nodeH: 44, lineH: 15, stubW: 120, stubMaxW: 240, stubH: 24, dummyH: 4,
    gapX: 76, gapY: 12, blockGap: 14, marginX: 12, marginY: 30, sweeps: 2,
    textWidth: name => 7.2 * String(name).length,
  };

  // { w, h } of a model card whose name renders textWidth px wide.
  function cardSize(textWidth, opt = DEFAULTS) {
    const wanted = Math.ceil(textWidth + opt.nodePadX);
    if (wanted <= opt.nodeMaxW) return { w: Math.max(opt.nodeW, wanted), h: opt.nodeH };
    const lines = Math.ceil(textWidth / (opt.nodeMaxW - opt.nodePadX));
    return { w: opt.nodeMaxW, h: opt.nodeH + (lines - 1) * opt.lineH };
  }

  // Unknown layers sit with intermediate models, as in the diagram layout.
  function columnKey(layer) {
    return (COLUMNS.find(column => column.layers.includes(layer)) || COLUMNS[3]).key;
  }

  function layout(model, options = {}) {
    const opt = { ...DEFAULTS, ...options };
    const lineage = model.lineage || {};
    const layers = model.layers || {};
    const tables = (model.tables || []).map(table => table.name);
    const known = new Set(tables);
    const stubs = [];
    const addStub = name => { if (!known.has(name) && !stubs.includes(name)) stubs.push(name); };
    (lineage.stubs || []).forEach(addStub);
    const edges = (lineage.edges || []).filter(edge => edge.from !== edge.to);
    edges.forEach(edge => { addStub(edge.from); addStub(edge.to); });
    const stubSet = new Set(stubs);
    const orderIndex = new Map((lineage.order || []).map((name, i) => [name, i]));

    // Column per node; empty columns are dropped so the graph stays compact.
    const keyOf = name => stubSet.has(name) ? 'stub' : columnKey(layers[name] ?? null);
    const used = COLUMNS.filter(column => [...tables, ...stubs].some(name => keyOf(name) === column.key));
    const rankOf = new Map([...tables, ...stubs].map(name => [name, used.findIndex(c => c.key === keyOf(name))]));
    const block = name => layers[name] === 'seed' ? 1 : 0;   // seeds stack below sources
    // Stub pills size to their label (kind tag + name), within limits.
    const stubWidth = name => Math.round(Math.min(opt.stubMaxW, Math.max(opt.stubW, 80 + 6.6 * name.length)));
    const size = name => stubSet.has(name) ? { w: stubWidth(name), h: opt.stubH } : cardSize(opt.textWidth(name), opt);

    // Chains: from, dummy..., to. Same-column edges have no dummies.
    const items = new Map();   // id -> { id, rank, block, dummy }
    [...tables, ...stubs].forEach(name => items.set(name, { id: name, rank: rankOf.get(name), block: block(name), dummy: false, ...size(name) }));
    const chains = edges.map((edge, index) => {
      const a = rankOf.get(edge.from), b = rankOf.get(edge.to);
      const chain = [edge.from];
      const step = b > a ? 1 : -1;
      for (let rank = a + step; Math.abs(b - a) > 1 && rank !== b; rank += step) {
        const id = `\u0000${index}:${rank}`;
        items.set(id, { id, rank, block: 0.5, dummy: true, w: 0, h: opt.dummyH });
        chain.push(id);
      }
      chain.push(edge.to);
      return chain;
    });
    const left = new Map(), right = new Map();   // neighbours in adjacent columns
    [...items.keys()].forEach(id => { left.set(id, []); right.set(id, []); });
    chains.forEach(chain => chain.slice(1).forEach((id, i) => {
      const a = chain[i], b = id, ra = items.get(a).rank, rb = items.get(b).rank;
      if (Math.abs(ra - rb) !== 1) return;
      const [lo, hi] = ra < rb ? [a, b] : [b, a];
      right.get(lo).push(hi); left.get(hi).push(lo);
    }));

    // Initial order: topological lineage order, stubs next to their targets.
    const firstUse = name => Math.min(Infinity, ...edges.filter(e => e.from === name).map(e => orderIndex.get(e.to) ?? Infinity));
    const seed = id => {
      const item = items.get(id);
      if (item.dummy) { const edge = edges[Number(id.slice(1).split(':')[0])]; return (orderIndex.get(edge.from) ?? 0) + 0.5; }
      return stubSet.has(id) ? firstUse(id) - 0.5 : orderIndex.get(id) ?? Infinity;
    };
    const columns = used.map(() => []);
    [...items.values()].forEach(item => columns[item.rank].push(item.id));
    columns.forEach(ids => ids.sort((a, b) => items.get(a).block - items.get(b).block || seed(a) - seed(b) || String(a).localeCompare(String(b))));

    // Barycentre sweeps reduce crossings; ties keep the current order.
    const position = new Map();
    const index = () => columns.forEach(ids => ids.forEach((id, i) => position.set(id, i)));
    index();
    function reorder(rank, neighbours) {
      const bary = new Map(columns[rank].map(id => {
        const list = neighbours.get(id);
        return [id, list.length ? list.reduce((sum, n) => sum + position.get(n), 0) / list.length : position.get(id)];
      }));
      columns[rank].sort((a, b) => items.get(a).block - items.get(b).block || bary.get(a) - bary.get(b) || position.get(a) - position.get(b));
      columns[rank].forEach((id, i) => position.set(id, i));
    }
    for (let sweep = 0; sweep < opt.sweeps; sweep++) {
      for (let rank = 1; rank < columns.length; rank++) reorder(rank, left);
      for (let rank = columns.length - 2; rank >= 0; rank--) reorder(rank, right);
    }

    // Coordinates: columns left to right, each stacked from the top, so short
    // columns stay in view on large graphs.
    const widths = columns.map(ids => Math.max(0, ...ids.map(id => items.get(id).w)));
    const heights = columns.map(ids => ids.reduce((h, id, i) => h + items.get(id).h + (i ? opt.gapY : 0)
      + (i && items.get(ids[i - 1]).block < 1 && items.get(id).block === 1 ? opt.blockGap : 0), 0));
    const tallest = Math.max(0, ...heights);
    const placed = new Map();
    const columnInfo = [];
    let x = opt.marginX;
    columns.forEach((ids, rank) => {
      columnInfo.push({ key: used[rank].key, label: used[rank].label, x, w: widths[rank] });
      let y = opt.marginY;
      ids.forEach((id, i) => {
        const item = items.get(id);
        if (i && items.get(ids[i - 1]).block < 1 && item.block === 1) y += opt.blockGap;
        // Model cards share their column's width; stubs hug its right edge so
        // their edges stay short.
        const stub = stubSet.has(id);
        placed.set(id, { x: stub ? x + widths[rank] - item.w : x, y, w: stub ? item.w : widths[rank], h: item.h });
        y += item.h + opt.gapY;
      });
      x += widths[rank] + opt.gapX;
    });

    const nodes = {};
    [...tables, ...stubs].forEach(name => {
      const p = placed.get(name);
      nodes[name] = { ...p, column: keyOf(name), stub: stubSet.has(name), layer: stubSet.has(name) ? null : layers[name] ?? null };
    });

    const laid = edges.map((edge, i) => {
      const chain = chains[i], a = placed.get(edge.from), b = placed.get(edge.to);
      const forward = rankOf.get(edge.to) > rankOf.get(edge.from);
      const loop = rankOf.get(edge.to) === rankOf.get(edge.from);
      const points = [loop || forward ? { x: a.x + a.w, y: a.y + a.h / 2 } : { x: a.x, y: a.y + a.h / 2 }];
      chain.slice(1, -1).forEach(id => {
        const d = placed.get(id), y = d.y + d.h / 2;
        points.push(forward ? { x: d.x, y } : { x: d.x + d.w, y }, forward ? { x: d.x + d.w, y } : { x: d.x, y });
      });
      points.push(forward ? { x: b.x, y: b.y + b.h / 2 } : { x: b.x + b.w, y: b.y + b.h / 2 });
      return { from: edge.from, to: edge.to, source: edge.source || 'declared', sources: edge.sources || [edge.source || 'declared'], points, loop };
    });
    spreadPorts(laid, nodes);

    return {
      nodes, edges: laid, columns: columnInfo,
      width: Math.max(0, x - opt.gapX + opt.marginX + 40), height: tallest + opt.marginY + 16,
    };
  }

  // Several edges on one side of a card fan out over its height instead of
  // meeting at a single point; order follows the far end to avoid crossings.
  function spreadPorts(edges, nodes) {
    const ports = new Map();
    edges.forEach(edge => [[edge.from, 0, 1], [edge.to, edge.points.length - 1, edge.points.length - 2]].forEach(([name, at, next]) => {
      const node = nodes[name], point = edge.points[at];
      const key = name + '\u0000' + (point.x === node.x ? 'L' : 'R');
      if (!ports.has(key)) ports.set(key, []);
      ports.get(key).push({ point, other: edge.points[next] });
    }));
    ports.forEach((list, key) => {
      if (list.length < 2) return;
      const node = nodes[key.split('\u0000')[0]];
      const span = Math.max(0, node.h - (node.stub ? 8 : 14));
      const step = Math.min(7, span / (list.length - 1));
      list.sort((a, b) => a.other.y - b.other.y);
      list.forEach((port, i) => { port.point.y = node.y + node.h / 2 + (i - (list.length - 1) / 2) * step; });
    });
  }

  // SVG path through the points: horizontal S-curves between columns,
  // straight runs through a column gap, and a bulge for same-column edges.
  function pathD(points) {
    let d = `M ${points[0].x} ${points[0].y}`;
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1], b = points[i];
      if (a.x === b.x) d += ` C ${a.x + 34} ${a.y} ${b.x + 34} ${b.y} ${b.x} ${b.y}`;
      else if (a.y === b.y) d += ` L ${b.x} ${b.y}`;
      else { const mx = (a.x + b.x) / 2; d += ` C ${mx} ${a.y} ${mx} ${b.y} ${b.x} ${b.y}`; }
    }
    return d;
  }

  // First-open scroll position: the target column starts `margin` px from
  // the left edge, so no column header is clipped. Near the right end, where
  // the browser would clamp, use the furthest column start that still fits.
  function scrollStart(columns, targetX, maxScroll, margin = 24) {
    const want = Math.max(0, targetX - margin);
    if (want <= maxScroll) return want;
    return Math.max(0, ...columns.map(column => column.x - margin).filter(x => x <= maxScroll));
  }

  // Transitive closure along data flow: 'up' follows edges to upstream models.
  function closure(edges, start, direction) {
    const next = new Map();
    edges.forEach(edge => {
      const [a, b] = direction === 'up' ? [edge.to, edge.from] : [edge.from, edge.to];
      if (!next.has(a)) next.set(a, []);
      next.get(a).push(b);
    });
    const seen = new Set(), queue = [].concat(start);
    while (queue.length) (next.get(queue.shift()) || []).forEach(n => { if (!seen.has(n)) { seen.add(n); queue.push(n); } });
    [].concat(start).forEach(n => seen.delete(n));
    return seen;
  }

  return { COLUMNS, DEFAULTS, columnKey, cardSize, layout, scrollStart, pathD, closure };
})();
if (typeof module !== 'undefined') module.exports = LineageLayout;
