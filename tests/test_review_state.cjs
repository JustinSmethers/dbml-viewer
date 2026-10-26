const { test } = require('node:test');
const assert = require('node:assert/strict');
const review = require('../src/dbml_viewer/templates/review-state.js');

const NOW = '2026-10-23T12:00:00.000Z';
// Local noon, so the exported review date is the same in every time zone.
const DAY = new Date(2026, 9, 23, 12);

function sampleModel() {
  return {
    project: { name: 'demo' },
    tables: [
      {
        name: 'fct_segments',
        columns: [{ name: 'customer_id', type: 'int', pk: true, not_null: false, unique: false, note: '' }],
        charter: { grain: ['customer_id'], replaces: ['legacy_report'], validation: { evidence: 'https://example.com/v' } },
      },
      {
        name: 'marts_customers',
        columns: [{ name: 'customer_id', type: 'int', pk: true }, { name: 'email', type: 'varchar' }],
        charter: { grain: ['customer_id'] },
      },
      { name: 'prep_orders', columns: [{ name: 'order_id', type: 'int' }], charter: null },
    ],
    layers: { fct_segments: 'mart', marts_customers: 'mart', prep_orders: 'prep' },
    comparison: {
      revision: 'main', base: 'abc123',
      tables: {
        fct_segments: { status: 'new', details: [] },
        marts_customers: { status: 'modified', details: [] },
        legacy_report: { status: 'removed', details: [] },
        prep_orders: { status: 'unchanged', details: [] },
      },
    },
    lineage: { order: ['prep_orders', 'marts_customers', 'fct_segments'] },
    review: {
      walkthrough: ['marts_customers', 'legacy_report', 'fct_segments'],
      tables: {
        fct_segments: {
          findings: [
            { rule: 'grain-compatibility', severity: 'warning', message: 'aggregation required' },
            { rule: 'undocumented-columns', severity: 'info', message: 'no descriptions' },
          ],
          replaces: ['legacy_report'], replaced_by: [], overlap: [],
        },
        marts_customers: {
          findings: [
            { rule: 'pii-column-unknown', severity: 'error', message: 'PII column email2 is not a column' },
            { rule: 'layer-violation', severity: 'warning', message: 'reads a source', inferred: true },
          ],
          replaces: [], replaced_by: [], overlap: [],
        },
        prep_orders: { findings: [], replaces: [], replaced_by: [], overlap: [] },
      },
    },
  };
}

test('storage key is per project, else per page path', () => {
  assert.equal(review.storageKey('demo', '/tmp/a.html'), 'dbml-viewer:review:demo');
  const a = review.storageKey('', '/tmp/a.html');
  assert.match(a, /^dbml-viewer:review:path-[0-9a-f]{8}$/);
  assert.equal(review.storageKey(undefined, '/tmp/a.html'), a);
  assert.notEqual(review.storageKey('', '/tmp/b.html'), a, 'unrelated unnamed diagrams do not share sign-offs');
  assert.equal(review.storageKey(''), review.storageKey(null, ''));
});

test('model hash covers columns and charter but not notes', () => {
  const model = sampleModel();
  const table = model.tables[1];
  const base = review.modelHash(table);
  assert.match(base, /^[0-9a-f]{8}$/);
  assert.equal(review.modelHash({ ...table, columns: table.columns.map(c => ({ ...c, note: 'documented' })) }), base);
  assert.notEqual(review.modelHash({ ...table, columns: [...table.columns, { name: 'x', type: 'int' }] }), base);
  assert.notEqual(review.modelHash({ ...table, charter: { grain: ['email'] } }), base);
  assert.notEqual(review.modelHash({ ...table, columns: [{ ...table.columns[0], not_null: true }, table.columns[1]] }), base);
  assert.equal(review.modelHash(null), review.modelHash({ columns: [], charter: null }));
});

