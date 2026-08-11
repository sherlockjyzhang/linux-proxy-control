const app = document.querySelector('#app');
const isAdmin = window.location.pathname.replace(/\/+$/, '') === '/admin';
const state = { data: null, mappings: [], rootDraft: null, busy: false, notice: null };
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function canonical(value = {}) {
  const hasCanonicalSelection = value.selection && (value.selection.kind === 'region' || value.selection.kind === 'node');
  const hasLegacyRegion = typeof value.region === 'string' && value.region.trim();
  const hasLegacyNode = typeof value.node === 'string' && value.node.trim();
  const selection = hasCanonicalSelection
    ? value.selection
    : hasLegacyRegion && !hasLegacyNode
      ? { kind: 'region', value: value.region }
      : hasLegacyNode && !hasLegacyRegion
        ? { kind: 'node', value: value.node }
        : { kind: 'region', value: '' };
  return {
    ip: String(value.ip || ''),
    selection: { kind: selection.kind, value: String(selection.value || '') },
    allow_cross_region_fallback: Object.prototype.hasOwnProperty.call(value, 'allow_cross_region_fallback')
      ? Boolean(value.allow_cross_region_fallback)
      : Boolean(hasLegacyRegion || hasLegacyNode)
  };
}

function catalog() {
  const source = state.data || {};
  const nodes = (source.nodes || source.catalog?.nodes || []).filter((node) => node && typeof node === 'object' && node.name);
  const declared = (source.regions || source.catalog?.regions || []).filter(Boolean);
  const regions = [...new Set([...declared, ...nodes.map((node) => node.region).filter(Boolean)])];
  return { regions, nodes };
}

function selectionOptions(kind, value) {
  const items = kind === 'region' ? catalog().regions : catalog().nodes;
  const option = kind === 'region'
    ? (item) => `<option value="${esc(item)}" ${item === value ? 'selected' : ''}>${esc(item)}</option>`
    : (item) => `<option value="${esc(item.name)}" ${item.name === value ? 'selected' : ''}>${esc(item.name)}${item.region ? ` (${esc(item.region)})` : ''}</option>`;
  const current = value && !items.some((item) => (kind === 'region' ? item : item.name) === value)
    ? `<option value="${esc(value)}" selected>${esc(value)} (not currently discovered)</option>` : '';
  return `<option value="">Choose ${kind}</option>${current}${items.map(option).join('')}`;
}

function selectionControl(mapping, index, admin) {
  const key = admin ? `data-map-control="${index}"` : 'data-me';
  const selected = canonical(mapping);
  return `<div class="selection" ${key}>
    <div class="segmented" role="group" aria-label="Selection type">
      <button class="segment ${selected.selection.kind === 'region' ? 'active' : ''}" type="button" data-action="kind" data-kind="region">Region</button>
      <button class="segment ${selected.selection.kind === 'node' ? 'active' : ''}" type="button" data-action="kind" data-kind="node">Node</button>
    </div>
    <label class="field"><span>${selected.selection.kind === 'region' ? 'Preferred region' : 'Preferred node'}</span>
      <select data-field="selection-value">${selectionOptions(selected.selection.kind, selected.selection.value)}</select>
    </label>
    <label class="toggle"><input type="checkbox" data-field="fallback" ${selected.allow_cross_region_fallback ? 'checked' : ''}><span>Allow cross-region fallback</span></label>
    <p class="hint">${selected.selection.kind === 'region'
      ? 'Uses the fastest usable node in this region (up to 600 ms).'
      : 'Uses this node at up to 300 ms, then its catalog region at up to 600 ms.'}</p>
  </div>`;
}

function rootView() {
  const me = state.data || {};
  const savedMapping = me.mapping ? canonical(me.mapping) : null;
  const mapping = state.rootDraft || savedMapping || canonical({});
  const effective = me.effective ? canonical(me.effective) : null;
  const decision = me.effective_decision;
  return `<section class="page narrow"><header class="masthead"><div><p class="eyebrow">RPb5 Proxy Control</p><h1>Your connection preference</h1><p class="subtitle">Set the routing preference for ${esc(me.ip || me.client_ip || 'your current address')}.</p></div><a class="admin-link" href="/admin">Administration</a></header>
    ${notice()}
    <section class="panel"><div class="panel-head"><div><h2>Preference</h2><p>Choose one routing mode. The saved preference applies only to your exact IP address.</p></div></div>
      ${selectionControl(mapping, 0, false)}
      <div class="actions"><button class="button" type="button" data-action="save-me" ${state.busy ? 'disabled' : ''}>${state.busy ? 'Saving...' : 'Save preference'}</button></div>
    </section>
    <section class="panel"><div class="panel-head"><div><h2>Saved canonical mapping</h2><p>The shared source-of-truth record for your exact address.</p></div></div>${canonicalSummary(savedMapping, me.ip || me.client_ip)}</section>
    <section class="panel effective"><div class="panel-head"><div><h2>Current effective configuration</h2><p>This reflects the server's current routing decision, including unavailable states.</p></div></div>${effectiveSummary(effective)}${decisionSummary(decision)}</section></section>`;
}

