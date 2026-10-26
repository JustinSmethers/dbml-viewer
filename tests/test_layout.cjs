const { test } = require('node:test');
const assert = require('node:assert/strict');
const layout = require('../src/dbml_viewer/templates/layout.js');

test('layer names support separate processing ranks and naming variations', () => {
  const names = [
    ['source_orders', 'src_orders', 'sources', 'source.users', 'raw_products'],
    ['seed_status', 'seeds', 'SeedsLayer'],
    ['prep_orders', 'prepared_orders', 'prepped_orders', 'staging_layer', 'PrepLayer'],
    ['int_orders', 'inter_orders', 'intermediate_layer', 'transformations'],
    ['bridge_customers', 'bridges', 'mart_bridge_customers', 'BridgeLayer', 'warehouse.bridge_customers'],
    ['marts', 'reports_revenue', 'reporting_layer', 'dim_products', 'orders_fact'],
  ];
  names.forEach((list, stage) => list.forEach(name => assert.equal(layout.stageName(name), stage, name)));
  for (const name of ['print_jobs', 'internal_users', 'department', 'customers', 'cambridge_customers', '_default']) assert.equal(layout.stageName(name), null, name);
});

test('schema-qualified names classify the bare name before the schema', () => {
  const cases = {
    'analytics.stg_orders': 2, 'analytics.int_orders': 3, 'reporting.source_orders': 0,
    'staging.dim_customers': 5, 'Analytics.StgOrders': 2,
    'analytics.customers': 5, 'staging.customers': 2, 'warehouse.customers': null,
  };
  Object.entries(cases).forEach(([name, stage]) => assert.equal(layout.stageName(name), stage, name));
});

test('sources and seeds stack in separate blocks to the left of prep', () => {
  const model = {
    tables: [
      { name: 'source_orders', group: 'prep_layer' },
      { name: 'seed_status', group: 'prep_layer' },
      { name: 'country_codes', group: 'seeds' },
      { name: 'customers', group: 'sources' },
      { name: 'prepared_orders', group: 'prep_layer' },
      { name: 'int_orders', group: '_default' },
      { name: 'mart_orders', group: '_default' },
    ],
    refs: [
      { from_table: 'prepared_orders', to_table: 'source_orders' },
      { from_table: 'int_orders', to_table: 'seed_status' },
      { from_table: 'mart_orders', to_table: 'int_orders' },
    ],
  };
  const sizes = Object.fromEntries(model.tables.map(t => [t.name, { w: 260, h: 140 }]));
  const positions = layout.layered(model, sizes, layout.stages(model));
  assert.equal(positions.customers.x, positions.source_orders.x);
  assert.equal(positions.country_codes.x, positions.seed_status.x);
  assert.equal(positions.source_orders.x, positions.seed_status.x);
  const sourceBottom = Math.max(...['source_orders', 'customers'].map(name => positions[name].y + sizes[name].h));
  const seedTop = Math.min(...['seed_status', 'country_codes'].map(name => positions[name].y));
  assert.ok(sourceBottom < seedTop, 'source and seed categories do not overlap');
  const ranks = ['source_orders', 'prepared_orders', 'int_orders', 'mart_orders'];
  for (let i = 1; i < ranks.length; i++) assert.ok(positions[ranks[i - 1]].x + 260 < positions[ranks[i]].x);
  assert.equal(Object.keys(positions).length, model.tables.length);
});

test('visual categories separate bridges from marts and seeds from sources', () => {
  const model = { tables: [
    { name: 'source_orders', group: 'prep_layer' },
    { name: 'seed_status', group: 'prep_layer' },
    { name: 'prep_orders', group: 'prep_layer' },
    { name: 'mart_orders', group: 'marts' },
    { name: 'bridge_orders', group: 'marts' },
  ], refs: [] };
  const groups = layout.groups(model, layout.stages(model));
  assert.deepEqual(groups.map(group => group.name), ['Sources', 'Seeds', 'prep layer', 'marts', 'Bridges']);
  assert.deepEqual(groups.find(group => group.name === 'Bridges').members, ['bridge_orders']);
  assert.deepEqual(groups.find(group => group.name === 'marts').members, ['mart_orders']);
  assert.equal(new Set(groups.map(group => group.key)).size, 5);
});

test('group precedence and neighbour inference do not depend on input order', () => {
  const model = { tables: [{ name: 'prep_users', group: 'marts' }, { name: 'unnamed', group: 'domain' }, { name: 'disconnected', group: '_default' }], refs: [{ from_table: 'unnamed', to_table: 'prep_users' }] };
  assert.deepEqual(layout.stages(model), { prep_users: 5, unnamed: 5, disconnected: null });
  assert.deepEqual(layout.stages({ ...model, tables: [...model.tables].reverse() }), layout.stages(model));
});