test('model hash covers refs with the table as an endpoint, in any order', () => {
  const table = sampleModel().tables[1];
  const own = { from_table: 'fct_segments', from_column: 'customer_id', to_table: 'marts_customers', to_column: 'customer_id', type: '>' };
  const other = { from_table: 'prep_orders', from_column: 'order_id', to_table: 'fct_segments', to_column: 'order_id', type: '>' };
  const base = review.modelHash(table, [other]);
  assert.equal(base, review.modelHash(table), 'refs between other tables are ignored');
  const joined = review.modelHash(table, [own, other]);
  assert.notEqual(joined, base);
  assert.equal(review.modelHash(table, [other, own]), joined);
  assert.notEqual(review.modelHash(table, [{ ...own, to_column: 'email' }]), joined);
});

test('parseState tolerates bad storage and normalizes entries', () => {
  assert.deepEqual(review.parseState('not json'), {});
  assert.deepEqual(review.parseState('[1,2]'), {});
  assert.deepEqual(review.parseState(null), {});
  const state = review.parseState(JSON.stringify({ a: { status: 'bogus', comment: 3 }, b: { status: 'approved', comment: 'ok' } }));
  assert.deepEqual(state.a, review.emptyEntry());
  assert.equal(state.b.status, 'approved');
  assert.equal(state.b.comment, 'ok');
});

test('step order is the walkthrough, then every other model in lineage order', () => {
  const model = sampleModel();
  assert.deepEqual(review.stepOrder(model), ['marts_customers', 'legacy_report', 'fct_segments', 'prep_orders']);
  assert.equal(review.stepLabel(model, 'legacy_report'), '2 of 3 in walkthrough');
  assert.equal(review.stepLabel(model, 'prep_orders'), '4 of 4 models');
  assert.equal(review.stepLabel(model, 'nowhere'), '3 in walkthrough');
  // Without a walkthrough, the changed models (in lineage order) lead.
  model.review.walkthrough = [];
  assert.deepEqual(review.stepOrder(model), ['marts_customers', 'fct_segments', 'legacy_report', 'prep_orders']);
  // Tables lineage does not order still appear, last.
  model.tables.push({ name: 'orphan', columns: [] });
  delete model.comparison;
  assert.deepEqual(review.stepOrder(model), ['prep_orders', 'marts_customers', 'fct_segments', 'orphan']);
  assert.equal(review.stepLabel(model, 'marts_customers'), '2 of 4 models');
});

test('unmapped source changes are never stepped through, reviewed or counted as changes', () => {
  const model = sampleModel();
  model.review.walkthrough = ['models/x.sql', 'marts_customers'];
  model.comparison.tables['models/x.sql'] = { status: 'modified', unmapped: true, details: [] };
  assert.deepEqual(review.walkthroughOf(model), ['marts_customers']);
  assert.ok(!review.stepOrder(model).includes('models/x.sql'));
  assert.deepEqual(review.unmappedChanges(model), [{ path: 'models/x.sql', status: 'modified' }]);
  model.review.walkthrough = [];
  assert.ok(!review.walkthroughOf(model).includes('models/x.sql'));
});

test('under review only when the comparison found new, modified or removed models', () => {
  const model = sampleModel();
  assert.equal(review.underReview(model), true, 'derived from table statuses without counts');
  model.comparison.counts = { new: 0, modified: 0, removed: 0, unchanged: 4 };
  assert.equal(review.underReview(model), false);
  model.comparison.counts.removed = 1;
  assert.equal(review.underReview(model), true);
  delete model.comparison;
  assert.equal(review.underReview(model), false);
});

test('catalog lists every model in layer order with purpose, grain and owner', () => {
  const model = sampleModel();
  model.tables.push({ name: 'src_orders', prose: 'Raw orders from the shop. Loaded hourly.', columns: [{ name: 'id', type: 'int', pk: true }], charter: null });
  model.layers.src_orders = 'source';
  model.tables[1].charter = { purpose: 'Customer profile', grain: ['customer_id'], owner: 'crm-team', consumers: ['CRM sync'] };
  const rows = review.catalog(model);
  assert.deepEqual(rows.map(row => row.name), ['src_orders', 'prep_orders', 'marts_customers', 'fct_segments']);
  assert.deepEqual(rows[0], { name: 'src_orders', layer: 'source', purpose: 'Raw orders from the shop.', grain: ['id'], grainSource: 'pk', owner: null, consumers: [], counts: { error: 0, warning: 0, info: 0 } });
  const mart = rows[2];
  assert.equal(mart.purpose, 'Customer profile');
  assert.equal(mart.grainSource, 'charter');
  assert.equal(mart.owner, 'crm-team');
  assert.deepEqual(mart.consumers, ['CRM sync']);
  assert.deepEqual(mart.counts, { error: 1, warning: 1, info: 0 });
  assert.deepEqual(review.synopsis(model, 'prep_orders'), { purpose: null, grain: [], grainSource: null });
});

