// ── Model review panel, sign-off and summary ─────────────────────
// Included inside the viewer IIFE after lineage.js. State logic lives in
// review-state.js (DBMLReview); this file only renders and wires the DOM.
// openReview is a hoisted function declaration so lineage.js can call it.
// Keys while the panel is open: ArrowLeft/ArrowRight step Previous/Next
// (not inside a text field); Escape closes it, or only leaves a text field.
//
// Sign-off (status buttons, Acknowledge boxes, the Sign-off section, the
// Summary tab and its exports) appears only while something is under review.
// Otherwise the panel describes the model and the third tab is a Catalog.
const reviewPanel = document.getElementById('change-detail');
const summaryView = document.getElementById('view-summary');
const underReview = DBMLReview.underReview(MODEL);
const reviewSteps = DBMLReview.stepOrder(MODEL);
const reviewKey = DBMLReview.storageKey(MODEL.project?.name, location.pathname);
const reviewInfo = name => MODEL.review?.tables?.[name] || { findings: [], missing: [], overlap: [], replaces: [], replaced_by: [] };
const changeInfo = name => comparison?.tables?.[name]?.unmapped ? null : comparison?.tables?.[name] || null;
const knownModel = name => !!(tableByName[name] || changeInfo(name) || MODEL.review?.tables?.[name]);
let reviewState = loadReviewState();
let catalogOpen = false;   // the Summary's collapsed catalog survives re-renders

function loadReviewState() {
  try { return DBMLReview.parseState(localStorage.getItem(reviewKey)); } catch (error) { return {}; }
}
function reviewEntry(name) { return DBMLReview.normalizeEntry(reviewState[name]); }
function putReviewEntry(name, entry) {
  if (DBMLReview.isEmpty(entry)) delete reviewState[name]; else reviewState[name] = entry;
  // Storage can be unavailable (private windows, file:// policies); keep working in memory.
  try { localStorage.setItem(reviewKey, JSON.stringify(reviewState)); } catch (error) { /* in-memory only */ }
  if (activeView === 'summary') renderSummary();
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
function countsText(counts) {
  return [counts.error ? plural(counts.error, 'error') : '', counts.warning ? plural(counts.warning, 'warning') : '', counts.info ? counts.info + ' info' : '']
    .filter(Boolean).join(', ');
}

// Card header indicator, coloured by the worst finding (info counts as none).
function reviewDot(name) {
  const findings = reviewInfo(name).findings || [];
  const severity = DBMLReview.worstSeverity(findings);
  const tone = severity === 'error' || severity === 'warning' ? severity : 'none';
  const title = 'Review model · ' + (countsText(DBMLReview.severityCounts(findings)) || 'no findings');
  return `<button class="review-dot ${tone}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title.replace('model', 'model ' + name))}"></button>`;
}

// ── Panel building blocks ────────────────────────────────────────
const isUrl = value => /^https?:\/\/\S+$/i.test(String(value || '').trim());
const asList = value => Array.isArray(value) ? value : value === null || value === undefined || value === '' ? [] : [value];
function modelLink(name) {
  return knownModel(name) && name !== reviewFocus
    ? `<button class="rp-link" data-open="${escapeHtml(name)}">${escapeHtml(name)}</button>`
    : `<code class="rp-code">${escapeHtml(name)}</code>`;
}
const modelLinks = list => asList(list).map(modelLink).join(' ');
const chipList = (list, cls = '') => asList(list).map(item => `<span class="rp-chip ${cls}">${escapeHtml(typeof item === 'object' ? JSON.stringify(item) : item)}</span>`).join(' ');
const bullets = list => `<ul class="rp-list">${asList(list).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
const field = (label, html) => html ? `<div class="rp-field"><span class="rp-label">${label}</span><div>${html}</div></div>` : '';
const section = (title, html, extra = '') => html ? `<section class="rp-section"${extra}><h3>${title}</h3>${html}</section>` : '';
const muted = text => `<span class="rp-muted">${text}</span>`;
function grainChips(table, grain) {
  const columns = new Set((table?.columns || []).map(column => column.name));
  return asList(grain).map(column => columns.has(column) || !table
    ? `<span class="rp-chip col">${escapeHtml(column)}</span>`
    : `<span class="rp-chip col unknown" title="Not a column of this table">${escapeHtml(column)}</span>`).join(' ');
}

// ── Panel sections, top to bottom ────────────────────────────────
function navHtml(name) {
  return `<div class="rp-top">
      <div class="change-heading"><span>${underReview ? 'Model review' : 'Model'}</span><button data-action="close" aria-label="Close model panel">&times;</button></div>
      <div class="change-nav"><button class="branch-btn" data-action="prev" aria-label="Previous model"${reviewSteps.length ? '' : ' disabled'}>← Previous</button><span title="${underReview ? 'Walkthrough first, then every other model in lineage order' : 'Every model in lineage order'}">${DBMLReview.stepLabel(MODEL, name, reviewSteps)}</span><button class="branch-btn" data-action="next" aria-label="Next model"${reviewSteps.length ? '' : ' disabled'}>Next →</button></div>
    </div>`;
}

function titleHtml(name) {
  const change = changeInfo(name);
  const layer = DBMLReview.layerOf(MODEL, name);
  return `<h2>${escapeHtml(name)}</h2>
    <div class="rp-meta">${layer ? `<span class="rp-layer">${escapeHtml(layer)}</span>` : ''}${DBMLReview.CHANGE_LABELS[change?.status] ? `<span class="status-${change.status}">${DBMLReview.CHANGE_LABELS[change.status]} model</span>` : ''}${!tableByName[name] ? muted('No card in the diagram') : ''}</div>`;
}

function gateText(name, entry, { refused = false, withdrawn = false } = {}) {
  const gate = DBMLReview.canApprove(MODEL, name, entry);
  if (!gate.blockers.length) return '';
  const reasons = escapeHtml(gate.blockers.join('; '));
  if (gate.overridden) return `<div class="rp-gate ok">Approval allowed by override despite: ${reasons}.</div>`;
  if (withdrawn) return `<div class="rp-gate refused" role="alert">Approval withdrawn: the override reason was cleared while blocked by ${reasons}.</div>`;
  if (entry.status === 'approved') return `<div class="rp-gate refused" role="alert">Approved without an override reason despite: ${reasons}. Record a reason under Sign-off or change the status.</div>`;
  return refused
    ? `<div class="rp-gate refused" role="alert">Can't approve: ${reasons}. Record an override reason under Sign-off to approve anyway.</div>`
    : `<div class="rp-gate" role="note">Approval needs an override reason: ${reasons}.</div>`;
}