test('bridges linking two marts keep a separate lane inside a mart group', () => {
  const model = {
    tables: ['mart_customers', 'mart_products', 'bridge_customer_products'].map(name => ({ name, group: 'mart_layer' })),
    refs: [
      { from_table: 'bridge_customer_products', to_table: 'mart_customers' },
      { from_table: 'bridge_customer_products', to_table: 'mart_products' },
    ],
  };
  const sizes = Object.fromEntries(model.tables.map(t => [t.name, { w: 260, h: 140 }]));
  const ranks = layout.stages(model);
  assert.deepEqual(ranks, { mart_customers: 5, mart_products: 5, bridge_customer_products: 4 });
  const positions = layout.layered(model, sizes, ranks);
  assert.equal(positions.mart_customers.x, 72, 'empty earlier lanes are omitted');
  assert.ok(positions.mart_customers.x + 260 < positions.bridge_customer_products.x);
  assert.equal(positions.mart_customers.x, positions.mart_products.x);
  assert.deepEqual(layout.stages({ ...model, tables: [...model.tables].reverse() }), ranks);
});

test('bridge groups are distinct but neighbour inference never invents a bridge role', () => {
  const model = {
    tables: [
      { name: 'customer_products', group: 'bridge_layer' },
      { name: 'int_customers', group: '_default' },
      { name: 'mart_products', group: '_default' },
      { name: 'unnamed', group: '_default' },
      { name: 'bridge_neighbour', group: '_default' },
      { name: 'unknown', group: '_default' },
    ],
    refs: [
      { from_table: 'unnamed', to_table: 'int_customers' },
      { from_table: 'unnamed', to_table: 'mart_products' },
      { from_table: 'unknown', to_table: 'bridge_neighbour' },
    ],
  };
  assert.equal(layout.stages(model).customer_products, 4);
  assert.equal(layout.stages(model).unnamed, 5);
  assert.equal(layout.stages(model).unknown, 5);
});

test('semantic stages stay left-to-right despite backwards refs, cycles and isolated nodes', () => {
  const model = { tables: ['prep_a', 'prepared_b', 'inter_a', 'mart_a', 'reports_isolated'].map(name => ({ name, group: 'one_mixed_group' })), refs: [{ from_table: 'mart_a', to_table: 'prep_a' }, { from_table: 'prep_a', to_table: 'mart_a' }] };
  const sizes = Object.fromEntries(model.tables.map(t => [t.name, { w: 260, h: 140 }]));
  const positions = layout.layered(model, sizes, layout.stages(model));
  assert.ok(positions.prep_a.x + 260 < positions.inter_a.x);
  assert.ok(positions.inter_a.x + 260 < positions.mart_a.x);
  assert.equal(positions.mart_a.x, positions.reports_isolated.x);
  const values = Object.values(positions);
  for (let i = 0; i < values.length; i++) for (let j = i + 1; j < values.length; j++) {
    const a = values[i], b = values[j];
    assert.ok(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);
  }
});

test('ordering reduces join distance using column ports', () => {
  const model = { tables: ['prep_a', 'prep_b', 'int_a', 'int_b'].map(name => ({ name, group: '_default' })), refs: [{ from_table: 'int_a', from_column: 'id', to_table: 'prep_b', to_column: 'id' }, { from_table: 'int_b', from_column: 'id', to_table: 'prep_a', to_column: 'id' }] };
  const sizes = Object.fromEntries(model.tables.map(t => [t.name, { w: 240, h: 100, ports: { id: 40 } }]));
  const initial = { prep_a: { y: 0 }, prep_b: { y: 200 }, int_a: { y: 0 }, int_b: { y: 200 } };
  const p = layout.layered(model, sizes, layout.stages(model), initial);
  assert.equal(p.prep_b.y, p.int_a.y);
  assert.equal(p.prep_a.y, p.int_b.y);
});