test('summary lists walkthrough models, or models with findings without a comparison, plus reviewed models', () => {
  const model = sampleModel();
  assert.deepEqual(review.summaryNames(model), ['marts_customers', 'legacy_report', 'fct_segments']);
  const state = { prep_orders: { ...review.emptyEntry(), comment: 'check this' }, ghost: { ...review.emptyEntry(), status: 'approved' } };
  assert.deepEqual(review.summaryNames(model, state), ['marts_customers', 'legacy_report', 'fct_segments', 'prep_orders']);
  delete model.comparison;
  model.review.walkthrough = [];
  assert.deepEqual(review.summaryNames(model), ['marts_customers', 'fct_segments']);
});

test('manual checks and finding ids are stable', () => {
  assert.deepEqual(review.MANUAL_CHECKS.map(item => item.id), ['grain', 'evidence', 'consumers']);
  const [error] = sampleModel().review.tables.marts_customers.findings;
  assert.match(review.findingId(error), /^finding:pii-column-unknown:[0-9a-f]{8}$/);
  assert.notEqual(review.findingId(error), review.findingId({ ...error, message: 'other' }));
});

test('approval is gated on error findings and validation evidence unless overridden', () => {
  const model = sampleModel();
  assert.deepEqual(review.canApprove(model, 'fct_segments', review.emptyEntry()), { ok: true, blockers: [], overridden: false });
  const gate = review.canApprove(model, 'marts_customers', review.emptyEntry());
  assert.equal(gate.ok, false);
  assert.deepEqual(gate.blockers, ['1 error finding', 'no validation evidence']);

  const refused = review.setStatus(model, 'marts_customers', review.emptyEntry(), 'approved', NOW);
  assert.deepEqual(refused.blocked, gate.blockers);
  assert.equal(refused.entry.status, null);

  const withReason = review.update(review.emptyEntry(), { override: 'PII fix tracked in JIRA-12' }, NOW);
  assert.equal(review.canApprove(model, 'marts_customers', withReason).overridden, true);
  const approved = review.setStatus(model, 'marts_customers', withReason, 'approved', NOW);
  assert.equal(approved.blocked, null);
  assert.equal(approved.entry.status, 'approved');
  assert.equal(approved.entry.hash, review.modelHash(model.tables[1]));
  assert.equal(approved.entry.at, NOW);
  // Whitespace is not a reason.
  assert.equal(review.canApprove(model, 'marts_customers', { override: '   ' }).ok, false);
  // Other statuses are never gated.
  assert.equal(review.setStatus(model, 'marts_customers', review.emptyEntry(), 'changes', NOW).entry.status, 'changes');
});

test('validation evidence is required only of new models and marts or bridges', () => {
  const model = sampleModel();
  // Existing prep model with no charter; removed model; model with no DBML card.
  assert.deepEqual(review.blockers(model, 'prep_orders'), []);
  assert.deepEqual(review.blockers(model, 'legacy_report'), []);
  model.review.tables.legacy_report = { layer: 'mart', findings: [{ rule: 'x', severity: 'error', message: 'still an error' }] };
  assert.deepEqual(review.blockers(model, 'legacy_report'), ['1 error finding'], 'errors block every model');
  // New model: evidence required when it has a charter or required keys.
  model.comparison.tables.prep_orders.status = 'new';
  model.review.tables.prep_orders.required = ['grain'];
  assert.deepEqual(review.blockers(model, 'prep_orders'), ['no validation evidence']);
  model.review.tables.prep_orders.required = [];
  assert.deepEqual(review.blockers(model, 'prep_orders'), [], 'no charter and no required keys');
  model.tables[2].charter = { grain: ['order_id'] };
  assert.deepEqual(review.blockers(model, 'prep_orders'), ['no validation evidence']);
  // Without required data from Python, sources and seeds have no required keys.
  delete model.review.tables.prep_orders.required;
  model.tables[2].charter = null;
  model.layers.prep_orders = 'source';
  assert.deepEqual(review.blockers(model, 'prep_orders'), []);
  // Unchanged mart or bridge with a charter.
  model.comparison.tables.prep_orders.status = 'unchanged';
  model.layers.prep_orders = 'bridge';
  model.tables[2].charter = { grain: ['order_id'] };
  assert.deepEqual(review.blockers(model, 'prep_orders'), ['no validation evidence']);
  assert.equal(review.needsEvidence(model, 'prep_orders'), true);
});

