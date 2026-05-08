// kuberly-graph dashboard SPA — vanilla JS, no build pipeline.
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";

mermaid.initialize({ startOnLoad: false, theme: "default", securityLevel: "loose" });

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  layers: [],
  selectedLayer: null,
  selectedNodeId: null,
};

// ---------- HTTP helpers ----------
async function fetchJson(path) {
  const r = await fetch(path);
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`HTTP ${r.status}: ${text}`);
  }
  return r.json();
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// ---------- tab switching ----------
function setupTabs() {
  $$("a[data-tab]").forEach((a) => {
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      const tab = a.dataset.tab;
      $$("a[data-tab]").forEach((x) => x.classList.remove("active"));
      $$(".tab").forEach((x) => x.classList.remove("active"));
      a.classList.add("active");
      const panel = $(`#tab-${tab}`);
      if (panel) panel.classList.add("active");
    });
  });
}

// ---------- summary / layers ----------
async function loadOverview() {
  let layers = [];
  let stats = {};
  try {
    layers = await fetchJson("/api/v1/layers");
  } catch (e) {
    showError("layers", e);
  }
  try {
    stats = await fetchJson("/api/v1/stats");
  } catch (e) {
    showError("stats", e);
  }
  state.layers = Array.isArray(layers) ? layers : [];
  renderLayerSidebar();
  renderSummaryCards(stats);
  populateLayerSelects();
  toggleEmptyBanner(layers, stats);
}

function toggleEmptyBanner(layers, stats) {
  const total = (stats && stats.totals && stats.totals.nodes) || 0;
  const banner = $("#empty-banner");
  if (!banner) return;
  if (total === 0) banner.classList.remove("hidden");
  else banner.classList.add("hidden");
}

function renderLayerSidebar() {
  const list = $("#layer-list");
  if (state.layers.length === 0) {
    list.innerHTML = '<li class="muted">no layers yet</li>';
    return;
  }
  list.innerHTML = "";
  // 'all' pseudo-entry first
  const liAll = document.createElement("li");
  const aAll = document.createElement("a");
  aAll.href = "#";
  aAll.dataset.layer = "";
  aAll.textContent = "all layers";
  if (state.selectedLayer === null) aAll.classList.add("selected");
  aAll.addEventListener("click", (e) => { e.preventDefault(); selectLayer(null); });
  liAll.appendChild(aAll);
  list.appendChild(liAll);

  state.layers.forEach((l) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = "#";
    a.dataset.layer = l.name;
    a.innerHTML = `${escapeHtml(l.name)} <span class="layer-pill">${l.node_count}n</span>`;
    if (state.selectedLayer === l.name) a.classList.add("selected");
    a.addEventListener("click", (e) => { e.preventDefault(); selectLayer(l.name); });
    li.appendChild(a);
    list.appendChild(li);
  });
}

function selectLayer(name) {
  state.selectedLayer = name;
  renderLayerSidebar();
  $("#filter-layer").value = name || "";
  // switch to nodes tab
  $('a[data-tab="nodes"]').click();
  loadNodes();
}

function renderSummaryCards(stats) {
  const host = $("#summary-cards");
  host.innerHTML = "";
  if (!stats || !stats.per_layer) {
    host.innerHTML = '<div class="card muted">no stats available — populate the graph and refresh</div>';
    return;
  }
  // totals card
  const totals = stats.totals || {};
  host.appendChild(card("totals", `<div class="row"><span>nodes</span><b>${totals.nodes ?? 0}</b></div>
                                   <div class="row"><span>edges</span><b>${totals.edges ?? 0}</b></div>
                                   <div class="row"><span>layers</span><b>${state.layers.length}</b></div>`));
  // per-layer cards (sorted by node_count desc)
  const layersByCount = state.layers.slice().sort((a, b) => (b.node_count || 0) - (a.node_count || 0));
  layersByCount.forEach((l) => {
    const refresh = l.last_refresh ? new Date(l.last_refresh).toLocaleString() : "never";
    host.appendChild(card(l.name, `
      <div class="row"><span>type</span><span class="muted">${escapeHtml(l.type)}</span></div>
      <div class="row"><span>nodes</span><b>${l.node_count}</b></div>
      <div class="row"><span>edges</span><b>${l.edge_count}</b></div>
      <div class="row"><span>last refresh</span><span class="muted">${escapeHtml(refresh)}</span></div>
    `));
  });
}

function card(title, body) {
  const el = document.createElement("div");
  el.className = "card";
  el.innerHTML = `<h3>${escapeHtml(title)}</h3>${body}`;
  return el;
}