function checkClear(points, boxes) {
  for (let i = 1; i < points.length; i++) {
    assert.ok(points[i - 1].x === points[i].x || points[i - 1].y === points[i].y, 'axis-aligned segments');
    for (const box of boxes) assert.equal(layout.intersects(points[i - 1], points[i], box), false, 'route avoids table interiors');
  }
}
test('adjacent tables get a shortest Manhattan route', () => {
  const boxes = [{ x: 0, y: 0, w: 240, h: 160 }, { x: 400, y: 100, w: 240, h: 160 }];
  const a = { x: 240, y: 70 }, b = { x: 400, y: 180 };
  const points = layout.route(a, b, 'right', 'left', boxes);
  checkClear(points, boxes);
  assert.equal(layout.length(points), 270);
  assert.ok(points.length <= 4, 'at most two bends');
});
test('same-lane joins leave from the same outside side', () => {
  const boxes = [{ x: 100, y: 0, w: 240, h: 100 }, { x: 100, y: 200, w: 240, h: 100 }];
  checkClear(layout.route({ x: 100, y: 50 }, { x: 100, y: 250 }, 'left', 'left', boxes), boxes);
});
test('skipped layers and complex obstructions route around cards', () => {
  const boxes = [{ x: 0, y: 100, w: 100, h: 100 }, { x: 600, y: 100, w: 100, h: 100 }, { x: 150, y: 40, w: 140, h: 240 }, { x: 350, y: 0, w: 140, h: 220 }];
  const points = layout.route({ x: 100, y: 150 }, { x: 600, y: 150 }, 'right', 'left', boxes);
  checkClear(points, boxes);
  assert.ok(layout.length(points) > 500);
});
test('self-reference remains visible and outside its own card', () => {
  const box = { x: 100, y: 100, w: 240, h: 160 };
  const points = layout.route({ x: 340, y: 160 }, { x: 340, y: 160 }, 'right', 'right', [box]);
  checkClear(points, [box]);
  assert.ok(layout.length(points) > 80);
});
test('visibility search handles a corridor requiring multiple detours', () => {
  const boxes = [{ x: 0, y: 100, w: 100, h: 100 }, { x: 600, y: 100, w: 100, h: 100 }, { x: 150, y: 0, w: 100, h: 260 }, { x: 80, y: 280, w: 300, h: 100 }, { x: 80, y: -80, w: 300, h: 160 }, { x: 350, y: 200, w: 100, h: 300 }];
  const points = layout.route({ x: 100, y: 150 }, { x: 600, y: 150 }, 'right', 'left', boxes);
  checkClear(points, boxes);
  assert.ok(points.length > 4);
});
test('parallel refs use distinct channels', () => {
  const boxes = [{ x: 0, y: 0, w: 240, h: 160 }, { x: 400, y: 100, w: 240, h: 160 }];
  const a = { x: 240, y: 70 }, b = { x: 400, y: 180 };
  const first = layout.route(a, b, 'right', 'left', boxes, 0), second = layout.route(a, b, 'right', 'left', boxes, 1);
  assert.notDeepEqual(first, second);
  checkClear(second, boxes);
});
test('empty model has finite, empty layout', () => {
  assert.deepEqual(layout.layered({ tables: [], refs: [] }, {}, {}), {});
});

function sharedLength(a, b) {
  let total = 0;
  for (let i = 1; i < a.length; i++) for (let j = 1; j < b.length; j++) {
    const p = a[i - 1], q = a[i], r = b[j - 1], s = b[j];
    const vertical = p.x === q.x;
    if (vertical !== (r.x === s.x) || (vertical ? p.x !== r.x : p.y !== r.y)) continue;
    const amount = Math.min(Math.max(vertical ? p.y : p.x, vertical ? q.y : q.x), Math.max(vertical ? r.y : r.x, vertical ? s.y : s.x)) - Math.max(Math.min(vertical ? p.y : p.x, vertical ? q.y : q.x), Math.min(vertical ? r.y : r.x, vertical ? s.y : s.x));
    total += Math.max(0, amount);
  }
  return total;
}

test('joins between different table pairs do not merge into a corridor bus', () => {
  const boxes = Array.from({ length: 6 }, (_, i) => ({ x: i < 3 ? 0 : 500, y: (i % 3) * 180, w: 240, h: 120 }));
  const occupied = [];
  for (let i = 0; i < 3; i++) {
    const a = boxes[i], b = boxes[5 - i];
    const points = layout.route({ x: a.x + a.w, y: a.y + 40 }, { x: b.x, y: b.y + 80 }, 'right', 'left', boxes, i, occupied);
    checkClear(points, boxes);
    occupied.forEach(previous => assert.equal(sharedLength(points, previous), 0));
    occupied.push(points);
  }
});

test('fan-out joins keep separate tracks at a crowded destination column', () => {
  const boxes = [{ x: 0, y: 0, w: 240, h: 120 }, { x: 0, y: 180, w: 240, h: 120 }, { x: 0, y: 360, w: 240, h: 120 }, { x: 500, y: 180, w: 240, h: 120 }];
  const occupied = [];
  for (let i = 0; i < 3; i++) {
    const a = boxes[i];
    const points = layout.route({ x: 240, y: a.y + 60 }, { x: 500, y: 240 + (i - 1) * 7 }, 'right', 'left', boxes, i, occupied);
    checkClear(points, boxes);
    occupied.forEach(previous => assert.equal(sharedLength(points, previous), 0));
    occupied.push(points);
  }
});