test('layer falls back to the review entry for removed models', () => {
  const model = sampleModel();
  assert.equal(review.layerOf(model, 'marts_customers'), 'mart');
  assert.equal(review.layerOf(model, 'legacy_report'), null);
  model.review.tables.legacy_report = { layer: 'mart', findings: [] };
  assert.equal(review.layerOf(model, 'legacy_report'), 'mart');
});

test('an entry is stale when the model changed after its status was set', () => {
  const model = sampleModel();
  const { entry } = review.setStatus(model, 'fct_segments', review.emptyEntry(), 'approved', NOW);
  assert.equal(review.isStale(entry, model, 'fct_segments'), false);
  model.tables[0].columns.push({ name: 'segment', type: 'varchar' });
  assert.equal(review.isStale(entry, model, 'fct_segments'), true);
  assert.equal(entry.status, 'approved');
  // Comments and checks keep the reviewed hash.
  const commented = review.update(entry, { comment: 'later note', checks: { grain: true } }, NOW);
  assert.equal(review.isStale(commented, model, 'fct_segments'), true);
  assert.equal(commented.checks.grain, true);
  // Re-setting the status confirms it for the current model.
  assert.equal(review.isStale(review.setStatus(model, 'fct_segments', commented, 'approved', NOW).entry, model, 'fct_segments'), false);
  // Ref changes touching the model also make it stale.
  const fresh = review.setStatus(model, 'fct_segments', commented, 'approved', NOW).entry;
  model.refs = [{ from_table: 'fct_segments', from_column: 'customer_id', to_table: 'marts_customers', to_column: 'customer_id' }];
  assert.equal(review.isStale(fresh, model, 'fct_segments'), true);
  // No status, no stale flag.
  assert.equal(review.isStale(review.update(review.emptyEntry(), { comment: 'x', hash: 'deadbeef' }, NOW), model, 'fct_segments'), false);
});

test('acknowledged findings are no longer open', () => {
  const model = sampleModel();
  const [, warning] = model.review.tables.marts_customers.findings;
  const entry = review.update(review.emptyEntry(), { checks: { [review.findingId(warning)]: true } }, NOW);
  assert.deepEqual(review.openFindings(model, 'marts_customers', entry).map(f => f.rule), ['pii-column-unknown']);
  assert.deepEqual(review.acknowledgedFindings(model, 'marts_customers', entry).map(f => f.rule), ['layer-violation']);
  // Info findings are neither open nor acknowledgeable.
  assert.deepEqual(review.openFindings(model, 'fct_segments', review.emptyEntry()).map(f => f.rule), ['grain-compatibility']);
});

test('replaced-by is derived for removed models', () => {
  const model = sampleModel();
  assert.deepEqual(review.replacedBy(model, 'legacy_report'), ['fct_segments']);
  assert.deepEqual(review.replacedBy(model, 'prep_orders'), []);
});

