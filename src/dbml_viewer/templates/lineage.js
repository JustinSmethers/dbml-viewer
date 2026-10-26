// ── Lineage view ──────────────────────────────────────────────
// Data flow between models (MODEL.lineage), kept separate from the FK
// diagram. Layout is pure (lineage-layout.js); this file renders it lazily
// the first time the Lineage tab opens and handles selection and impact.
// The view is a fixed top bar plus its own scroll area (.lin-scroll), which
// sits right of the project header and review panel on wide screens.
//
// Public: lineageView.show(name) switches to Lineage and selects a model;
// lineageView.follow(name) re-selects it when Lineage is already active.
// Neither opens the review panel, because their callers already show it.
const lineageView = (() => {
  const root = document.getElementById('view-lineage');
  const data = { edges: [], stubs: [], stub_kinds: {}, order: [], notes: [], tables: {}, ...(MODEL.lineage || {}) };
  const reviewTables = MODEL.review?.tables || {};
  const bus = MODEL.review?.bus_matrix || {};
  const layerStage = { source: 0, seed: 1, prep: 2, intermediate: 3, bridge: 4, mart: 5 };
  const changeBadge = status => DBMLReview.CHANGE_LABELS[status] ? `<span class="lin-change lin-change-${status}">${DBMLReview.CHANGE_LABELS[status]}</span>` : '';
  const ns = 'http://www.w3.org/2000/svg';
  let graph = null, selected = null, impactOn = false, zoom = 1, fitted = false;
  const nodeEls = {};
  let edgeEls = [];
  let parts = {};

  const changeOf = name => comparison?.tables?.[name]?.status;
  const isModel = name => !!tableByName[name];
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  function layerColor(name) {
    const stage = layerStage[MODEL.layers?.[name]];
    return (stage !== undefined ? stageColors[stage] : tableColor(tableByName[name]))?.header || '#64748b';
  }
  function consumersOf(names) {
    const out = new Set();
    names.forEach(name => {
      const info = data.tables[name];
      if (info) (info.consumers_affected || []).forEach(c => out.add(c));
      // Stubs have no entry; use the models they feed.
      else data.edges.filter(e => e.from === name).forEach(e => (data.tables[e.to]?.consumers_affected || []).forEach(c => out.add(c)));
    });
    return [...out];
  }
  function findingsBadge(name) {
    const findings = reviewTables[name]?.findings || [];
    if (!findings.length) return '';
    const label = Object.entries(DBMLReview.severityCounts(findings)).filter(([, n]) => n).map(([s, n]) => plural(n, s)).join(', ');
    return `<span class="lin-findings lin-sev-${DBMLReview.worstSeverity(findings) || 'info'}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">${findings.length}</span>`;
  }

  // Card names are measured in the card font, so cards fit their names.
  function textWidth() {
    const context = document.createElement('canvas').getContext('2d');
    if (!context) return undefined;
    context.font = '600 12px ' + getComputedStyle(document.body).fontFamily;
    return name => context.measureText(name).width + 2;
  }

  function render() {
    graph = LineageLayout.layout(MODEL, { textWidth: textWidth() });
    const modelCount = Object.values(graph.nodes).filter(n => !n.stub).length;
    const canImpact = !!comparison && [...changedNames].some(name => graph.nodes[name]);
    root.innerHTML = `
      <div class="lin-head">
        <div class="lin-bar">
          <h2 class="lin-title">Lineage <span>${plural(modelCount, 'model')} · ${plural(graph.edges.length, 'edge')}${data.stubs.length ? ' · ' + plural(data.stubs.length, 'upstream stub') : ''}</span></h2>
          <div class="lin-actions">
            ${canImpact ? '<button class="branch-btn" id="lin-impact" aria-pressed="false" title="Highlight changed models and everything downstream of them">Impact of changes</button>' : ''}
            <button class="branch-btn" id="lin-fit" title="Scale the graph to the available width">Fit</button>
            <button class="branch-btn" id="lin-reset" title="Actual size">1:1</button>
          </div>
        </div>
        <div class="lin-legend">
          <span><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" class="lin-sample"/></svg>ref() in SQL</span>
          <span><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" class="lin-sample lin-manifest"/></svg>dbt manifest</span>
          <span><svg width="26" height="8"><line x1="1" y1="4" x2="25" y2="4" class="lin-sample lin-declared"/></svg>declared depends_on</span>
          <span><i class="lin-swatch lin-swatch-up"></i>upstream</span>
          <span><i class="lin-swatch lin-swatch-down"></i>downstream</span>
          <span class="lin-hint">Click a model to trace it · Esc clears</span>
        </div>
        ${data.notes.length ? `<div class="lin-notes">${data.notes.map(escapeHtml).join(' · ')}</div>` : ''}
        ${graph.edges.length ? '' : '<p class="lin-empty">No lineage edges found. Add <code>depends_on</code> to charters, or run inside a dbt project so model SQL can be read.</p>'}
        ${busMatrix()}
        <div class="lin-detail" id="lin-detail" aria-live="polite" hidden></div>
      </div>
      <div class="lin-scroll"><div class="lin-sizer"><div class="lin-graph"><svg class="lin-edges"></svg></div></div></div>`;
    parts = {
      scroll: root.querySelector('.lin-scroll'), sizer: root.querySelector('.lin-sizer'), canvas: root.querySelector('.lin-graph'),
      svg: root.querySelector('.lin-edges'), detail: root.querySelector('#lin-detail'),
    };
    parts.canvas.style.width = graph.width + 'px';
    parts.canvas.style.height = graph.height + 'px';
    parts.svg.setAttribute('width', graph.width);
    parts.svg.setAttribute('height', graph.height);
    parts.svg.innerHTML = '<defs>' + ['base', 'up', 'down'].map(kind =>
      `<marker id="lin-arrow-${kind}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M 0 1 L 9 5 L 0 9 Z" class="lin-arrow-${kind}"/></marker>`).join('') + '</defs>';

    graph.columns.forEach(column => {
      const label = document.createElement('div');
      label.className = 'lin-column';
      label.style.left = column.x + 'px'; label.style.width = column.w + 'px';
      label.textContent = column.label;
      parts.canvas.appendChild(label);
    });
    edgeEls = graph.edges.map(edge => {
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', LineageLayout.pathD(edge.points));
      path.setAttribute('class', 'lin-edge lin-' + edge.source);
      path.setAttribute('marker-end', 'url(#lin-arrow-base)');
      const title = document.createElementNS(ns, 'title');
      title.textContent = `${edge.from} → ${edge.to} (${edge.sources.join(' + ')})`;
      path.appendChild(title);
      parts.svg.appendChild(path);
      return { edge, path };
    });
    Object.entries(graph.nodes).forEach(([name, node]) => {
      const el = document.createElement('div');
      el.className = 'lin-node' + (node.stub ? ' lin-stub' : '');
      el.dataset.name = name;
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      Object.assign(el.style, { left: node.x + 'px', top: node.y + 'px', width: node.w + 'px', height: node.h + 'px' });
      if (node.stub) {
        const kind = data.stub_kinds[name] || 'unknown';
        el.title = `${name}\nNot in the DBML (${kind})`;
        el.innerHTML = `<span class="lin-kind">${escapeHtml(kind)}</span><span class="lin-name">${escapeHtml(name)}</span>`;
      } else {
        const status = changeOf(name);
        if (status) el.dataset.status = status;
        el.style.setProperty('--layer', layerColor(name));
        el.title = `${name}\n${node.layer || 'unclassified layer'}`;
        el.innerHTML = `<div class="lin-name">${escapeHtml(name)}</div>
          <div class="lin-meta"><span class="lin-layer">${escapeHtml(node.layer || 'unclassified')}</span>
          ${changeBadge(status)}${findingsBadge(name)}</div>`;
      }
      parts.canvas.appendChild(el);
      nodeEls[name] = el;
    });

    root.querySelector('#lin-fit').addEventListener('click', fit);
    root.querySelector('#lin-reset').addEventListener('click', () => { fitted = false; setZoom(1); });
    root.querySelector('#lin-impact')?.addEventListener('click', () => toggleImpact());
    root.addEventListener('click', event => {
      const node = event.target.closest('.lin-node');
      if (node) { select(node.dataset.name); return; }
      if (event.target.closest('.lin-head, button')) return;
      if (selected) select(null);
    });
    root.addEventListener('keydown', event => {
      const node = event.target.closest('.lin-node');
      if (node && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select(node.dataset.name); }
    });
  }

  // Collapsed by default so it never pushes the graph down on large diagrams.
  function busMatrix() {
    const facts = bus.facts || [], dims = bus.dims || [];
    if (!facts.length) return '';
    const cells = bus.cells || {};
    return `<details class="lin-bus">
      <summary>Bus matrix: ${plural(facts.length, 'fact')} × ${dims.length} dim${dims.length === 1 ? '' : 's'}</summary>
      <div class="lin-bus-body">
        <p>Built only from declared DBML refs between facts and <code>dim_*</code> tables; dimensions re-derived inside a fact are not detected.</p>
        <table><thead><tr><th scope="col">Model</th>${dims.map(d => `<th scope="col">${escapeHtml(d)}</th>`).join('')}</tr></thead>
        <tbody>${facts.map(fact => `<tr><th scope="row">${escapeHtml(fact)} ${changeBadge(changeOf(fact))}</th>${dims.map(d =>
          (cells[fact] || []).includes(d) ? '<td class="lin-hit" aria-label="yes">✓</td>' : '<td></td>').join('')}</tr>`).join('')}</tbody></table>
      </div>
    </details>`;
  }

  function setZoom(value) {
    zoom = value;
    parts.canvas.style.transform = zoom === 1 ? '' : `scale(${zoom})`;
    parts.sizer.style.width = graph.width * zoom + 'px';
    parts.sizer.style.height = graph.height * zoom + 'px';
  }
  function availableWidth() {
    const style = getComputedStyle(parts.scroll);
    return parts.scroll.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  }
  // Scroll a graph-space box into the scroll area. 'start' puts its top
  // near the top; otherwise it is centred. Visible boxes stay put unless forced.
  function reveal(box, { vertical = 'center', force = false } = {}) {
    const el = parts.scroll, margin = 24;
    const x = parts.sizer.offsetLeft + box.x * zoom, y = parts.sizer.offsetTop + box.y * zoom;
    const w = box.w * zoom, h = box.h * zoom, viewW = el.clientWidth, viewH = el.clientHeight;
    const inView = x >= el.scrollLeft && x + w <= el.scrollLeft + viewW && y >= el.scrollTop && y + h <= el.scrollTop + viewH;
    if (inView && !force) return;
    const left = w + 2 * margin <= viewW ? x - (viewW - w) / 2 : x - margin;
    const top = vertical === 'start' || h + 2 * margin > viewH ? y - margin : y - (viewH - h) / 2;
    el.scrollTo({ left: Math.max(0, left), top: Math.max(0, top) });
  }
  // First changed model in walkthrough order that has a card in the graph.
  function firstChanged() {
    return [...DBMLReview.walkthroughOf(MODEL), ...changedNames].find(name => changedNames.has(name) && graph.nodes[name]) || null;
  }
  // Scroll so the model's column header and its card are both in view; the
  // left edge falls in the gap before the column, never across a header.
  function revealColumnOf(name) {
    const el = parts.scroll, node = graph.nodes[name], margin = 24;
    const offset = parts.sizer.offsetLeft;
    const column = graph.columns.find(c => c.key === node.column);
    const columns = graph.columns.map(c => ({ x: offset + c.x * zoom }));
    const left = LineageLayout.scrollStart(columns, offset + column.x * zoom, el.scrollWidth - el.clientWidth, margin);
    const bottom = parts.sizer.offsetTop + (node.y + node.h) * zoom + margin;
    el.scrollTo({ left, top: bottom <= el.clientHeight ? 0 : parts.sizer.offsetTop + node.y * zoom - margin });
  }
  function fit() {
    fitted = true;
    setZoom(Math.max(0.4, Math.min(1, availableWidth() / Math.max(1, graph.width))));
  }

  // Highlight state: `up`/`down` closures around the selection, or the
  // changed models and their downstream closure in impact mode.
  function paint(focus, up, down, roots) {
    parts.canvas.classList.toggle('lin-focus', focus);
    Object.entries(nodeEls).forEach(([name, el]) => {
      el.classList.toggle('lin-selected', roots.has(name));
      el.classList.toggle('lin-up', up.has(name));
      el.classList.toggle('lin-down', down.has(name));
      el.setAttribute('aria-pressed', String(name === selected));
    });
    edgeEls.forEach(({ edge, path }) => {
      const isUp = up.has(edge.from) && (up.has(edge.to) || roots.has(edge.to));
      const isDown = down.has(edge.to) && (down.has(edge.from) || roots.has(edge.from));
      const kind = isUp ? 'up' : isDown ? 'down' : 'base';
      path.classList.toggle('lin-on-up', isUp);
      path.classList.toggle('lin-on-down', isDown);
      path.setAttribute('marker-end', `url(#lin-arrow-${kind})`);
      // Highlighted edges draw above faded ones.
      if (kind !== 'base') parts.svg.appendChild(path);
    });
  }
  function detail(html) {
    parts.detail.hidden = !html;
    parts.detail.innerHTML = html ? html + '<button class="lin-clear" aria-label="Clear selection" title="Clear (Esc)">&times;</button>' : '';
    parts.detail.querySelector('.lin-clear')?.addEventListener('click', clear);
  }
  const consumerText = list => list.length ? list.map(escapeHtml).join(', ') : '<em>none declared</em>';

  function select(name, { review = true } = {}) {
    if (name && !graph.nodes[name]) name = null;
    selected = name;
    if (impactOn && name) setImpact(false);
    if (!name) { if (!impactOn) { paint(false, new Set(), new Set(), new Set()); detail(''); } return; }
    const up = LineageLayout.closure(data.edges, name, 'up');
    const down = LineageLayout.closure(data.edges, name, 'down');
    paint(true, up, down, new Set([name]));
    const direct = dir => new Set(data.edges.filter(e => (dir === 'up' ? e.to : e.from) === name).map(e => dir === 'up' ? e.from : e.to)).size;
    const impact = [...down].filter(isModel).length;
    const stub = graph.nodes[name].stub;
    detail(`<strong>${escapeHtml(name)}</strong>${stub ? ` <span class="lin-muted">not in the DBML (${escapeHtml(data.stub_kinds[name] || 'unknown')})</span>` : ''}
      — <span class="lin-up-text">${direct('up')} upstream</span>${up.size > direct('up') ? ` (${up.size} total)` : ''},
      <span class="lin-down-text">${direct('down')} downstream</span>, impact ${plural(impact, 'model')}
      <div>Affected consumers: ${consumerText(consumersOf([name]))}</div>`);
    reveal(graph.nodes[name]);
    if (review && !stub) openReview(name);
  }
  function clear() { selected = null; if (impactOn) setImpact(false); select(null); }

  function setImpact(on) {
    impactOn = on;
    root.querySelector('#lin-impact')?.setAttribute('aria-pressed', String(on));
    if (!on) return;
    selected = null;
    const roots = new Set([...changedNames].filter(name => graph.nodes[name]));
    // Per-root closures, so a changed model downstream of another counts.
    const down = new Set([...roots].flatMap(name => [...LineageLayout.closure(data.edges, name, 'down')]));
    paint(true, new Set(), down, roots);
    const impacted = [...down].filter(isModel);
    detail(`<strong>Impact of changes</strong> — ${plural(roots.size, 'changed model')}
      (${[...roots].map(escapeHtml).join(', ')}), <span class="lin-down-text">impact ${plural(impacted.length, 'model')}</span>${impacted.length ? ': ' + impacted.map(escapeHtml).join(', ') : ''}
      <div>Affected consumers: ${consumerText(consumersOf([...roots]))}</div>`);
  }
  function toggleImpact() {
    if (impactOn) { setImpact(false); select(null); } else setImpact(true);
  }

  // Narrow screens stack the project header above the view; start below it.
  function place() {
    root.style.setProperty('--lin-top', document.querySelector('.project-header').getBoundingClientRect().bottom + 8 + 'px');
  }
  function ensure() {
    place();
    if (graph) return;
    render();
    setZoom(1);
    const fitScale = availableWidth() / Math.max(1, graph.width);
    const changed = firstChanged();
    if (changed) {
      // Fit only when labels stay readable, then bring the change into view.
      if (fitScale < 1 && fitScale >= 0.8) fit();
      revealColumnOf(changed);
    } else {
      if (fitScale >= 0.5) fit();
      parts.scroll.scrollTo({ left: 0, top: 0 });
    }
  }
  function follow(name) {
    if (!graph || activeView !== 'lineage') return;
    if (selected !== name) select(graph.nodes[name] ? name : null, { review: false });
  }
  onView(view => {
    if (view !== 'lineage') return;
    ensure();
    if (!reviewPanel.hidden && reviewFocus) follow(reviewFocus);
  });
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !isTextField(event.target) && graph && activeView === 'lineage' && (selected || impactOn)) clear();
  });
  window.addEventListener('resize', () => {
    if (!graph || activeView !== 'lineage') return;
    place();
    if (fitted) fit();
  });

  return {
    show(name) {
      if (activeView !== 'lineage') switchView('lineage');
      ensure();
      select(name, { review: false });
    },
    follow,
  };
})();