// Status buttons, stale badge and gate note, re-rendered on every sign-off edit.
function statusHtml(name, flags = {}) {
  const entry = reviewEntry(name);
  const stale = DBMLReview.isStale(entry, MODEL, name);
  const gate = DBMLReview.canApprove(MODEL, name, entry);
  const statusButtons = DBMLReview.STATUSES.map(status => {
    const pressed = entry.status === status;
    const title = pressed ? (stale ? 'Confirm this status for the current model' : 'Click again to clear') : status === 'approved' && !gate.ok ? 'Needs an override reason' : '';
    return `<button class="rp-status ${status}" data-status="${status}" aria-pressed="${pressed}"${title ? ` title="${title}"` : ''}>${DBMLReview.STATUS_LABELS[status]}</button>`;
  }).join('');
  return `<div class="rp-statuses">${statusButtons}</div>
    ${stale ? '<div class="rp-stale" title="Columns, charter or refs changed after this status was set. Click the status again to confirm it.">Stale: model changed since review</div>' : ''}
    ${gateText(name, entry, flags)}`;
}
const statusBlockHtml = name => underReview ? `<div class="rp-status-block" id="rp-status">${statusHtml(name)}</div>` : '';

// "Purpose … · Grain …": the charter, else the prose's first sentence and the primary key.
function synopsisHtml(name) {
  const table = tableByName[name];
  const about = DBMLReview.synopsis(MODEL, name);
  const purpose = about.purpose || (table ? '' : String(changeInfo(name)?.baseline?.prose || '').split(/\n|(?<=[.!?])\s+/)[0]);
  const parts = [];
  if (purpose) parts.push(`<span class="rp-label">Purpose</span> ${escapeHtml(purpose)}`);
  if (about.grain.length) parts.push(`<span class="rp-label">Grain</span> ${grainChips(table, about.grain)}${about.grainSource === 'pk' ? ' <span class="rp-tag" title="No declared grain; primary key columns">PK</span>' : ''}`);
  return parts.length ? `<p class="rp-synopsis">${parts.join(' <span class="rp-sep">·</span> ')}</p>` : '';
}

