// Embedded comparison data keeps exported HTML independent of Git and a server.
const comparison = MODEL.comparison || null;
const changedNames = new Set(Object.entries(comparison?.tables || {})
  .filter(([name, change]) => tableByName[name] && ['new', 'modified'].includes(change.status))
  .map(([name]) => name));
const reviewNames = Object.keys(comparison?.tables || {}).filter(name => comparison.tables[name].status !== 'unchanged');
const changeContext = new Set(changedNames);
MODEL.refs.forEach(ref => {
  if (changedNames.has(ref.from_table) || changedNames.has(ref.to_table)) {
    changeContext.add(ref.from_table); changeContext.add(ref.to_table);
  }
});
let branchEnabled = !!comparison, branchFocused = false, reviewIndex = -1;

function activeReviewName() {
  const name = reviewNames[reviewIndex];
  return tableByName[name] ? name : null;
}

function branchContext() {
  const focusName = activeReviewName();
  const context = focusName ? new Set([focusName]) : branchFocused ? changeContext : null;
  if (focusName) MODEL.refs.forEach(ref => {
    if (ref.from_table === focusName || ref.to_table === focusName) {
      context.add(ref.from_table); context.add(ref.to_table);
    }
  });
  return context;
}

function applyBranchOverlay() {
  const focusName = activeReviewName();
  const context = branchContext();
  canvas.classList.toggle('branch-overlay', branchEnabled);
  Object.entries(nodeElements).forEach(([name, el]) => {
    el.classList.toggle('branch-muted', changedNames.size > 0 && !changedNames.has(name) && !context);
    el.classList.toggle('branch-outside', !!context && !context.has(name));
    el.classList.toggle('change-selected', reviewIndex >= 0 && reviewNames[reviewIndex] === name);
  });
  edgesSvg.querySelectorAll('.edge').forEach(edge => {
    const path = edge.querySelector('.edge-path');
    const incident = focusName ? path.dataset.from === focusName || path.dataset.to === focusName
      : changedNames.has(path.dataset.from) || changedNames.has(path.dataset.to);
    edge.classList.toggle('branch-muted', changedNames.size > 0 && !incident && !context);
    edge.classList.toggle('branch-outside', !!context && !incident);
  });
  Object.values(groupElements).forEach(group => {
    const members = visualGroups.find(item => item.key === group.dataset.group)?.members || [];
    group.classList.toggle('branch-outside', !!context && !members.some(name => context.has(name)));
  });
  document.getElementById('branch-toggle')?.setAttribute('aria-pressed', String(branchEnabled));
  document.getElementById('branch-focus')?.setAttribute('aria-pressed', String(branchFocused));
}

function closeChanges() {
  const panel = document.getElementById('change-detail');
  if (panel) panel.hidden = true;
  reviewIndex = -1;
  applyBranchOverlay();
}

function reviewChange(name) {
  reviewIndex = reviewNames.indexOf(name);
  if (reviewIndex < 0) return;
  selectJoin(null);
  branchEnabled = true;
  const change = comparison.tables[name];
  document.getElementById('change-name').textContent = name;
  const status = document.getElementById('change-status');
  status.textContent = {new: '+ New model', modified: '~ Modified model', removed: '− Removed model'}[change.status];
  status.className = 'status-' + change.status;
  const list = document.getElementById('change-list');
  list.replaceChildren(...change.details.map(detail => {
    const li = document.createElement('li'); li.textContent = detail; return li;
  }));
  document.getElementById('change-index').textContent = `${reviewIndex + 1} of ${reviewNames.length}`;
  document.getElementById('change-detail').hidden = false;
  applyBranchOverlay();
  if (tableByName[name]) {
    fitToScreen(branchContext());
  }
}

if (comparison) {
  document.getElementById('branch-toggle').addEventListener('click', () => {
    branchEnabled = !branchEnabled;
    if (!branchEnabled) { branchFocused = false; closeChanges(); }
    applyBranchOverlay();
  });
  document.getElementById('branch-focus').disabled = !changedNames.size;
  document.getElementById('branch-review').disabled = !reviewNames.length;
  document.getElementById('branch-focus').addEventListener('click', () => {
    selectJoin(null); closeChanges();
    branchEnabled = true; branchFocused = !branchFocused;
    applyBranchOverlay(); fitToScreen(branchFocused ? changeContext : null);
  });
  document.getElementById('branch-review').addEventListener('click', () => reviewChange(reviewNames[0]));
  document.getElementById('close-changes').addEventListener('click', closeChanges);
  document.getElementById('change-prev').addEventListener('click', () => reviewChange(reviewNames[(reviewIndex - 1 + reviewNames.length) % reviewNames.length]));
  document.getElementById('change-next').addEventListener('click', () => reviewChange(reviewNames[(reviewIndex + 1) % reviewNames.length]));
  canvas.addEventListener('click', event => {
    const badge = event.target.closest('.change-badge');
    if (badge) reviewChange(badge.closest('.table-node').dataset.table);
  });
}
