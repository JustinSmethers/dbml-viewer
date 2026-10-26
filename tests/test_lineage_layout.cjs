const { test } = require('node:test');
const assert = require('node:assert/strict');
const lineage = require('../src/dbml_viewer/templates/lineage-layout.js');

function model(layers, edges, extra = {}) {
  return {
    tables: Object.keys(layers).map(name => ({ name })),
    layers,
    lineage: { edges, stubs: [], stub_kinds: {}, order: Object.keys(layers), notes: [], tables: {}, ...extra },
  };
}

const warehouse = model(
  { src_orders: 'source', seed_status: 'seed', stg_orders: 'prep', int_orders: 'intermediate', fct_orders: 'mart', bridge_x: 'bridge', mystery: null },
  [
    { from: 'crm.orders', to: 'src_orders', source: 'sql' },
    { from: 'src_orders', to: 'stg_orders', source: 'sql' },
    { from: 'seed_status', to: 'stg_orders', source: 'manifest' },
    { from: 'stg_orders', to: 'int_orders', source: 'sql' },
    { from: 'int_orders', to: 'fct_orders', source: 'declared' },
    { from: 'src_orders', to: 'fct_orders', source: 'sql' },
    { from: 'fct_orders', to: 'bridge_x', source: 'sql' },
  ],
  { stubs: ['crm.orders'], stub_kinds: { 'crm.orders': 'source' } },
);

test('columns follow layer order with sources and seeds stacked together', () => {
  const { nodes } = lineage.layout(warehouse);
  assert.equal(nodes.src_orders.column, 'source');
  assert.equal(nodes.seed_status.column, 'source');
  assert.equal(nodes.src_orders.x, nodes.seed_status.x);
  assert.ok(nodes.seed_status.y > nodes.src_orders.y, 'seeds stack below sources');
  assert.equal(nodes.mystery.column, 'intermediate', 'unknown layers sit with intermediate models');
  const order = ['crm.orders', 'src_orders', 'stg_orders', 'int_orders', 'fct_orders', 'bridge_x'];
  order.slice(1).forEach((name, i) => assert.ok(nodes[name].x > nodes[order[i]].x, `${name} right of ${order[i]}`));
});

test('columns are top-aligned, not centred on the tallest column', () => {
  const m = model({ a: 'source', b: 'source', c: 'source', d: 'source', f: 'mart' }, [{ from: 'a', to: 'f', source: 'sql' }]);
  const { nodes, height } = lineage.layout(m, { sweeps: 0 });
  const top = lineage.DEFAULTS.marginY;
  assert.equal(nodes.a.y, top);
  assert.equal(nodes.f.y, top, 'a short column starts at the top');
  assert.ok(nodes.d.y + nodes.d.h <= height);
});

test('stubs sit in their own column left of sources', () => {
  const { nodes, columns } = lineage.layout(warehouse);
  assert.equal(nodes['crm.orders'].stub, true);
  assert.equal(nodes['crm.orders'].column, 'stub');
  assert.equal(columns[0].key, 'stub');
  assert.ok(nodes['crm.orders'].x + nodes['crm.orders'].w <= nodes.src_orders.x);
  assert.ok(nodes['crm.orders'].h < nodes.src_orders.h, 'stubs are compact pills');
});

test('edge endpoints missing from the DBML become stubs', () => {
  const { nodes } = lineage.layout(model({ stg_a: 'prep' }, [{ from: 'pkg_model', to: 'stg_a', source: 'manifest' }]));
  assert.equal(nodes.pkg_model.stub, true);
  assert.equal(nodes.pkg_model.column, 'stub');
});

test('empty columns are dropped', () => {
  const { columns } = lineage.layout(model({ stg_a: 'prep', fct_b: 'mart' }, [{ from: 'stg_a', to: 'fct_b', source: 'declared' }]));
  assert.deepEqual(columns.map(c => c.key), ['prep', 'mart']);
});