// Error and warning findings carry an Acknowledge checkbox while under review.
function findingsSection(name) {
  const findings = reviewInfo(name).findings || [];
  const checks = reviewEntry(name).checks;
  return section('Findings', findings.length ? `<ul class="rp-findings">${findings.map(finding => {
    const id = DBMLReview.findingId(finding);
    const ack = underReview && (finding.severity === 'error' || finding.severity === 'warning');
    return `<li${ack && checks[id] ? ' class="acknowledged"' : ''}><span class="rp-sev ${finding.severity}">${finding.severity}</span> <span class="rp-finding">${escapeHtml(finding.message)}</span>${finding.inferred ? ' <span class="rp-tag" title="Derived from SQL or naming, not declared">inferred</span>' : ''}
      ${ack ? `<label class="rp-check rp-ack" title="Seen and discussed; the finding stays open in the export"><input type="checkbox" data-check="${escapeHtml(id)}"${checks[id] ? ' checked' : ''}> Acknowledge</label>` : ''}</li>`;
  }).join('')}</ul>` : '');
}

function diffLines(text) {
  return String(text).replace(/\n$/, '').split('\n').map(line => {
    const kind = /^(\+\+\+|---)( |$)/.test(line) ? 'meta' : line.startsWith('@@') ? 'hunk' : line[0] === '+' ? 'add' : line[0] === '-' ? 'del' : '';
    return kind ? `<span class="${kind}">${escapeHtml(line)}</span>` : escapeHtml(line);
  }).join('\n');
}
function diffHtml(diffs) {
  const files = asList(diffs).filter(item => item && item.diff);
  if (!files.length) return '';
  return `<details class="rp-diff"><summary>Source diff (${plural(files.length, 'file')})</summary>${files.map(item =>
    `<div class="rp-diff-path">${escapeHtml(item.path)}</div><pre>${diffLines(item.diff)}</pre>`).join('')}</details>`;
}
function changesSection(name) {
  const change = changeInfo(name);
  return section('Changes', (change?.details?.length ? bullets(change.details) : '') + diffHtml(change?.diffs));
}

// Removed models: what the comparison baseline had, when Python provides it.
function baselineSection(name) {
  const baseline = changeInfo(name)?.baseline;
  if (!baseline || tableByName[name]) return '';
  const columns = asList(baseline.columns).map(column => `<li><code class="rp-code">${escapeHtml(column.name)}</code> ${muted(escapeHtml(column.type || ''))}${column.pk ? ' <span class="rp-pk">PK</span>' : ''}</li>`).join('');
  return section(`In ${escapeHtml(comparison?.revision || 'the baseline')}`,
    (baseline.prose ? `<p class="rp-prose">${escapeHtml(baseline.prose)}</p>` : '')
    + field('Group', baseline.group && baseline.group !== '_default' ? escapeHtml(baseline.group) : '')
    + field(`Columns (${asList(baseline.columns).length})`, columns ? `<ul class="rp-columns${asList(baseline.columns).length > 8 ? ' wide' : ''}">${columns}</ul>` : muted('none recorded')));
}

