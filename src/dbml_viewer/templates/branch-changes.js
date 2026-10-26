// Embedded comparison data keeps exported HTML independent of Git and a server.
const comparison = MODEL.comparison || null;
const changedNames = new Set(Object.entries(comparison?.tables || {})
  .filter(([name, change]) => tableByName[name] && ['new', 'modified'].includes(change.status))
  .map(([name]) => name));
const changeContext = new Set(changedNames);
MODEL.refs.forEach(ref => {
  if (changedNames.has(ref.from_table) || changedNames.has(ref.to_table)) {
    changeContext.add(ref.from_table); changeContext.add(ref.to_table);
  }
});
// reviewFocus is the model open in the review panel (review-panel.js sets it).
let branchEnabled = !!comparison, branchFocused = false, reviewFocus = null;

function activeReviewName() {
  return tableByName[reviewFocus] ? reviewFocus : null;
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
    el.classList.toggle('change-selected', focusName === name);
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

// Closes the model review panel and clears the diagram focus.
function closeChanges() {
  const panel = document.getElementById('change-detail');
  if (panel) panel.hidden = true;
  reviewFocus = null;
  applyBranchOverlay();
}

// True when the card is entirely inside the part of the diagram not covered
// by the project header, the review panel (as a sidebar) or the controls.
function cardVisible(name) {
  const el = nodeElements[name];
  if (!el) return false;
  const card = el.getBoundingClientRect(), view = container.getBoundingClientRect();
  const panel = document.getElementById('change-detail');
  let left = view.left;
  if (panel && !panel.hidden && window.innerWidth > NARROW_WIDTH) {
    left = Math.max(left, panel.getBoundingClientRect().right, document.querySelector('.project-header').getBoundingClientRect().right);
  }
  const right = view.right - document.querySelector('.controls').getBoundingClientRect().width - 24;
  return card.left >= left && card.right <= right && card.top >= view.top && card.bottom <= view.bottom;
}

// Emphasize one model and its immediate neighbours in the diagram. The
// user's zoom stays unless the card is out of view; then fit to the model
// and its neighbours, no larger than actual size.
function focusModel(name) {
  reviewFocus = name;
  if (!tableByName[name]) { applyBranchOverlay(); return; }
  selectJoin(null);
  branchEnabled = true;
  applyBranchOverlay();
  if (!cardVisible(name)) fitToScreen(branchContext(), 1);
}

// The toolbar buttons exist only while something is under review.
document.getElementById('branch-toggle')?.addEventListener('click', () => {
  branchEnabled = !branchEnabled;
  if (!branchEnabled) { branchFocused = false; closeChanges(); }
  applyBranchOverlay();
});
const focusButton = document.getElementById('branch-focus');
if (focusButton) {
  focusButton.disabled = !changedNames.size;
  focusButton.addEventListener('click', () => {
    selectJoin(null); closeChanges();
    branchEnabled = true; branchFocused = !branchFocused;
    applyBranchOverlay(); fitToScreen(branchFocused ? changeContext : null);
  });
}