test('markdown export has counts, per-model status, questions and replacements', () => {
  const model = sampleModel();
  const state = {};
  state.fct_segments = review.setStatus(model, 'fct_segments', review.emptyEntry(), 'approved', NOW).entry;
  state.marts_customers = review.update(review.setStatus(model, 'marts_customers', review.emptyEntry(), 'question', NOW).entry,
    { comment: 'Is email\nreally PII here?' }, NOW);
  model.tables[0].charter = { ...model.tables[0].charter, owner: 'analytics-eng' }; // makes fct_segments stale
  const text = review.markdown(model, state, DAY);
  assert.equal(text, [
    '## Model review: demo · 2026-10-23',
    '',
    'Baseline: main (merge base abc123)',
    '',
    '**3 models:** 1 approved · 0 needs changes · 1 open question · 1 not reviewed · 1 stale',
    '',
    '- `marts_customers` (modified mart): **Open question**',
    '  - Grain: customer_id',
    '  - Comment: Is email really PII here?',
    '  - Blocking:',
    '    - error: PII column email2 is not a column',
    '  - Warnings:',
    '    - warning: reads a source (inferred)',
    '- `legacy_report` (removed): **Not reviewed**',
    '- `fct_segments` (new mart): **Approved** (stale: model changed since review)',
    '  - Grain: customer_id',
    '  - Warnings:',
    '    - warning: aggregation required',
    '',
    '### Unresolved questions',
    '',
    '- `marts_customers`: Is email really PII here?',
    '',
    '### Replaced models',
    '',
    '- `legacy_report` replaced by `fct_segments` (removed in this branch)',
    '',
  ].join('\n'));
});

test('markdown names what an override overrode and keeps acknowledged findings open', () => {
  const model = sampleModel();
  const [error, warning] = model.review.tables.marts_customers.findings;
  model.tables[1].charter = { purpose: 'Customer profile', grain: ['customer_id', 'as_of'] };
  model.lineage.tables = { marts_customers: { consumers_affected: ['Finance dashboard', 'CRM sync'] } };
  const withReason = review.update(review.emptyEntry(), { override: 'Fix in follow-up PR', checks: { [review.findingId(error)]: true, grain: true, consumers: true } }, NOW);
  const entry = review.setStatus(model, 'marts_customers', withReason, 'approved', NOW).entry;
  const text = review.markdown(model, { marts_customers: entry }, DAY);
  assert.ok(text.includes([
    '- `marts_customers` (modified mart): **Approved**',
    '  - Purpose: Customer profile · Grain: customer_id, as_of',
    '  - Affected consumers: Finance dashboard, CRM sync',
    '  - Override: Fix in follow-up PR (1 error finding; no validation evidence)',
    '  - Warnings:',
    '    - warning: reads a source (inferred)',
    '  - Acknowledged (still open):',
    '    - error: PII column email2 is not a column',
    '  - Checks: grain confirmed, consumers confirmed',
    '',
  ].join('\n')), text);
  assert.doesNotMatch(text, /Unresolved questions/);
  const row = review.rows(model, { marts_customers: entry }).find(item => item.name === 'marts_customers');
  assert.equal(row.overridden, true);
  // A reason with nothing to override is not an override.
  const plain = review.setStatus(model, 'fct_segments', review.update(review.emptyEntry(), { override: 'n/a' }, NOW), 'approved', NOW).entry;
  assert.doesNotMatch(review.markdown(model, { fct_segments: plain }), /Override/);
  assert.equal(review.rows(model, { fct_segments: plain }).find(item => item.name === 'fct_segments').overridden, false);
  assert.equal(warning.inferred, true);
  // Consumers are listed for modified models only.
  model.lineage.tables.fct_segments = { consumers_affected: ['Marketing'] };
  assert.doesNotMatch(review.markdown(model, {}), /Marketing/);
  delete model.comparison;
  assert.match(review.markdown(model, {}), /Baseline: no branch comparison/);
});

test('clearing the override withdraws a blocked approval but keeps the comment', () => {
  const model = sampleModel();
  const reason = review.update(review.emptyEntry(), { override: 'Tracked in JIRA-12', comment: 'Looks right' }, NOW, model, 'marts_customers');
  const approved = review.setStatus(model, 'marts_customers', reason, 'approved', NOW).entry;
  assert.equal(approved.status, 'approved');
  const edited = review.update(approved, { override: 'Tracked in JIRA-13' }, NOW, model, 'marts_customers');
  assert.equal(edited.status, 'approved', 'a changed reason is still a reason');
  const cleared = review.update(approved, { override: '  ' }, NOW, model, 'marts_customers');
  assert.equal(cleared.status, null);
  assert.equal(cleared.hash, null);
  assert.equal(cleared.comment, 'Looks right');
  assert.doesNotMatch(review.markdown(model, { marts_customers: cleared }, DAY), /\*\*Approved/);
  // Unblocked models keep their approval without a reason.
  const plain = review.setStatus(model, 'fct_segments', review.emptyEntry(), 'approved', NOW).entry;
  assert.equal(review.update(plain, { override: '' }, NOW, model, 'fct_segments').status, 'approved');
  // Without model context, update only merges.
  assert.equal(review.update(approved, { override: '' }, NOW).status, 'approved');
});