test('without sweeps, nodes in a column keep lineage order', () => {
  const m = model({ a: 'prep', b: 'prep', c: 'prep', d: 'prep' }, []);
  m.lineage.order = ['c', 'a', 'd', 'b'];
  const { nodes } = lineage.layout(m, { sweeps: 0 });
  const sorted = Object.keys(nodes).sort((x, y) => nodes[x].y - nodes[y].y);
  assert.deepEqual(sorted, ['c', 'a', 'd', 'b']);
  const swept = lineage.layout(m).nodes;
  assert.deepEqual(Object.keys(swept).sort((x, y) => swept[x].y - swept[y].y), ['c', 'a', 'd', 'b'], 'unconnected nodes keep order');
});

test('barycentre sweep removes an avoidable crossing', () => {
  const m = model({ a: 'source', b: 'source', y: 'prep', x: 'prep' }, [
    { from: 'a', to: 'x', source: 'sql' }, { from: 'b', to: 'y', source: 'sql' },
  ]);
  const before = lineage.layout(m, { sweeps: 0 }).nodes;
  assert.ok(before.y.y < before.x.y, 'lineage order alone would cross');
  const { nodes } = lineage.layout(m);
  assert.equal(Math.sign(nodes.a.y - nodes.b.y), Math.sign(nodes.x.y - nodes.y.y));
});

test('no two cards overlap', () => {
  const { nodes } = lineage.layout(warehouse);
  const list = Object.entries(nodes);
  list.forEach(([an, a], i) => list.slice(i + 1).forEach(([bn, b]) => {
    const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    assert.ok(!overlap, `${an} overlaps ${bn}`);
  }));
});

test('every edge starts and ends on its cards and keeps its source', () => {
  const { nodes, edges } = lineage.layout(warehouse);
  assert.equal(edges.length, warehouse.lineage.edges.length);
  edges.forEach(edge => {
    const a = nodes[edge.from], b = nodes[edge.to];
    const start = edge.points[0], end = edge.points[edge.points.length - 1];
    assert.ok([a.x, a.x + a.w].includes(start.x) && start.y >= a.y && start.y <= a.y + a.h, `${edge.from} start`);
    assert.ok([b.x, b.x + b.w].includes(end.x) && end.y >= b.y && end.y <= b.y + b.h, `${edge.to} end`);
  });
  assert.deepEqual(edges.map(e => e.source), warehouse.lineage.edges.map(e => e.source));
});

test('edges spanning columns route through gaps between cards', () => {
  const { nodes, edges } = lineage.layout(warehouse);
  const skip = edges.find(e => e.from === 'src_orders' && e.to === 'fct_orders');
  assert.equal(skip.points.length, 6, 'one waypoint pair per skipped column');
  const cards = Object.values(nodes);
  skip.points.slice(1, -1).forEach(p => cards.forEach(card => {
    assert.ok(!(p.x > card.x && p.x < card.x + card.w && p.y > card.y && p.y < card.y + card.h), 'waypoint inside a card');
  }));
  const through = skip.points.slice(1, -1);
  for (let i = 0; i < through.length; i += 2) {
    const y = through[i].y;
    cards.filter(card => card.x <= through[i].x && card.x + card.w >= through[i + 1].x)
      .forEach(card => assert.ok(y < card.y || y > card.y + card.h, 'straight run crosses a card'));
  }
});

test('backward and same-column edges get valid paths', () => {
  const m = model({ int_a: 'intermediate', fct_b: 'mart', int_c: 'intermediate' }, [
    { from: 'int_a', to: 'fct_b', source: 'sql' },
    { from: 'fct_b', to: 'int_c', source: 'sql' },
    { from: 'int_a', to: 'int_c', source: 'sql' },
  ]);
  const { nodes, edges } = lineage.layout(m);
  const back = edges.find(e => e.from === 'fct_b');
  assert.equal(back.points[0].x, nodes.fct_b.x, 'backward edge leaves from the left side');
  assert.equal(back.points.at(-1).x, nodes.int_c.x + nodes.int_c.w, 'and arrives on the right side');
  const loop = edges.find(e => e.from === 'int_a' && e.to === 'int_c');
  assert.equal(loop.loop, true);
  edges.forEach(edge => assert.match(lineage.pathD(edge.points), /^M [\d.]+ [\d.]+( [CL] [-\d. ]+)+$/));
});

