'use strict';

const $ = id => document.getElementById(id);
const state = { type: '', page: 1, total: 0, limit: 48, records: [], stats: {}, active: null, request: null, detailRequest: null, detailVersion: 0 };
const types = [['', 'All references', '◈'], ['character', 'Characters', '人'], ['item', 'Items & materials', '◇'], ['stage', 'Stages & missions', '旗'], ['interface', 'Interface', '▣'], ['miscellaneous', 'Other references', '⋯']];
const statusLabels = { source_verified: 'Source verified', source: 'Source information', source_documented: 'Source documented', manually_verified: 'Manually verified', manual_verified: 'Manually verified', verified: 'Verified', inferred: 'Inferred', unidentified: 'Unidentified', unknown: 'Unknown', unverified: 'Unverified' };
const typeLabels = Object.fromEntries(types.filter(t => t[0]).map(t => [t[0], t[1]]));
const humanize = value => String(value ?? '').replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
const statusLabel = value => statusLabels[value] || humanize(value || 'unknown');
const count = value => Number(value || 0).toLocaleString();
function node(tag, className, text) { const result = document.createElement(tag); if (className) result.className = className; if (text !== undefined) result.textContent = text; return result; }
function localAsset(path) {
  if (!path) return null;
  const value = String(path).replaceAll('\\', '/').replace(/^\/+/, '');
  if (!value.startsWith('assets/') || value.split('/').some(segment => segment === '..')) return null;
  return '/' + value.split('/').map(encodeURIComponent).join('/');
}
function sourceURL(value) { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; } }
async function api(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  if (!response.ok) { let message = `Request failed (${response.status}).`; try { const body = await response.json(); message = body.error || body.message || message; } catch {} throw new Error(message); }
  return response.json();
}
function badge(value, kind = '') {
  const status = String(value || 'unknown');
  const style = /manual/.test(status) ? 'manual' : /unknown|unverified|unidentified|inferred/.test(status) ? 'unknown' : /source/.test(status) ? 'source' : '';
  return node('span', `badge ${kind || style}`, statusLabel(status));
}
function renderNavigation() {
  const byType = state.stats.by_type || state.stats.record_types || state.stats.types || {};
  $('type-nav').replaceChildren(...types.map(([value, label, icon]) => {
    const button = node('button', 'nav-button' + (state.type === value ? ' active' : ''));
    button.type = 'button'; button.setAttribute('aria-pressed', String(state.type === value));
    button.append(node('span', 'nav-icon', icon), node('span', '', label));
    const amount = value ? byType[value] : state.stats.total_records ?? state.stats.records;
    button.append(node('span', 'count', amount == null ? '' : count(amount)));
    button.addEventListener('click', () => { state.type = value; state.page = 1; $('collection-title').textContent = label; renderNavigation(); loadRecords(); });
    return button;
  }));
}
function facetValues(value) {
  if (Array.isArray(value)) return value.map(entry => typeof entry === 'object' ? entry.value ?? entry.name ?? entry.category ?? entry.element ?? entry.rarity ?? entry.verification_status : entry).filter(entry => entry !== undefined && entry !== null && String(entry) !== '');
  return value && typeof value === 'object' ? Object.keys(value) : [];
}
function setOptions(id, values, initialLabel, formatter = String) {
  const select = $(id), previous = select.value;
  select.replaceChildren(new Option(initialLabel, ''), ...[...new Set(values.map(String))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).map(value => new Option(formatter(value), value)));
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
}
function populateFacets() {
  const facets = state.stats.facets || state.stats;
  const categories = facetValues(facets.categories || facets.category);
  setOptions('category', categories, 'All categories');
  setOptions('element', facetValues(facets.elements || facets.element), 'All elements');
  setOptions('rarity', facetValues(facets.rarities || facets.rarity), 'All rarities');
  const statuses = facetValues(facets.statuses || facets.verification_statuses || facets.verification_status);
  setOptions('status', statuses.length ? statuses : ['source_verified', 'inferred', 'unidentified', 'unknown', 'manually_verified'], 'All statuses', statusLabel);
  $('category-options').replaceChildren(...categories.map(value => new Option(String(value), String(value))));
}
async function loadStats() {
  try {
    state.stats = await api('/api/stats'); populateFacets(); renderNavigation();
    const total = state.stats.total_assets ?? state.stats.assets;
    $('index-state').textContent = total == null ? 'Local library' : `${count(total)} local images`;
  } catch (error) { $('index-state').textContent = 'Stats unavailable'; }
}
function addImage(parent, path, name, className = '') {
  const url = localAsset(path);
  const missing = node('span', 'missing-image', 'No image available');
  if (!url) { parent.append(missing); return; }
  const image = node('img', className); image.alt = name || 'Reference image'; image.src = url; image.loading = 'lazy'; image.decoding = 'async';
  image.addEventListener('error', () => { image.remove(); parent.append(missing); }, { once: true }); parent.append(image);
}
function renderCard(record) {
  const name = record.display_name || 'Unnamed reference';
  const card = node('button', 'asset-card'); card.type = 'button'; card.setAttribute('aria-label', `Inspect ${name}`);
  const image = node('div', 'card-image'); addImage(image, record.thumbnail_path || record.local_image_path, name);
  const content = node('div', 'card-content');
  content.append(node('span', 'card-kind', typeLabels[record.record_type] || humanize(record.record_type || 'reference')), node('span', 'card-name', name));
  const subtitle = [];
  if (record.game_id !== null && record.game_id !== undefined && record.game_id !== '') subtitle.push(`ID ${record.game_id}`);
  if (record.element) subtitle.push(record.element);
  if (record.rarity) subtitle.push(`Rarity ${record.rarity}`);
  content.append(node('span', 'card-subtitle', subtitle.join(' · ') || record.category || 'Identifier unknown'));
  const bottom = node('div', 'card-bottom'); bottom.append(badge(record.verification_status));
  card.append(image, content, bottom); card.addEventListener('click', () => openDetails(record.id)); return card;
}
async function loadRecords() {
  state.request?.abort(); const controller = new AbortController(); state.request = controller;
  $('error').hidden = true; $('grid').setAttribute('aria-busy', 'true'); $('results-summary').textContent = 'Searching references…';
  const parameters = new URLSearchParams({ q: $('search').value.trim(), type: state.type, page: state.page, limit: $('limit').value });
  for (const field of ['category', 'element', 'rarity', 'status']) if ($(field).value) parameters.set(field, $(field).value);
  try {
    const data = await api('/api/records?' + parameters, { signal: controller.signal });
    if (controller !== state.request) return;
    state.records = data.records || []; state.total = Number(data.total || 0); state.page = Number(data.page || state.page); state.limit = Number(data.limit || $('limit').value);
    $('grid').replaceChildren(...state.records.map(renderCard)); $('empty').hidden = state.records.length !== 0;
    const start = state.total ? (state.page - 1) * state.limit + 1 : 0, end = Math.min(state.total, start + state.records.length - 1);
    $('results-summary').textContent = state.total ? `${count(start)}–${count(end)} of ${count(state.total)} references` : '0 references';
    const pages = Math.max(1, Math.ceil(state.total / state.limit));
    $('page-label').textContent = `Page ${state.page} of ${count(pages)}`; $('previous').disabled = state.page <= 1; $('next').disabled = state.page >= pages;
  } catch (error) {
    if (error.name === 'AbortError') return;
    $('error').textContent = `Unable to load the library: ${error.message} Check that the local library server is running.`; $('error').hidden = false; $('results-summary').textContent = 'References unavailable';
  } finally { if (controller === state.request) $('grid').setAttribute('aria-busy', 'false'); }
}
function addFact(label, value) { $('record-facts').append(node('dt', '', label), node('dd', '', value === null || value === undefined || value === '' ? 'Unknown' : String(value))); }
function renderValue(value, depth = 0) {
  if (value === null || value === undefined || value === '') return node('span', 'muted', 'Unknown');
  if (Array.isArray(value)) { const list = node('ul'); for (const item of value) { const li = node('li'); li.append(renderValue(item, depth + 1)); list.append(li); } return list; }
  if (typeof value === 'object') {
    const list = node('dl'); for (const [key, item] of Object.entries(value)) { const dd = node('dd'); dd.append(depth > 6 ? node('span', '', JSON.stringify(item)) : renderValue(item, depth + 1)); list.append(node('dt', '', humanize(key)), dd); } return list;
  }
  return node('span', '', String(value));
}
function renderProvenance(record) {
  const sources = Array.isArray(record.sources) ? record.sources : [];
  $('provenance-section').hidden = sources.length === 0;
  $('provenance').replaceChildren(...sources.map((source, index) => {
    const group = node('div', 'source-entry');
    const url = sourceURL(source.source_url);
    const heading = node(url ? 'a' : 'strong', 'source-link', source.source_name || `Source ${index + 1}`);
    if (url) { heading.href = url; heading.target = '_blank'; heading.rel = 'noopener noreferrer'; }
    group.append(heading);
    const facts = node('dl');
    for (const [label, value] of [['Original name', source.display_name], ['Game identifier', source.game_id], ['Source key', source.source_key], ['Category', source.category], ['Element', source.element], ['Rarity', source.rarity], ['Verification', statusLabel(source.verification_status)]]) {
      if (value !== null && value !== undefined && value !== '') facts.append(node('dt', '', label), node('dd', '', String(value)));
    }
    group.append(facts);
    let metadata = source.metadata;
    if (typeof metadata === 'string') { try { metadata = JSON.parse(metadata); } catch {} }
    if (metadata !== null && metadata !== undefined && metadata !== '' && (typeof metadata !== 'object' || Object.keys(metadata).length)) {
      const details = node('details', 'source-metadata'); details.append(node('summary', '', 'Original source metadata'), renderValue(metadata)); group.append(details);
    }
    return group;
  }));
}
function renderPreview(asset, index) {
  const record = state.active, image = $('preview'), path = asset.local_image_path || asset.local_path || asset.path || record.local_image_path;
  const url = localAsset(path); image.hidden = !url; $('preview-missing').hidden = Boolean(url); image.alt = record.display_name || 'Reference image';
  image.onerror = () => { image.hidden = true; $('preview-missing').hidden = false; }; if (url) image.src = url; else image.removeAttribute('src');
  const info = $('image-info'); info.replaceChildren(node('p', '', `Local path: ${path || 'Unavailable'}`));
  const original = asset.original_filename || asset.filename; if (original) info.append(node('p', '', `Original filename: ${original}`));
  if (asset.width && asset.height) info.append(node('p', '', `${asset.width} × ${asset.height} pixels`));
  if (asset.role) info.append(node('p', '', `Image role: ${humanize(asset.role)}`));
  const hash = asset.sha256 || asset.content_hash; if (hash) info.append(node('p', '', `SHA-256: ${hash}`));
  if (url) { const link = node('a', '', 'Open original local image ↗'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; info.append(link); }
  const originals = Array.isArray(asset.source_urls) ? asset.source_urls : [];
  const imageSources = [...originals, asset.source_image_url || record.source_image_url].filter(Boolean);
  const seenURLs = new Set();
  for (const source of imageSources) {
    const originalURL = sourceURL(typeof source === 'string' ? source : source.source_image_url || source.source_url || source.url);
    if (!originalURL || seenURLs.has(originalURL)) continue;
    seenURLs.add(originalURL);
    const line = node('p'); const name = typeof source === 'object' && source.source_name;
    const link = node('a', '', name ? `View ${name} source image ↗` : `View source image ${seenURLs.size} ↗`);
    link.href = originalURL; link.target = '_blank'; link.rel = 'noopener noreferrer'; line.append(link); info.append(line);
  }
  for (const [i, button] of [...$('asset-strip').children].entries()) { button.classList.toggle('active', i === index); button.setAttribute('aria-pressed', String(i === index)); }
}
function renderDetails(record) {
  state.active = record; $('detail-loading').hidden = true; $('detail-body').hidden = false; $('detail-kind').textContent = typeLabels[record.record_type] || humanize(record.record_type || 'Reference');
  $('detail-title').textContent = record.display_name || 'Unnamed reference'; $('detail-badges').replaceChildren(badge(record.verification_status));
  if (record.category) $('detail-badges').append(node('span', 'badge', record.category));
  $('record-facts').replaceChildren(); addFact('Game identifier', record.game_id); addFact('Element', record.element); addFact('Rarity', record.rarity); addFact('Library ID', record.id);
  if (record.canonical_id) addFact('Canonical ID', record.canonical_id);
  if (record.aliases?.length) addFact('Alias IDs', record.aliases.map(alias => typeof alias === 'string' ? alias : alias.alias_id || alias.id).filter(Boolean).join(', '));
  if (record.source_key) addFact('Source key', record.source_key);
  if (record.collected_at || record.collection_timestamp) addFact('Collected', record.collected_at || record.collection_timestamp);
  if (record.updated_at) addFact('Updated', record.updated_at);
  if (record.tags?.length) addFact('Tags', record.tags.join(', '));
  const url = sourceURL(record.source_url); $('source-link').hidden = !url; if (url) $('source-link').href = url; else $('source-link').removeAttribute('href');
  renderProvenance(record);
  let metadata = record.metadata || {}; if (typeof metadata === 'string') { try { metadata = JSON.parse(metadata); } catch {} }
  $('metadata').replaceChildren(renderValue(metadata)); if (typeof metadata === 'object' && !Object.keys(metadata).length) $('metadata').replaceChildren(node('p', 'muted', 'No additional metadata was provided by the source.'));
  const assets = record.assets?.length ? record.assets : [{ local_image_path: record.local_image_path, thumbnail_path: record.thumbnail_path, content_hash: record.content_hash }];
  $('asset-strip').replaceChildren(...assets.map((asset, index) => {
    const button = node('button', 'asset-option'); button.type = 'button'; button.setAttribute('aria-label', `Preview image ${index + 1}`); addImage(button, asset.thumbnail_path || asset.local_image_path || asset.local_path || asset.path, `${record.display_name || 'Reference'}, image ${index + 1}`); button.addEventListener('click', () => renderPreview(asset, index)); return button;
  })); $('asset-strip').hidden = assets.length <= 1;
  const primaryIndex = record.canonical_id ? 0 : Math.max(0, assets.findIndex(asset => asset.local_image_path === record.local_image_path && record.local_image_path));
  renderPreview(assets[primaryIndex], primaryIndex);
  const related = record.related || []; $('related-section').hidden = related.length === 0; $('related').replaceChildren(...related.map(item => {
    const relation = item.record || item; const button = node('button', '', relation.display_name || relation.name || relation.id || item.related_id || 'Related reference'); button.type = 'button'; const id = relation.id || item.related_id;
    button.disabled = !id; if (id) button.addEventListener('click', () => openDetails(id)); return button;
  }));
  $('edit-name').value = record.display_name || ''; $('edit-category').value = record.category || ''; $('edit-tags').value = (record.tags || []).join(', '); $('edit-notes').value = record.notes || '';
  const statuses = [...new Set([record.verification_status || 'unknown', 'manually_verified', 'source_verified', 'inferred', 'unidentified'])];
  $('edit-status').replaceChildren(...statuses.map(value => new Option(statusLabel(value), value))); $('edit-status').value = record.verification_status || 'unknown'; $('save-message').textContent = ''; $('save').disabled = false;
}
async function openDetails(id) {
  state.detailRequest?.abort(); const controller = new AbortController(); state.detailRequest = controller; const version = ++state.detailVersion;
  if (!$('details').open) $('details').showModal(); $('detail-body').hidden = true; $('detail-loading').hidden = false; $('detail-loading').textContent = 'Loading reference…'; $('detail-title').textContent = 'Reference details';
  try { const record = await api('/api/records/' + encodeURIComponent(id), { signal: controller.signal }); if (version === state.detailVersion) renderDetails(record.record || record); }
  catch (error) { if (error.name !== 'AbortError' && version === state.detailVersion) $('detail-loading').textContent = `Unable to load this reference: ${error.message}`; }
}
async function saveLabels(event) {
  event.preventDefault(); if (!state.active) return;
  const id = state.active.id, version = state.detailVersion; $('save').disabled = true; $('save-message').textContent = 'Saving…'; $('save-message').style.color = '';
  const labels = { display_name: $('edit-name').value.trim(), category: $('edit-category').value.trim(), tags: [...new Set($('edit-tags').value.split(',').map(tag => tag.trim()).filter(Boolean))], notes: $('edit-notes').value, verification_status: $('edit-status').value };
  if (!labels.display_name) { $('save-message').textContent = 'Enter a display name.'; $('save').disabled = false; $('edit-name').focus(); return; }
  try {
    const result = await api('/api/records/' + encodeURIComponent(id), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(labels) });
    if (version === state.detailVersion) { const updated = result.record || result; renderDetails(updated.id ? { ...state.active, ...updated } : { ...state.active, ...labels }); $('save-message').textContent = 'Labels saved.'; }
    await loadStats(); await loadRecords();
  } catch (error) { if (version === state.detailVersion) { $('save-message').textContent = `Save failed: ${error.message}`; $('save-message').style.color = 'var(--danger)'; } }
  finally { if (version === state.detailVersion) $('save').disabled = false; }
}
let searchTimer;
$('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.page = 1; loadRecords(); }, 250); });
for (const id of ['category', 'element', 'rarity', 'status', 'limit']) $(id).addEventListener('change', () => { state.page = 1; loadRecords(); });
$('reset').addEventListener('click', () => { clearTimeout(searchTimer); for (const id of ['search', 'category', 'element', 'rarity', 'status']) $(id).value = ''; state.type = ''; state.page = 1; $('collection-title').textContent = 'All references'; renderNavigation(); loadRecords(); });
for (const [id, increment] of [['previous', -1], ['next', 1]]) $(id).addEventListener('click', () => { state.page += increment; loadRecords(); window.scrollTo({ top: 0, behavior: 'instant' }); });
$('close-details').addEventListener('click', () => $('details').close());
$('details').addEventListener('close', () => { state.detailRequest?.abort(); state.detailVersion++; });
$('details').addEventListener('click', event => { if (event.target === $('details')) { const bounds = $('details').getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) $('details').close(); } });
$('edit-form').addEventListener('submit', saveLabels);
document.addEventListener('keydown', event => { if (event.key === '/' && !$('details').open && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) { event.preventDefault(); $('search').focus(); } });
renderNavigation(); loadStats(); loadRecords();