function canonicalSummary(mapping, fallbackIp = '') {
  if (!mapping) return '<div class="empty">No exact mapping is saved for this address.</div>';
  const item = canonical(mapping);
  return `<dl class="canonical-summary"><div><dt>IP</dt><dd>${esc(item.ip || fallbackIp)}</dd></div><div><dt>Selection</dt><dd>${esc(item.selection.kind)}: ${esc(item.selection.value || 'Not configured')}</dd></div><div><dt>Cross-region fallback</dt><dd>${item.allow_cross_region_fallback ? 'Allowed' : 'Disabled'}</dd></div></dl><pre class="canonical-json">${esc(JSON.stringify(item, null, 2))}</pre>`;
}

function effectiveSummary(mapping) {
  if (!mapping) return '<p class="empty">No effective mapping is reported.</p>';
  const item = canonical(mapping);
  return `<dl class="canonical-summary effective-scope"><div><dt>Effective scope</dt><dd>${esc(item.ip)}</dd></div><div><dt>Selection</dt><dd>${esc(item.selection.kind)}: ${esc(item.selection.value || 'Not configured')}</dd></div></dl>`;
}

function decisionSummary(decision, compact = false) {
  if (!decision || typeof decision !== 'object') return '<p class="empty">No effective decision is reported.</p>';
  const node = decision.node === null ? 'No node selected' : decision.node;
  const mode = ['demo', 'real', 'unavailable'].includes(decision.mode) ? decision.mode : 'unknown';
  const status = mode === 'unavailable' ? 'Unavailable' : decision.applied ? 'Applied' : decision.simulated ? 'Simulated' : 'Not applied';
  if (compact) return `<div class="decision-summary compact"><div class="decision-head"><strong>${esc(node)}</strong><span class="tag ${mode === 'unavailable' ? 'warn' : decision.applied ? 'good' : ''}">${esc(status)}</span></div><p class="decision-reason">${esc(decision.reason || 'No reason reported')}</p><div class="decision-flags"><span class="tag">${esc(mode)}</span><span class="tag">Applied: ${decision.applied ? 'yes' : 'no'}</span><span class="tag">Simulated: ${decision.simulated ? 'yes' : 'no'}</span><span class="tag">Probed: ${decision.probed ? 'yes' : 'no'}</span></div></div>`;
  return `<div class="decision-summary"><div class="decision-head"><strong>Effective decision</strong><span class="tag ${mode === 'unavailable' ? 'warn' : decision.applied ? 'good' : ''}">${esc(status)}</span></div><dl class="canonical-summary"><div><dt>Selected node</dt><dd>${esc(node)}</dd></div><div><dt>Reason</dt><dd>${esc(decision.reason || 'No reason reported')}</dd></div><div><dt>Mode</dt><dd>${esc(mode)}</dd></div><div><dt>Applied</dt><dd>${decision.applied ? 'Yes' : 'No'}</dd></div><div><dt>Simulated</dt><dd>${decision.simulated ? 'Yes' : 'No'}</dd></div><div><dt>Latency probed</dt><dd>${decision.probed ? 'Yes' : 'No'}</dd></div></dl></div>`;
}

function mappingRow(mapping, index) {
  const item = canonical(mapping);
  const decision = decisionForMapping(item, index);
  return `<article class="mapping-row" data-map="${index}"><label class="field ip-field"><span>IP or CIDR</span><input data-field="ip" value="${esc(item.ip)}" placeholder="Client IP or CIDR"></label>${selectionControl(item, index, true)}<div class="row-decision">${decisionSummary(decision, true)}</div><button class="icon-button remove" type="button" title="Remove mapping" aria-label="Remove mapping" data-action="remove-map">x</button></article>`;
}