test('closure follows data flow both ways and excludes the start', () => {
  const up = lineage.closure(warehouse.lineage.edges, 'fct_orders', 'up');
  assert.deepEqual([...up].sort(), ['crm.orders', 'int_orders', 'seed_status', 'src_orders', 'stg_orders']);
  const down = lineage.closure(warehouse.lineage.edges, 'stg_orders', 'down');
  assert.deepEqual([...down].sort(), ['bridge_x', 'fct_orders', 'int_orders']);
  const union = lineage.closure(warehouse.lineage.edges, ['int_orders', 'fct_orders'], 'down');
  assert.deepEqual([...union], ['bridge_x']);
});

test('empty lineage still lays out every table', () => {
  const { nodes, edges } = lineage.layout({ tables: [{ name: 'a' }], layers: {}, lineage: {} });
  assert.deepEqual(Object.keys(nodes), ['a']);
  assert.deepEqual(edges, []);
});

test('cards size to their names within limits, then wrap', () => {
  const opt = lineage.DEFAULTS;
  assert.deepEqual(lineage.cardSize(40), { w: opt.nodeW, h: opt.nodeH }, 'short names keep the minimum width');
  assert.deepEqual(lineage.cardSize(250), { w: 250 + opt.nodePadX, h: opt.nodeH });
  const wrapped = lineage.cardSize(600);
  assert.equal(wrapped.w, opt.nodeMaxW);
  assert.equal(wrapped.h, opt.nodeH + opt.lineH, 'one extra line');
  assert.equal(lineage.cardSize(900).h, opt.nodeH + 2 * opt.lineH);
});

test('a long name widens its whole column and never truncates', () => {
  const long = 'fct_subscription_churn_by_cohort_and_region';
  const m = model({ stg_a: 'prep', [long]: 'mart', fct_b: 'mart' }, [{ from: 'stg_a', to: long, source: 'sql' }, { from: 'stg_a', to: 'fct_b', source: 'sql' }]);
  const textWidth = name => 7 * name.length;
  const { nodes, columns } = lineage.layout(m, { textWidth });
  assert.equal(nodes[long].w, textWidth(long) + lineage.DEFAULTS.nodePadX);
  assert.equal(nodes.fct_b.w, nodes[long].w, 'cards in a column share its width');
  assert.equal(nodes.stg_a.w, lineage.DEFAULTS.nodeW);
  assert.equal(columns.find(c => c.key === 'mart').w, nodes[long].w);
  const huge = lineage.layout(m, { textWidth: name => name === long ? 700 : 50 }).nodes;
  assert.equal(huge[long].w, lineage.DEFAULTS.nodeMaxW);
  assert.ok(huge[long].h > lineage.DEFAULTS.nodeH, 'wrapped names make the card taller');
  assert.ok(huge.fct_b.y >= huge[long].y + huge[long].h || huge.fct_b.y + huge.fct_b.h <= huge[long].y, 'taller cards do not overlap');
});

test('first-open scroll starts at a column boundary', () => {
  const columns = [{ x: 12 }, { x: 300 }, { x: 600 }, { x: 900 }];
  assert.equal(lineage.scrollStart(columns, 12, 1000), 0);
  assert.equal(lineage.scrollStart(columns, 600, 1000), 576);
  // Clamped near the right end: the furthest column start that still fits.
  assert.equal(lineage.scrollStart(columns, 900, 700), 576);
  assert.equal(lineage.scrollStart(columns, 900, 100), 0);
});