function charterFields(table) {
  const charter = table.charter || {};
  const parts = [];
  parts.push(field('Purpose', charter.purpose ? escapeHtml(charter.purpose) : ''));
  parts.push(field('Grain', grainChips(table, charter.grain)));
  parts.push(field('Owner', charter.owner ? escapeHtml(charter.owner) : ''));
  parts.push(field('Consumers', asList(charter.consumers).length ? bullets(charter.consumers) : ''));
  parts.push(field('Questions', asList(charter.questions).length ? bullets(charter.questions) : ''));
  parts.push(field('Metrics', asList(charter.metrics).map(metric => {
    const item = typeof metric === 'object' && metric ? metric : { name: metric };
    return `<div class="rp-metric"><strong>${escapeHtml(item.name)}</strong>${item.expression ? ` <code class="rp-code">${escapeHtml(item.expression)}</code>` : ''}${item.description ? `<div class="rp-muted">${escapeHtml(item.description)}</div>` : ''}</div>`;
  }).join('')));
  parts.push(field('History', charter.history ? escapeHtml(charter.history) : ''));
  const mat = charter.materialization;
  if (mat) {
    const bits = [escapeHtml(typeof mat === 'object' ? mat.type || '' : mat)];
    if (asList(mat.unique_key).length) bits.push('unique_key ' + chipList(mat.unique_key, 'col'));
    if (mat.strategy) bits.push('strategy ' + escapeHtml(mat.strategy));
    parts.push(field('Materialization', bits.filter(Boolean).join(' · ')));
  }
  parts.push(field('Freshness', charter.freshness ? escapeHtml(charter.freshness) : ''));
  if ('pii' in charter) parts.push(field('PII', asList(charter.pii).length ? chipList(charter.pii, 'col') : muted('none declared')));
  const validation = charter.validation && typeof charter.validation === 'object' ? charter.validation : null;
  if (validation) {
    const evidence = validation.evidence;
    parts.push(field('Validation', [
      validation.reconciles_to ? 'Reconciles to ' + modelLinks(validation.reconciles_to) : '',
      evidence ? 'Evidence: ' + (isUrl(evidence) ? `<a href="${escapeHtml(String(evidence).trim())}" target="_blank" rel="noopener">${escapeHtml(evidence)}</a>` : escapeHtml(evidence)) : '<span class="rp-warn">No evidence recorded</span>',
      asList(validation.tests).length ? 'Tests: ' + chipList(validation.tests) : '',
    ].filter(Boolean).map(line => `<div>${line}</div>`).join('')));
  }
  if (Array.isArray(charter.replaces) && !charter.replaces.length) parts.push(field('Replaces', muted('nothing (declared)')));
  return parts.join('');
}

// The full charter, with the description and linked models. "Missing" keys
// show while under review, otherwise only for new models or declared charters.
function charterSection(name) {
  const table = tableByName[name];
  const replaces = DBMLReview.replacesOf(MODEL, name), replacedBy = DBMLReview.replacedBy(MODEL, name);
  const links = field('Replaces', replaces.length ? modelLinks(replaces) : '') + field('Replaced by', replacedBy.length ? modelLinks(replacedBy) : '')
    + field('Depends on', modelLinks(table?.charter?.depends_on));
  if (!table) return section('Charter', links);
  const missing = asList(reviewInfo(name).missing);
  const showMissing = missing.length && (underReview || changeInfo(name)?.status === 'new' || !!table.charter);
  return section('Charter', [
    table.prose ? `<p class="rp-prose">${escapeHtml(table.prose)}</p>` : '',
    table.charter_error ? `<p class="rp-error">${escapeHtml(table.charter_error)}</p>` : !table.charter ? '<p class="rp-muted">No charter block in this table\'s note</p>' : '',
    charterFields(table), links,
    showMissing ? field('Missing', `<span class="rp-warn">${missing.map(escapeHtml).join(', ')}</span>`) : '',
  ].join(''));
}

// Models joined to this one by a DBML ref, either direction.
function joinedWith(name) {
  const joined = new Set();
  MODEL.refs.forEach(ref => {
    if (ref.from_table === name && ref.to_table !== name) joined.add(ref.to_table);
    if (ref.to_table === name && ref.from_table !== name) joined.add(ref.from_table);
  });
  return [...joined];
}
function replacementsInDiagram(name) {
  return DBMLReview.replacedBy(MODEL, name).filter(other => tableByName[other]);
}
function lineageSection(name) {
  const lineage = MODEL.lineage?.tables?.[name];
  const parts = [];
  if (lineage) {
    parts.push(field(`Upstream (${lineage.upstream.length})`, modelLinks(lineage.upstream) || muted('none')));
    parts.push(field(`Downstream (${lineage.downstream.length})`, modelLinks(lineage.downstream) || muted('none')));
    if (changeInfo(name)?.status === 'modified') {
      parts.push(field(`Impact (${lineage.impact.length})`, modelLinks(lineage.impact) || muted('no downstream models')));
      if (lineage.consumers_affected.length) parts.push(field('Consumers affected', bullets(lineage.consumers_affected)));
    }
  }
  const joined = joinedWith(name);
  if (joined.length) parts.push(field(`Joined with (${joined.length})`, modelLinks(joined)));
  const buttons = [
    lineage || tableByName[name] ? '<button class="branch-btn" data-action="lineage">Show in lineage</button>' : '',
    tableByName[name] ? '<button class="branch-btn" data-action="diagram">Show in diagram</button>'
      : replacementsInDiagram(name).length ? '<button class="branch-btn" data-action="diagram" title="Fit the diagram to the models that replace this one">Show replacements in diagram</button>' : '',
  ].join('');
  if (buttons) parts.push(`<div class="rp-buttons">${buttons}</div>`);
  return section('Lineage', parts.join(''));
}