function decisionForMapping(mapping, index) {
  const entries = Array.isArray(state.data?.effective_decisions) ? state.data.effective_decisions : [];
  return (entries.find((entry) => entry && entry.ip === mapping.ip) || entries[index] || {}).decision || null;
}

function adminView() {
  return `<section class="page"><header class="masthead"><div><p class="eyebrow">RPb5 Proxy Control</p><h1>IP routing administration</h1><p class="subtitle">Manage canonical IP and CIDR mapping records.</p></div><a class="admin-link" href="/">My preference</a></header>
    ${notice()}
    <section class="panel"><div class="panel-head"><div><h2>Mappings</h2><p>Each record uses exactly one selection mode. New records default to no cross-region fallback.</p></div><button class="button secondary" type="button" data-action="add-map">Add mapping</button></div>
      <div class="mapping-list">${state.mappings.length ? state.mappings.map(mappingRow).join('') : '<div class="empty">No IP or CIDR mappings configured.</div>'}</div>
      <div class="actions footer-actions"><button class="button" type="button" data-action="save-mappings" ${state.busy ? 'disabled' : ''}>${state.busy ? 'Saving...' : 'Save mappings'}</button></div>
    </section></section>`;
}

function notice() { return state.notice ? `<div class="notice ${esc(state.notice.type)}">${esc(state.notice.text)}</div>` : ''; }
function render() { app.innerHTML = isAdmin ? adminView() : rootView(); bind(); }

function readControl(container, withIp) {
  const kind = container.querySelector('.segment.active')?.dataset.kind || 'region';
  return canonical({
    ip: withIp ? container.querySelector('[data-field="ip"]').value.trim() : '',
    selection: { kind, value: container.querySelector('[data-field="selection-value"]').value },
    allow_cross_region_fallback: container.querySelector('[data-field="fallback"]').checked
  });
}

function syncMappingDrafts() {
  const rows = [...document.querySelectorAll('.mapping-row')];
  if (rows.length) state.mappings = rows.map((row) => readControl(row, true));
}

function bind() {
  document.querySelectorAll('[data-action="kind"]').forEach((button) => button.onclick = () => {
    syncMappingDrafts();
    const container = button.closest('[data-map-control], [data-me]');
    const current = readControl(container, container.hasAttribute('data-map-control'));
    current.selection = { kind: button.dataset.kind, value: '' };
    if (container.hasAttribute('data-map-control')) state.mappings[Number(container.dataset.mapControl)] = current;
    else state.rootDraft = current;
    render();
  });
  document.querySelectorAll('[data-action="remove-map"]').forEach((button) => button.onclick = () => { syncMappingDrafts(); state.mappings.splice(Number(button.closest('[data-map]').dataset.map), 1); render(); });
  document.querySelectorAll('[data-action]').forEach((button) => {
    if (!['save-me', 'save-mappings', 'add-map'].includes(button.dataset.action)) return;
    button.onclick = () => action(button.dataset.action);
  });
}

async function action(name) {
  try {
    if (name === 'add-map') { syncMappingDrafts(); state.mappings.push(canonical({})); render(); return; }
    if (name === 'save-me') {
      const mapping = readControl(document.querySelector('[data-me]'), false);
      state.rootDraft = mapping;
      state.busy = true; render();
      state.data = await api('/me', { method: 'PUT', body: JSON.stringify({ selection: mapping.selection, allow_cross_region_fallback: mapping.allow_cross_region_fallback }) });
      state.rootDraft = null;
      state.notice = { type: 'success', text: 'Your preference has been saved.' };
    } else {
      const mappings = [...document.querySelectorAll('.mapping-row')].map((row) => readControl(row, true));
      state.mappings = mappings;
      state.busy = true; render();
      const result = await api('/mappings', { method: 'PUT', body: JSON.stringify({ mappings }) });
      state.data = result;
      state.mappings = (result.mappings || []).map(canonical);
      state.notice = { type: 'success', text: 'Mappings have been saved.' };
    }
  } catch (error) { state.notice = { type: 'error', text: error.message }; }
  finally { state.busy = false; render(); }
}

async function load() {
  try {
    state.data = await api(isAdmin ? '/mappings' : '/me');
    state.mappings = (state.data.mappings || []).map(canonical);
    state.rootDraft = null;
  } catch (error) { state.notice = { type: 'error', text: error.message }; }
  render();
}
load();