function populateLayerSelects() {
  ["#filter-layer", "#anom-layer"].forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    const cur = el.value;
    el.innerHTML = '<option value="">(any)</option>';
    state.layers.forEach((l) => {
      const opt = document.createElement("option");
      opt.value = l.name;
      opt.textContent = `${l.name} (${l.node_count})`;
      el.appendChild(opt);
    });
    if (cur) el.value = cur;
  });
}

// ---------- nodes tab ----------
async function loadNodes() {
  const layer = $("#filter-layer").value;
  const type = $("#filter-type").value.trim();
  const name = $("#filter-name").value.trim();
  const limit = $("#filter-limit").value || "50";
  const params = new URLSearchParams();
  if (layer) params.set("layer", layer);
  if (type) params.set("type", type);
  if (name) params.set("name", name);
  params.set("limit", limit);
  const tbody = $("#node-table tbody");
  tbody.innerHTML = '<tr><td colspan="4" class="muted">loading…</td></tr>';
  try {
    const data = await fetchJson(`/api/v1/nodes?${params.toString()}`);
    const rows = data.nodes || [];
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="muted">no matches</td></tr>';
      return;
    }
    tbody.innerHTML = "";
    rows.forEach((n) => {
      const tr = document.createElement("tr");
      tr.dataset.id = n.id;
      tr.innerHTML = `<td>${escapeHtml(n.id)}</td>
                      <td>${escapeHtml(n.type ?? "")}</td>
                      <td>${escapeHtml(n.layer ?? "")}</td>
                      <td>${escapeHtml(n.label ?? "")}</td>`;
      tr.addEventListener("click", () => selectNode(n.id));
      tbody.appendChild(tr);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">error: ${escapeHtml(e.message)}</td></tr>`;
  }
}

async function selectNode(id) {
  state.selectedNodeId = id;
  $$("#node-table tbody tr").forEach((tr) => {
    tr.classList.toggle("selected", tr.dataset.id === id);
  });
  const pane = $("#node-detail");
  pane.innerHTML = '<p class="muted">loading…</p>';
  try {
    const enc = encodeURIComponent(id);
    const [detail, neighbors] = await Promise.all([
      fetchJson(`/api/v1/nodes/${enc}`),
      fetchJson(`/api/v1/nodes/${enc}/neighbors`),
    ]);
    const node = detail.node || {};
    const inc = neighbors.incoming || [];
    const out = neighbors.outgoing || [];
    pane.innerHTML = `
      <h3>${escapeHtml(node.label || node.id || id)}</h3>
      <div class="muted">${escapeHtml(node.type || "")} · ${escapeHtml(node.layer || "")}</div>
      <pre>${escapeHtml(JSON.stringify(node, null, 2))}</pre>
      <h3>incoming (${inc.length})</h3>
      <ul>${inc.slice(0, 25).map((e) => `<li>${escapeHtml(e.relation)} ← ${escapeHtml(e.source)}</li>`).join("")}</ul>
      <h3>outgoing (${out.length})</h3>
      <ul>${out.slice(0, 25).map((e) => `<li>${escapeHtml(e.relation)} → ${escapeHtml(e.target)}</li>`).join("")}</ul>
      <h3>neighbourhood</h3>
      <div class="mermaid-host" id="node-mermaid"></div>
    `;
    renderNodeMermaid(node, inc, out);
  } catch (e) {
    pane.innerHTML = `<p class="muted">error: ${escapeHtml(e.message)}</p>`;
  }
}

function safeId(s) {
  return "n_" + String(s).replace(/[^A-Za-z0-9]/g, "_").slice(0, 80);
}

async function renderNodeMermaid(node, inc, out) {
  const host = $("#node-mermaid");
  if (!host) return;
  const lines = ["graph LR"];
  const center = safeId(node.id);
  const centerLabel = (node.label || node.id || "").replace(/"/g, "'").slice(0, 40);
  lines.push(`  ${center}["${centerLabel}"]:::center`);
  inc.slice(0, 10).forEach((e) => {
    const id = safeId(e.source);
    const lbl = (e.label || e.source).replace(/"/g, "'").slice(0, 40);
    lines.push(`  ${id}["${lbl}"] -->|${e.relation || ""}| ${center}`);
  });
  out.slice(0, 10).forEach((e) => {
    const id = safeId(e.target);
    const lbl = (e.label || e.target).replace(/"/g, "'").slice(0, 40);
    lines.push(`  ${center} -->|${e.relation || ""}| ${id}["${lbl}"]`);
  });
  lines.push("  classDef center fill:#fde68a,stroke:#92400e,stroke-width:2px;");
  try {
    const { svg } = await mermaid.render("graph_node_" + Date.now(), lines.join("\n"));
    host.innerHTML = svg;
  } catch (e) {
    host.textContent = "mermaid render error: " + e.message;
  }
}

// ---------- search ----------
function setupSearch() {
  const box = $("#search-box");
  let debounce;
  box.addEventListener("input", () => {
    clearTimeout(debounce);
    const q = box.value.trim();
    if (q.length < 2) {
      $("#search-results").innerHTML = "";
      return;
    }
    debounce = setTimeout(() => runSearch(q), 250);
  });
}

async function runSearch(q) {
  const ul = $("#search-results");
  ul.innerHTML = '<li class="muted">searching…</li>';
  try {
    const data = await fetchJson(`/api/v1/search?q=${encodeURIComponent(q)}&limit=15`);
    const hits = data.hits || [];
    if (hits.length === 0) { ul.innerHTML = '<li class="muted">no hits</li>'; return; }
    ul.innerHTML = "";
    hits.forEach((h) => {
      const li = document.createElement("li");
      li.innerHTML = `${escapeHtml(h.label || h.id)} <span class="layer-pill">${escapeHtml(h.layer || "?")}</span>`;
      li.addEventListener("click", () => {
        $('a[data-tab="nodes"]').click();
        selectNode(h.id);
      });
      ul.appendChild(li);
    });
  } catch (e) {
    ul.innerHTML = `<li class="muted">error: ${escapeHtml(e.message)}</li>`;
  }
}

// ---------- anomalies ----------
async function loadAnomalies() {
  const layer = $("#anom-layer").value;
  const limit = $("#anom-limit").value || "20";
  const params = new URLSearchParams();
  if (layer) params.set("layer", layer);
  params.set("limit", limit);
  const tbody = $("#anomaly-table tbody");
  tbody.innerHTML = '<tr><td colspan="5" class="muted">loading…</td></tr>';
  try {
    const data = await fetchJson(`/api/v1/anomalies?${params.toString()}`);
    const rows = data.anomalies || [];
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">no anomalies (or layers not populated)</td></tr>';
      return;
    }
    tbody.innerHTML = "";
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      tr.dataset.id = r.id;
      tr.innerHTML = `<td>${escapeHtml(r.score)}</td>
                      <td>${escapeHtml(r.id)}</td>
                      <td>${escapeHtml(r.type ?? "")}</td>
                      <td>${escapeHtml(r.layer ?? "")}</td>
                      <td>${escapeHtml(r.why ?? "")}</td>`;
      tr.addEventListener("click", () => {
        $('a[data-tab="nodes"]').click();
        selectNode(r.id);
      });
      tbody.appendChild(tr);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="muted">error: ${escapeHtml(e.message)}</td></tr>`;
  }
}

// ---------- service one-pager ----------
async function loadService() {
  const name = $("#svc-name").value.trim();
  const env = $("#svc-env").value.trim();
  if (!name) return;
  $("#svc-json").textContent = "loading…";
  $("#svc-mermaid").innerHTML = "";
  try {
    const enc = encodeURIComponent(name);
    const params = env ? `?env=${encodeURIComponent(env)}` : "";
    const profile = await fetchJson(`/api/v1/service/${enc}${params}`);
    $("#svc-json").textContent = JSON.stringify(profile, null, 2);
    const merm = await fetchJson(`/api/v1/service/${enc}/mermaid${params}`);
    if (merm && merm.mermaid) {
      try {
        const { svg } = await mermaid.render("graph_svc_" + Date.now(), merm.mermaid);
        $("#svc-mermaid").innerHTML = svg;
      } catch (e) {
        $("#svc-mermaid").textContent = "mermaid render error: " + e.message;
      }
    }
  } catch (e) {
    $("#svc-json").textContent = "error: " + e.message;
  }
}

// ---------- bootstrap ----------
function showError(scope, e) {
  console.error(scope, e);
}

function setupHandlers() {
  $("#btn-refresh").addEventListener("click", () => loadOverview());
  $("#btn-apply-filter").addEventListener("click", loadNodes);
  $("#btn-load-anomalies").addEventListener("click", loadAnomalies);
  $("#btn-load-service").addEventListener("click", loadService);
}

setupTabs();
setupSearch();
setupHandlers();
loadOverview();