function overlapSection(name) {
  const overlap = reviewInfo(name).overlap || [];
  return section('Extend instead of create?', overlap.length ? '<p class="rp-muted">Existing marts this model could extend:</p>' + overlap.map(item =>
    `<div class="rp-overlap"><div>${modelLink(item.model)} ${muted('score ' + escapeHtml(item.score))}</div>${bullets(item.reasons)}</div>`).join('') : '');
}

// Comment, manual checks and override reason, at the bottom of the panel.
function signoffHtml(name) {
  const entry = reviewEntry(name);
  const gate = DBMLReview.canApprove(MODEL, name, entry);
  const checks = DBMLReview.MANUAL_CHECKS.map(item => `<label class="rp-check"><input type="checkbox" data-check="${escapeHtml(item.id)}"${entry.checks[item.id] ? ' checked' : ''}> <span>${escapeHtml(item.label)}</span></label>`).join('');
  return `<h3>Sign-off</h3>
    <textarea class="rp-input" data-field="comment" rows="2" placeholder="Comment or open question">${escapeHtml(entry.comment)}</textarea>
    <div class="rp-checks">${checks}</div>
    ${gate.blockers.length || entry.override ? `<input class="rp-input" data-field="override" placeholder="Override reason (included in the export)" value="${escapeHtml(entry.override)}"><div id="rp-override-note" aria-live="polite"></div>` : ''}
    ${entry.at ? `<div class="rp-muted rp-at">Updated ${escapeHtml(new Date(entry.at).toLocaleString())}</div>` : ''}`;
}
const signoffSection = name => underReview ? `<section class="rp-section rp-signoff" id="rp-signoff">${signoffHtml(name)}</section>` : '';

function renderReviewPanel() {
  const name = reviewFocus;
  reviewPanel.setAttribute('aria-label', underReview ? 'Model review' : 'Model details');
  reviewPanel.innerHTML = [
    navHtml(name), titleHtml(name), statusBlockHtml(name), synopsisHtml(name),
    findingsSection(name), changesSection(name), baselineSection(name), charterSection(name),
    lineageSection(name), overlapSection(name), signoffSection(name),
  ].join('');
}

function positionReviewPanel() {
  const wide = window.innerWidth > 900;
  const top = document.querySelector('.project-header').getBoundingClientRect().bottom + 12;
  reviewPanel.style.top = wide ? top + 'px' : '';
  reviewPanel.style.maxHeight = wide ? `min(calc(100vh - 240px), calc(100vh - ${top + 16}px))` : '';
}

function openReview(name) {
  if (!name) return;
  reviewFocus = name;
  renderReviewPanel();
  reviewPanel.hidden = false;
  positionReviewPanel();
  reviewPanel.scrollTop = 0;
  if (activeView === 'diagram') focusModel(name); else applyBranchOverlay();
  if (activeView === 'lineage') lineageView.follow(name);
  if (activeView === 'summary') renderSummary();
}

function stepReview(delta) {
  if (!reviewSteps.length) return;
  const index = reviewSteps.indexOf(reviewFocus);
  const next = index < 0 ? (delta > 0 ? 0 : reviewSteps.length - 1) : (index + delta + reviewSteps.length) % reviewSteps.length;
  openReview(reviewSteps[next]);
}

function refreshSignoff(name, flags = {}, focusSelector = null) {
  const status = document.getElementById('rp-status'), signoff = document.getElementById('rp-signoff');
  if (!status || !signoff) return;
  status.innerHTML = statusHtml(name, flags);
  signoff.innerHTML = signoffHtml(name);
  if (focusSelector) reviewPanel.querySelector(focusSelector)?.focus();
}

