// Review sign-off state: pure functions shared by the viewer and the Node
// tests. No DOM or storage access; review-panel.js wires these to the page.
const DBMLReview = (() => {
  const STATUSES = ['approved', 'changes', 'question'];
  const STATUS_LABELS = { approved: 'Approved', changes: 'Needs changes', question: 'Open question', null: 'Not reviewed' };
  const MANUAL_CHECKS = [
    { id: 'grain', label: 'Grain confirmed' },
    { id: 'evidence', label: 'Validation evidence reviewed' },
    { id: 'consumers', label: 'Consumers confirmed' },
  ];
  const SEVERITIES = ['error', 'warning', 'info'];
  // Branch change labels shared by diagram badges, lineage cards and the panel.
  const CHANGE_LABELS = { new: '+ New', modified: '~ Edited', removed: '− Removed' };
  const PRESENTATION_LAYERS = ['mart', 'bridge'];

  // Unnamed projects are keyed by page path, so unrelated file:// diagrams
  // don't share sign-offs.
  function storageKey(projectName, pathname = '') {
    return 'dbml-viewer:review:' + (projectName || 'path-' + hashString(String(pathname || '')));
  }

  // FNV-1a, 32-bit. Small and stable; only used to notice that a model changed.
  function hashString(text) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, '0');
  }

  // Columns (name, type, keys, nullability), the charter and the refs with
  // this table as an endpoint define what was reviewed. Notes, positions and
  // findings do not.
  function modelHash(table, refs = []) {
    const columns = (table?.columns || []).map(c => [c.name, c.type, !!c.pk, !!c.not_null, !!c.unique]);
    const joins = (refs || []).filter(ref => table && (ref.from_table === table.name || ref.to_table === table.name))
      .map(ref => [ref.from_table, ref.from_column, ref.to_table, ref.to_column].join('\u0000')).sort();
    return hashString(JSON.stringify([columns, table?.charter ?? null, joins]));
  }
  const currentHash = (model, name) => modelHash(tableOf(model, name), model.refs);

  function emptyEntry() {
    return { status: null, comment: '', checks: {}, override: '', hash: null, at: null };
  }

  function normalizeEntry(entry) {
    const base = emptyEntry();
    if (!entry || typeof entry !== 'object') return base;
    return {
      status: STATUSES.includes(entry.status) ? entry.status : null,
      comment: typeof entry.comment === 'string' ? entry.comment : '',
      checks: entry.checks && typeof entry.checks === 'object' ? { ...entry.checks } : {},
      override: typeof entry.override === 'string' ? entry.override : '',
      hash: typeof entry.hash === 'string' ? entry.hash : null,
      at: typeof entry.at === 'string' ? entry.at : null,
    };
  }

  // Unreadable or foreign storage content starts a fresh review instead of failing.
  function parseState(text) {
    let raw;
    try { raw = JSON.parse(text || '{}'); } catch (error) { return {}; }
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
    const state = {};
    Object.entries(raw).forEach(([name, entry]) => { state[name] = normalizeEntry(entry); });
    return state;
  }

  function isEmpty(entry) {
    return !entry || (!entry.status && !entry.comment.trim() && !entry.override.trim() && !Object.values(entry.checks).some(Boolean));
  }

  // ── Model context ────────────────────────────────────────────────
  function tableOf(model, name) { return (model.tables || []).find(table => table.name === name) || null; }
  function reviewOf(model, name) { return model.review?.tables?.[name] || { findings: [], replaces: [], replaced_by: [], overlap: [] }; }
  // Path-keyed source changes without a card are listed separately, never reviewed.
  const isUnmapped = (model, name) => !!model.comparison?.tables?.[name]?.unmapped;
  function changeOf(model, name) {
    const change = model.comparison?.tables?.[name];
    return change && !change.unmapped && change.status !== 'unchanged' ? change.status : null;
  }
  function unmappedChanges(model) {
    return Object.entries(model.comparison?.tables || {}).filter(([, change]) => change.unmapped)
      .map(([path, change]) => ({ path, status: change.status || null }));
  }
  // Review machinery (sign-off, walkthrough, export) only appears when the
  // branch comparison found new, modified or removed models.
  function underReview(model) {
    const comparison = model.comparison;
    if (!comparison) return false;
    const counts = comparison.counts;
    if (counts) return (counts.new || 0) + (counts.modified || 0) + (counts.removed || 0) > 0;
    return Object.keys(comparison.tables || {}).some(name => changeOf(model, name));
  }
  function findingsOf(model, name) { return reviewOf(model, name).findings || []; }
  // Removed models have no entry in model.layers; the review entry carries their layer.
  function layerOf(model, name) { return model.layers?.[name] || model.review?.tables?.[name]?.layer || null; }
  function severityCounts(findings) {
    const counts = { error: 0, warning: 0, info: 0 };
    findings.forEach(finding => { if (finding.severity in counts) counts[finding.severity]++; });
    return counts;
  }
  function worstSeverity(findings) {
    return SEVERITIES.find(severity => findings.some(finding => finding.severity === severity)) || null;
  }
  function replacesOf(model, name) {
    const declared = reviewOf(model, name).replaces;
    if (declared && declared.length) return declared;
    const charter = tableOf(model, name)?.charter?.replaces;
    return Array.isArray(charter) ? charter : [];
  }
  // Removed models have no review entry, so derive "replaced by" from every replacement.
  function replacedBy(model, name) {
    const result = new Set(reviewOf(model, name).replaced_by || []);
    Object.keys(model.review?.tables || {}).forEach(other => { if (replacesOf(model, other).includes(name)) result.add(other); });
    return [...result];
  }
  const asList = value => Array.isArray(value) ? value : value === null || value === undefined || value === '' ? [] : [value];
  const firstSentence = text => String(text || '').trim().split(/\n|(?<=[.!?])\s+/)[0].trim();

  // One-line description: charter purpose and grain, else the prose's first
  // sentence and the primary-key columns (grainSource 'pk').
  function synopsis(model, name) {
    const table = tableOf(model, name);
    const charter = table?.charter || {};
    const charterGrain = asList(charter.grain).map(String);
    const pk = (table?.columns || []).filter(column => column.pk).map(column => column.name);
    return {
      purpose: charter.purpose ? String(charter.purpose) : firstSentence(table?.prose ?? table?.note) || null,
      grain: charterGrain.length ? charterGrain : pk,
      grainSource: charterGrain.length ? 'charter' : pk.length ? 'pk' : null,
    };
  }

  // Catalog: every model with a card, in layer order, then lineage order.
  const LAYER_ORDER = ['source', 'seed', 'prep', 'intermediate', 'mart', 'bridge'];
  function catalog(model) {
    const lineageIndex = new Map((model.lineage?.order || []).map((name, i) => [name, i]));
    const rank = name => { const i = LAYER_ORDER.indexOf(layerOf(model, name)); return i < 0 ? LAYER_ORDER.indexOf('intermediate') : i; };
    const names = (model.tables || []).map(table => table.name);
    return names.sort((a, b) => rank(a) - rank(b) || (lineageIndex.get(a) ?? Infinity) - (lineageIndex.get(b) ?? Infinity) || a.localeCompare(b))
      .map(name => {
        const charter = tableOf(model, name).charter || {};
        return {
          name, layer: layerOf(model, name), ...synopsis(model, name),
          owner: charter.owner ? String(charter.owner) : null,
          consumers: asList(charter.consumers).map(String),
          counts: severityCounts(findingsOf(model, name)),
        };
      });
  }

  // Previous/Next order: the walkthrough (else the changed models), then
  // every remaining model in lineage order, then any model lineage missed.
  function walkthroughOf(model) {
    const walkthrough = (model.review?.walkthrough || []).filter(name => !isUnmapped(model, name));
    if (walkthrough.length) return [...walkthrough];
    const lineage = model.lineage?.order || [];
    const changed = Object.keys(model.comparison?.tables || {}).filter(name => changeOf(model, name));
    const at = name => { const i = lineage.indexOf(name); return i < 0 ? Infinity : i; };
    return changed.sort((a, b) => at(a) - at(b));
  }
  function stepOrder(model) {
    const order = walkthroughOf(model);
    const seen = new Set(order);
    [...(model.lineage?.order || []).filter(name => tableOf(model, name)), ...(model.tables || []).map(table => table.name)]
      .forEach(name => { if (!seen.has(name)) { seen.add(name); order.push(name); } });
    return order;
  }
  // Counter text: "2 of 3 in walkthrough" inside the walkthrough, "5 of 14 models" after it.
  function stepLabel(model, name, order = stepOrder(model)) {
    const walk = walkthroughOf(model).length;
    const index = order.indexOf(name);
    if (index < 0) return walk ? walk + ' in walkthrough' : order.length + ' models';
    return index < walk ? `${index + 1} of ${walk} in walkthrough` : `${index + 1} of ${order.length} models`;
  }

  // Summary rows: walkthrough (else models with findings), plus anything already reviewed.
  function summaryNames(model, state = {}) {
    const walkthrough = walkthroughOf(model);
    const names = walkthrough.length ? walkthrough
      : stepOrder(model).filter(name => findingsOf(model, name).length);
    Object.keys(state).forEach(name => {
      if (!names.includes(name) && !isEmpty(state[name]) && (tableOf(model, name) || changeOf(model, name))) names.push(name);
    });
    return names;
  }

  // ── Acknowledgements and gating ──────────────────────────────────
  // Error and warning findings can be acknowledged; the id survives reordering.
  function findingId(finding) {
    return 'finding:' + finding.rule + ':' + hashString(finding.message || '');
  }

  // Validation evidence is expected of new models and marts/bridges, never of
  // removed models or of models with no charter and no required keys.
  function needsEvidence(model, name) {
    const table = tableOf(model, name);
    const change = changeOf(model, name);
    if (!table || change === 'removed') return false;
    const layer = layerOf(model, name);
    const required = reviewOf(model, name).required;
    const hasRequired = Array.isArray(required) ? required.length > 0 : !!layer && layer !== 'source' && layer !== 'seed';
    if (!table.charter && !hasRequired) return false;
    return change === 'new' || PRESENTATION_LAYERS.includes(layer);
  }

  function blockers(model, name) {
    const reasons = [];
    const errors = severityCounts(findingsOf(model, name)).error;
    if (errors) reasons.push(errors === 1 ? '1 error finding' : errors + ' error findings');
    if (needsEvidence(model, name) && !tableOf(model, name)?.charter?.validation?.evidence) reasons.push('no validation evidence');
    return reasons;
  }

  function canApprove(model, name, entry) {
    const reasons = blockers(model, name);
    const overridden = reasons.length > 0 && !!entry?.override?.trim();
    return { ok: reasons.length === 0 || overridden, blockers: reasons, overridden };
  }

  function isStale(entry, model, name) {
    return !!(entry && entry.status && entry.hash && entry.hash !== currentHash(model, name));
  }

  // Returns { entry, blocked }. Setting a status records the model hash, so
  // stale detection compares against the version that was signed off.
  function setStatus(model, name, entry, status, now = new Date().toISOString()) {
    const current = normalizeEntry(entry);
    const next = STATUSES.includes(status) ? status : null;
    if (next === 'approved') {
      const gate = canApprove(model, name, current);
      if (!gate.ok) return { entry: current, blocked: gate.blockers };
    }
    return { entry: { ...current, status: next, hash: next ? currentHash(model, name) : null, at: now }, blocked: null };
  }

  // Merges a patch (comment, override, checks). With model and name, an
  // approval that loses its override while blockers remain is withdrawn:
  // the status clears and the comment and checks stay.
  function update(entry, patch, now = new Date().toISOString(), model = null, name = null) {
    const current = normalizeEntry(entry);
    const next = { ...current, ...patch, at: now };
    if (patch.checks) next.checks = { ...current.checks, ...patch.checks };
    const result = normalizeEntry(next);
    if (model && name && result.status === 'approved' && !canApprove(model, name, result).ok) {
      result.status = null;
      result.hash = null;
    }
    return result;
  }

  const acknowledgeable = finding => finding.severity === 'error' || finding.severity === 'warning';
  function openFindings(model, name, entry) {
    const checks = entry?.checks || {};
    return findingsOf(model, name).filter(finding => acknowledgeable(finding) && !checks[findingId(finding)]);
  }
  function acknowledgedFindings(model, name, entry) {
    const checks = entry?.checks || {};
    return findingsOf(model, name).filter(finding => acknowledgeable(finding) && checks[findingId(finding)]);
  }

  // ── Summaries and export ─────────────────────────────────────────
  function rows(model, state = {}) {
    return summaryNames(model, state).map(name => {
      const entry = normalizeEntry(state[name]);
      const gate = canApprove(model, name, entry);
      return {
        name,
        layer: layerOf(model, name),
        change: changeOf(model, name),
        counts: severityCounts(findingsOf(model, name)),
        status: entry.status,
        stale: isStale(entry, model, name),
        // Approved only because of an override reason; blockers lists what it overrode.
        overridden: entry.status === 'approved' && gate.overridden,
        // Approved while blocked and without an override (older state, or a model that changed).
        invalid: entry.status === 'approved' && !gate.ok,
        blockers: gate.blockers,
        replaces: replacesOf(model, name),
        replacedBy: replacedBy(model, name),
        entry,
      };
    });
  }
  const statusLabel = row => row.invalid ? 'Approved (invalid: blocked without override)' : STATUS_LABELS[row.status];

  function statusCounts(list) {
    const counts = { approved: 0, changes: 0, question: 0, unreviewed: 0, stale: 0 };
    list.forEach(row => { counts[row.status || 'unreviewed']++; if (row.stale) counts.stale++; });
    return counts;
  }

  function baselineText(model) {
    const comparison = model.comparison;
    if (!comparison) return 'no branch comparison';
    return comparison.revision + (comparison.base ? ' (merge base ' + comparison.base + ')' : '');
  }

  const inline = text => String(text ?? '').replace(/\s*\n\s*/g, ' ').trim();
  const code = name => '`' + String(name).replace(/`/g, "'") + '`';
  // Local calendar date, ISO formatted.
  function localDate(now) {
    const date = now instanceof Date ? now : new Date(now);
    return [date.getFullYear(), date.getMonth() + 1, date.getDate()].map((n, i) => String(n).padStart(i ? 2 : 4, '0')).join('-');
  }

  // Per model: status, purpose and grain, affected consumers (modified
  // models), comment, override, then findings. Unticked errors are
  // "Blocking"; ticked ones stay listed as "Acknowledged (still open)".
  // Needs-changes models list every error and warning, ticked or not.
  function modelLines(model, row) {
    const lines = [];
    const meta = [row.change, row.layer].filter(Boolean).join(' ');
    let head = '- ' + code(row.name) + (meta ? ' (' + meta + ')' : '') + ': **' + statusLabel(row) + '**';
    if (row.stale) head += ' (stale: model changed since review)';
    lines.push(head);
    const charter = tableOf(model, row.name)?.charter;
    if (charter) {
      const about = [charter.purpose ? 'Purpose: ' + inline(charter.purpose) : '',
        asList(charter.grain).length ? 'Grain: ' + asList(charter.grain).map(inline).join(', ') : ''].filter(Boolean);
      if (about.length) lines.push('  - ' + about.join(' · '));
    }
    const consumers = model.lineage?.tables?.[row.name]?.consumers_affected || [];
    if (row.change === 'modified' && consumers.length) lines.push('  - Affected consumers: ' + consumers.map(inline).join(', '));
    if (row.entry.comment.trim()) lines.push('  - Comment: ' + inline(row.entry.comment));
    if (row.overridden) lines.push('  - Override: ' + inline(row.entry.override) + ' (' + row.blockers.join('; ') + ')');
    const findingLine = finding => '    - ' + finding.severity + ': ' + inline(finding.message) + (finding.inferred ? ' (inferred)' : '');
    const group = (label, list) => { if (list.length) lines.push('  - ' + label + ':', ...list.map(findingLine)); };
    const all = findingsOf(model, row.name).filter(acknowledgeable);
    const open = row.status === 'changes' ? all : openFindings(model, row.name, row.entry);
    group('Blocking', open.filter(finding => finding.severity === 'error'));
    group('Warnings', open.filter(finding => finding.severity === 'warning'));
    if (row.status !== 'changes') group('Acknowledged (still open)', acknowledgedFindings(model, row.name, row.entry));
    const checks = MANUAL_CHECKS.filter(item => row.entry.checks[item.id]).map(item => item.label.toLowerCase());
    if (checks.length) lines.push('  - Checks: ' + checks.join(', '));
    return lines;
  }

  function markdown(model, state = {}, now = new Date()) {
    const list = rows(model, state);
    const counts = statusCounts(list);
    const project = model.project?.name || 'DBML diagram';
    const lines = ['## Model review: ' + project + ' · ' + localDate(now), '', 'Baseline: ' + baselineText(model), ''];
    const tally = [counts.approved + ' approved', counts.changes + ' needs changes', counts.question + ' open question', counts.unreviewed + ' not reviewed'];
    if (counts.stale) tally.push(counts.stale + ' stale');
    lines.push('**' + list.length + ' model' + (list.length === 1 ? '' : 's') + ':** ' + tally.join(' · '), '');
    list.forEach(row => lines.push(...modelLines(model, row)));
    const commentSection = (title, status) => {
      const commented = list.filter(row => row.status === status && row.entry.comment.trim());
      if (!commented.length) return;
      lines.push('', '### ' + title, '');
      commented.forEach(row => lines.push('- ' + code(row.name) + ': ' + inline(row.entry.comment)));
    };
    commentSection('Unresolved questions', 'question');
    const replaced = [];
    list.forEach(row => row.replaces.forEach(target => replaced.push([target, row.name])));
    if (replaced.length) {
      lines.push('', '### Replaced models', '');
      replaced.forEach(([target, by]) => {
        const status = changeOf(model, target);
        lines.push('- ' + code(target) + ' replaced by ' + code(by) + (status ? ' (' + status + ' in this branch)' : ''));
      });
    }
    commentSection('Action items', 'changes');
    return lines.join('\n') + '\n';
  }

  function exportJson(model, state = {}, now = new Date().toISOString()) {
    const models = {};
    rows(model, state).forEach(row => {
      models[row.name] = {
        layer: row.layer, change: row.change, findings: row.counts,
        ...row.entry, stale: row.stale, invalid: row.invalid, current_hash: currentHash(model, row.name),
      };
    });
    return {
      project: model.project?.name || null,
      baseline: model.comparison ? { revision: model.comparison.revision, base: model.comparison.base } : null,
      exported_at: now,
      models,
    };
  }

  return {
    STATUSES, STATUS_LABELS, MANUAL_CHECKS, CHANGE_LABELS,
    storageKey, hashString, modelHash, emptyEntry, normalizeEntry, parseState, isEmpty,
    severityCounts, worstSeverity, layerOf, replacedBy, replacesOf, underReview, unmappedChanges,
    synopsis, catalog, walkthroughOf, stepOrder, stepLabel, summaryNames,
    findingId, needsEvidence, blockers, canApprove, isStale, setStatus, update, openFindings, acknowledgedFindings,
    rows, statusLabel, statusCounts, markdown, exportJson,
  };
})();
if (typeof module !== 'undefined') module.exports = DBMLReview;