test('an approval that is blocked without an override is exported as invalid', () => {
  const model = sampleModel();
  const state = { marts_customers: { ...review.emptyEntry(), status: 'approved', hash: review.modelHash(model.tables[1]) } };
  const row = review.rows(model, state).find(item => item.name === 'marts_customers');
  assert.equal(row.invalid, true);
  assert.equal(row.overridden, false);
  assert.equal(review.statusLabel(row), 'Approved (invalid: blocked without override)');
  assert.match(review.markdown(model, state, DAY), /- `marts_customers` \(modified mart\): \*\*Approved \(invalid: blocked without override\)\*\*\n/);
  assert.equal(review.exportJson(model, state, NOW).models.marts_customers.invalid, true);
  const valid = review.rows(model, { fct_segments: { ...review.emptyEntry(), status: 'approved' } }).find(item => item.name === 'fct_segments');
  assert.equal(valid.invalid, false);
  assert.equal(review.statusLabel(valid), 'Approved');
});

test('open-question comments are unresolved questions; needs-changes comments are action items', () => {
  const model = sampleModel();
  const [error] = model.review.tables.marts_customers.findings;
  const changes = review.setStatus(model, 'marts_customers', review.emptyEntry(), 'changes', NOW).entry;
  const state = {
    // Ticks do not hide findings on a needs-changes model.
    marts_customers: review.update(changes, { comment: 'Rename the column', checks: { [review.findingId(error)]: true } }, NOW),
    fct_segments: review.update(review.setStatus(model, 'fct_segments', review.emptyEntry(), 'question', NOW).entry, { comment: 'Weekly or daily?' }, NOW),
    legacy_report: review.update(review.emptyEntry(), { comment: 'Who still reads this?' }, NOW),
  };
  const text = review.markdown(model, state, DAY);
  assert.ok(text.includes([
    '- `marts_customers` (modified mart): **Needs changes**',
    '  - Grain: customer_id',
    '  - Comment: Rename the column',
    '  - Blocking:',
    '    - error: PII column email2 is not a column',
    '  - Warnings:',
    '    - warning: reads a source (inferred)',
    '- ',
  ].join('\n')), text);
  assert.doesNotMatch(text, /Acknowledged/);
  const questions = text.split('### Unresolved questions')[1].split('###')[0];
  assert.equal(questions.trim(), '- `fct_segments`: Weekly or daily?');
  assert.ok(text.endsWith('### Action items\n\n- `marts_customers`: Rename the column\n'), 'action items come last');
});

test('JSON export carries entries, stale flags and current hashes', () => {
  const model = sampleModel();
  const state = { fct_segments: review.setStatus(model, 'fct_segments', review.emptyEntry(), 'changes', NOW).entry };
  const json = review.exportJson(model, state, NOW);
  assert.equal(json.project, 'demo');
  assert.deepEqual(json.baseline, { revision: 'main', base: 'abc123' });
  assert.equal(json.exported_at, NOW);
  assert.deepEqual(Object.keys(json.models), ['marts_customers', 'legacy_report', 'fct_segments']);
  assert.equal(json.models.fct_segments.status, 'changes');
  assert.equal(json.models.fct_segments.stale, false);
  assert.equal(json.models.fct_segments.current_hash, json.models.fct_segments.hash);
  assert.deepEqual(json.models.marts_customers.findings, { error: 1, warning: 1, info: 0 });
});

test('status counts and worst severity', () => {
  const model = sampleModel();
  const counts = review.statusCounts(review.rows(model, { fct_segments: { ...review.emptyEntry(), status: 'approved' } }));
  assert.deepEqual(counts, { approved: 1, changes: 0, question: 0, unreviewed: 2, stale: 0 });
  assert.equal(review.worstSeverity(model.review.tables.marts_customers.findings), 'error');
  assert.equal(review.worstSeverity(model.review.tables.fct_segments.findings), 'warning');
  assert.equal(review.worstSeverity([]), null);
});