reviewPanel.addEventListener('click', event => {
  const open = event.target.closest('[data-open]');
  if (open) { openReview(open.dataset.open); return; }
  const status = event.target.closest('[data-status]');
  const name = reviewFocus;
  if (status && name) {
    const entry = reviewEntry(name);
    const wanted = status.dataset.status;
    const clear = entry.status === wanted && !DBMLReview.isStale(entry, MODEL, name);
    const result = DBMLReview.setStatus(MODEL, name, entry, clear ? null : wanted);
    if (!result.blocked) putReviewEntry(name, result.entry);
    refreshSignoff(name, { refused: !!result.blocked }, result.blocked ? 'input[data-field="override"]' : `[data-status="${wanted}"]`);
    return;
  }
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (action === 'close') closeChanges();
  else if (action === 'prev') stepReview(-1);
  else if (action === 'next') stepReview(1);
  else if (action === 'diagram') {
    switchView('diagram');
    if (tableByName[name]) focusModel(name);
    else {
      // Fit to the replacements and their joined tables, as focus does for a model.
      const context = new Set(replacementsInDiagram(name));
      MODEL.refs.forEach(ref => { if (context.has(ref.from_table) || context.has(ref.to_table)) context.add(ref.from_table).add(ref.to_table); });
      fitToScreen(context);
    }
  }
  else if (action === 'lineage') lineageView.show(name);
});
// Edits go through DBMLReview.update with the model, so clearing the
// override of a blocked approval withdraws it. Only the status block is
// re-rendered, which keeps focus in the field being typed in.
function editEntry(name, patch) {
  const before = reviewEntry(name);
  const entry = DBMLReview.update(before, patch, undefined, MODEL, name);
  putReviewEntry(name, entry);
  const withdrawn = before.status === 'approved' && !entry.status;
  const status = document.getElementById('rp-status'), note = document.getElementById('rp-override-note');
  if (status) status.innerHTML = statusHtml(name, { withdrawn });
  // The status buttons may be scrolled out of view; say it next to the field too.
  if (note && (withdrawn || entry.status)) note.innerHTML = withdrawn ? '<div class="rp-gate refused">Approval withdrawn: approving this model needs an override reason.</div>' : '';
}
reviewPanel.addEventListener('input', event => {
  const fieldName = event.target.dataset.field, name = reviewFocus;
  if (fieldName && name) editEntry(name, { [fieldName]: event.target.value });
});
reviewPanel.addEventListener('change', event => {
  const id = event.target.dataset.check, name = reviewFocus;
  if (!id || !name) return;
  editEntry(name, { checks: { [id]: event.target.checked } });
  event.target.closest('.rp-findings li')?.classList.toggle('acknowledged', event.target.checked);
});
window.addEventListener('keydown', event => {
  if (reviewPanel.hidden || isTextField(event.target) || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
  event.preventDefault();
  stepReview(event.key === 'ArrowLeft' ? -1 : 1);
});
canvas.addEventListener('click', event => {
  const trigger = event.target.closest('.change-badge, .review-dot');
  if (trigger) openReview(trigger.closest('.table-node').dataset.table);
});
const reviewButton = document.getElementById('branch-review');
if (reviewButton) {
  reviewButton.disabled = !reviewSteps.length;
  reviewButton.addEventListener('click', () => openReview(reviewSteps[0]));
}
window.addEventListener('resize', () => { if (!reviewPanel.hidden) positionReviewPanel(); });

// ── Summary and Catalog views ────────────────────────────────────
// Under review the tab is the Summary: walkthrough rows with sign-off,
// unmatched source paths, exports, and a collapsed catalog. Otherwise it is
// the Catalog of every model.
function catalogTable(rows) {
  // Columns may wrap between each other; the PK tag stays with the last one.
  const pkTag = ' <span class="rp-tag" title="No declared grain; primary key columns">PK</span>';
  const grain = row => row.grain.map((column, i) => `<span class="rs-nowrap"><code class="rp-code">${escapeHtml(column)}</code>${
    i < row.grain.length - 1 ? ',' : row.grainSource === 'pk' ? pkTag : ''}</span>`).join(' ');
  return `<table class="rs-table rs-catalog">
    <thead><tr><th>Model</th><th>Layer</th><th>Purpose</th><th>Grain</th><th>Owner</th><th>Consumers</th><th title="Errors">E</th><th title="Warnings">W</th><th title="Info">I</th></tr></thead>
    <tbody>${rows.map(row => `<tr data-open="${escapeHtml(row.name)}" tabindex="0" title="Open model"${row.name === reviewFocus ? ' class="current"' : ''}>
      <td class="rs-name">${escapeHtml(row.name)}</td>
      <td>${escapeHtml(row.layer || '')}</td>
      <td class="rs-text">${row.purpose ? escapeHtml(row.purpose) : '<span class="rp-muted">—</span>'}</td>
      <td>${grain(row)}</td>
      <td>${escapeHtml(row.owner || '')}</td>
      <td class="rs-text">${escapeHtml(row.consumers.join(', '))}</td>
      ${severityCells(row.counts)}
    </tr>`).join('')}</tbody></table>`;
}
const severityCells = counts => `<td class="rs-num${counts.error ? ' error' : ''}">${counts.error}</td><td class="rs-num${counts.warning ? ' warning' : ''}">${counts.warning}</td><td class="rs-num">${counts.info}</td>`;

function catalogHtml() {
  const rows = DBMLReview.catalog(MODEL);
  const baseline = MODEL.comparison_error ? `<span class="rp-warn">Branch comparison unavailable: ${escapeHtml(MODEL.comparison_error)}</span>`
    : comparison ? `No changes since <strong>${escapeHtml(comparison.revision)}</strong> · merge base ${escapeHtml(comparison.base)}` : 'No branch comparison';
  return `<div class="rs-wrap">
    <h1>Model catalog</h1>
    <p class="rs-sub">${plural(rows.length, 'model')} in layer order · ${baseline} · click a row for details</p>
    ${rows.length ? catalogTable(rows) : '<p class="rp-muted">No tables in this DBML file.</p>'}
  </div>`;
}

// Changed files with no unique card: path-keyed comparison entries and the
// comparison's unmapped list (strings or { path }).
function unmatchedHtml() {
  const items = DBMLReview.unmappedChanges(MODEL);
  asList(comparison?.unmapped).forEach(item => {
    const path = typeof item === 'string' ? item : item?.path;
    if (path && !items.some(other => other.path === path)) items.push({ path, status: typeof item === 'object' ? item.status || null : null });
  });
  if (!items.length) return '';
  return `<div class="rs-unmatched"><h2>Unmatched source changes</h2>
    <p class="rs-sub">Changed model files with no unique diagram card. They are not in the walkthrough.</p>
    <ul>${items.map(item => `<li><code class="rp-code">${escapeHtml(item.path)}</code>${item.status ? ` <span class="status-${escapeHtml(item.status)}">${DBMLReview.CHANGE_LABELS[item.status] || escapeHtml(item.status)}</span>` : ''}</li>`).join('')}</ul></div>`;
}

function summaryHtml() {
  const rows = DBMLReview.rows(MODEL, reviewState);
  const counts = DBMLReview.statusCounts(rows);
  const severity = rows.reduce((sum, row) => ({ error: sum.error + row.counts.error, warning: sum.warning + row.counts.warning, info: sum.info + row.counts.info }), { error: 0, warning: 0, info: 0 });
  const chip = (label, value, cls) => `<span class="rs-count ${cls}"><strong>${value}</strong> ${label}</span>`;
  const catalog = DBMLReview.catalog(MODEL);
  return `<div class="rs-wrap">
    <h1>Review summary</h1>
    <p class="rs-sub">Changes since <strong>${escapeHtml(comparison.revision)}</strong> · merge base ${escapeHtml(comparison.base)} · walkthrough order · stored in this browser only</p>
    <div class="rs-counts">
      ${chip('approved', counts.approved, 'approved')}${chip('needs changes', counts.changes, 'changes')}${chip('open question', counts.question, 'question')}${chip('not reviewed', counts.unreviewed, 'none')}${counts.stale ? chip('stale', counts.stale, 'stale') : ''}
      <span class="rs-sep"></span>
      ${chip(severity.error === 1 ? 'error' : 'errors', severity.error, 'error')}${chip(severity.warning === 1 ? 'warning' : 'warnings', severity.warning, 'warning')}${chip('info', severity.info, 'info')}
    </div>
    <div class="rs-actions">
      <button class="branch-btn" data-summary="markdown">Copy as Markdown</button>
      <button class="branch-btn" data-summary="json">Download JSON</button>
      <button class="branch-btn" data-summary="clear">Clear review state</button>
      <span class="rs-msg" id="rs-msg" aria-live="polite"></span>
    </div>
    <table class="rs-table">
      <thead><tr><th>Model</th><th>Layer</th><th>Change</th><th title="Errors">E</th><th title="Warnings">W</th><th title="Info">I</th><th>Sign-off</th><th>Replaces</th></tr></thead>
      <tbody>${rows.map(row => `<tr data-open="${escapeHtml(row.name)}" tabindex="0" title="Open model review"${row.name === reviewFocus ? ' class="current"' : ''}>
        <td class="rs-name">${escapeHtml(row.name)}</td>
        <td>${escapeHtml(row.layer || '')}</td>
        <td>${row.change ? `<span class="status-${row.change}">${DBMLReview.CHANGE_LABELS[row.change] || escapeHtml(row.change)}</span>` : ''}</td>
        ${severityCells(row.counts)}
        <td><span class="rs-status ${row.invalid ? 'invalid' : row.status || 'none'}">${escapeHtml(DBMLReview.statusLabel(row))}</span>${row.overridden ? ` <span class="rp-tag" title="${escapeHtml('Approved despite ' + row.blockers.join('; ') + ': ' + row.entry.override)}">override</span>` : ''}${row.stale ? ' <span class="rp-stale">stale</span>' : ''}${row.entry.comment.trim() ? ` <span class="rp-tag" title="${escapeHtml(row.entry.comment)}">comment</span>` : ''}</td>
        <td>${row.replaces.length ? escapeHtml(row.replaces.join(', ')) : row.replacedBy.length ? `<span class="rp-muted">replaced by ${escapeHtml(row.replacedBy.join(', '))}</span>` : ''}</td>
      </tr>`).join('')}</tbody></table>
    ${unmatchedHtml()}
    <textarea class="rs-copy" id="rs-copy" aria-label="Review summary as Markdown" readonly hidden></textarea>
    <details class="rs-catalog-details"${catalogOpen ? ' open' : ''}><summary>Catalog (all models, ${catalog.length})</summary>${catalogTable(catalog)}</details>
  </div>`;
}

function renderSummary() {
  summaryView.innerHTML = underReview ? summaryHtml() : catalogHtml();
}

function summaryMessage(text) {
  const message = document.getElementById('rs-msg');
  if (message) message.textContent = text;
}

// "Copied" only when the Clipboard API resolved or execCommand reported
// success; otherwise the Markdown is shown for manual selection.
function copyMarkdown() {
  const text = DBMLReview.markdown(MODEL, reviewState);
  const copied = () => summaryMessage('Copied Markdown to the clipboard.');
  const fallback = () => {
    const temp = document.createElement('textarea');
    temp.value = text;
    temp.setAttribute('readonly', '');
    temp.className = 'rs-offscreen';
    document.body.appendChild(temp);
    temp.select();
    let ok = false;
    try { ok = document.execCommand('copy') === true; } catch (error) { ok = false; } finally { temp.remove(); }
    if (ok) { copied(); return; }
    summaryMessage('Copy failed — select the text below');
    const area = document.getElementById('rs-copy');
    if (!area) return;
    area.value = text; area.hidden = false; area.focus(); area.select();
  };
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(copied, fallback);
  else fallback();
}

function downloadJson() {
  const blob = new Blob([JSON.stringify(DBMLReview.exportJson(MODEL, reviewState), null, 2)], { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = 'model-review-' + String(MODEL.project?.name || 'default').replace(/[^\w.-]+/g, '_') + '.json';
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  summaryMessage('Downloaded ' + link.download + '.');
}

summaryView.addEventListener('click', event => {
  const button = event.target.closest('[data-summary]');
  if (button) {
    const action = button.dataset.summary;
    if (action === 'markdown') copyMarkdown();
    else if (action === 'json') downloadJson();
    else if (action === 'clear' && confirm('Clear every sign-off, comment and check for ' + (MODEL.project?.name || 'this project') + ' in this browser?')) {
      reviewState = {};
      try { localStorage.removeItem(reviewKey); } catch (error) { /* in-memory only */ }
      renderSummary();
      if (!reviewPanel.hidden && reviewFocus) refreshSignoff(reviewFocus);
      summaryMessage('Review state cleared.');
    }
    return;
  }
  const row = event.target.closest('tr[data-open]');
  if (row) openReview(row.dataset.open);
});
summaryView.addEventListener('keydown', event => {
  const row = event.target.closest('tr[data-open]');
  if (row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openReview(row.dataset.open); }
});
summaryView.addEventListener('toggle', event => {
  if (event.target.matches?.('.rs-catalog-details')) catalogOpen = event.target.open;
}, true);
onView(view => { if (view === 'summary') renderSummary(); });
