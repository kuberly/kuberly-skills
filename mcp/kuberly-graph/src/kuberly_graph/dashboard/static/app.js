// kuberly-graph dashboard SPA — vanilla JS, no build pipeline.
//
// Tabs (v0.52.0):
//   - dashboard      overlays + KPIs + AWS Architecture tiles (real data).
//   - graph          3D force-directed; group-by category/layer/type/community.
//   - stack          Stack Overview — meta-layer graph-of-graphs (2D force).
//   - compliance     ComplianceLayer findings table + KPIs + chip filters.
//
// All visual tokens come from style.css :root. This file does the data
// glue + DOM wiring.

import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";

mermaid.initialize({
  startOnLoad: false,
  theme: "dark",
  securityLevel: "loose",
  fontFamily: "Geist, system-ui, sans-serif",
});

// ---- category palette (mirror style.css :root) -------------------------
const CATEGORY_COLORS = {
  iac_files:          "#1677ff",
  tg_state:           "#ff9900",
  k8s_resources:      "#ff5552",
  docs:               "#9da3ad",
  cue:                "#a259ff",
  ci_cd:              "#3ddc84",
  applications:       "#ff4f9c",
  live_observability: "#f5b800",
  aws:                "#ff9900",
  dependency:         "#c0c4cc",
  meta:               "#ffffff",
};

const CATEGORY_LABELS = {
  iac_files:          "IAC FILES",
  tg_state:           "TG STATE",
  k8s_resources:      "K8S",
  docs:               "DOCS",
  cue:                "CUE",
  ci_cd:              "CI/CD",
  applications:       "APPLICATIONS",
  live_observability: "LIVE",
  aws:                "AWS",
  dependency:         "DEPS",
  meta:               "META",
};

const CATEGORY_ORDER = [
  "iac_files", "tg_state", "k8s_resources", "applications",
  "ci_cd", "cue", "docs", "live_observability", "aws",
  "dependency", "meta",
];

// 16-colour palette for community groups (HSL ring).
const COMMUNITY_PALETTE = [
  "#1677ff", "#ff9900", "#ff5552", "#3ddc84",
  "#a259ff", "#f5b800", "#ff4f9c", "#9da3ad",
  "#1abcfe", "#fb923c", "#22c55e", "#e11d48",
  "#7c3aed", "#facc15", "#ec4899", "#06b6d4",
];
function communityColor(idx) {
  if (idx == null || idx < 0) return "#666";
  return COMMUNITY_PALETTE[idx % COMMUNITY_PALETTE.length];
}

// layer_type → colour for the Stack Overview meta graph.
const LAYER_TYPE_COLORS = {
  cold:     "#1677ff",  // blue
  live:     "#ff9900",  // orange
  derived:  "#a259ff",  // purple
  capstone: "#ff4f9c",  // pink
  meta:     "#ffffff",  // white
  unknown:  "#9da3ad",  // gray
};

// ---- state -------------------------------------------------------------
const STATE = {
  stats: null,
  layers: [],
  graph: { nodes: [], edges: [] },
  byCategory: new Map(),
  byId: new Map(),
  activeCategories: new Set(CATEGORY_ORDER),  // all on by default
  selected: null,
  groupBy: "category",
  search: "",
  Graph3D: null,
  StackGraph: null,         // 3d-force-graph instance for Stack Overview
  meta: null,               // {nodes, links}
  aws: null,                // /api/v1/aws-services payload
  compliance: null,         // /api/v1/compliance payload
  cmpFilters: { severity: null, rule_id: null, sortBy: null, sortDir: 1 },
  communities: null,        // {communities: {id: idx}, sizes: [...]}
  communityFilter: null,    // sticky layer filter for communities API
};

// ---- helpers -----------------------------------------------------------
function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    e.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  }
  return e;
}
function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll("\"", "&quot;");
}
async function fetchJSON(url) {
  const r = await fetch(url, { credentials: "same-origin" });
  if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
  return r.json();
}

// ---- bootstrap ---------------------------------------------------------
async function init() {
  wireTabs();
  wireEmptyBanner();
  wireSidebar();
  wireGraphControls();

  await Promise.all([loadStats(), loadLayers()]);
  await loadGraph();
  // Background-load aux data so tabs feel instant on first switch.
  loadMetaOverview().catch(e => console.error("meta-overview load failed", e));
  loadAwsServices().catch(e => console.error("aws-services load failed", e));
  loadCompliance().catch(e => console.error("compliance load failed", e));

  renderDashboard();
  renderChips();
}

document.addEventListener("DOMContentLoaded", init);

// ---- API loaders -------------------------------------------------------
async function loadStats() {
  try {
    STATE.stats = await fetchJSON("/api/v1/stats");
  } catch (exc) {
    console.error("stats fetch failed", exc);
    STATE.stats = { total_nodes: 0, total_edges: 0, per_layer: {} };
  }
  $("#footer-persist").textContent = STATE.stats.persist_dir || "?";
  if ((STATE.stats.total_nodes || 0) === 0) {
    $("#empty-banner").classList.remove("hidden");
  }
}

async function loadLayers() {
  try {
    const arr = await fetchJSON("/api/v1/layers");
    STATE.layers = Array.isArray(arr) ? arr : [];
  } catch (exc) {
    console.error("layers fetch failed", exc);
    STATE.layers = [];
  }
}

async function loadGraph() {
  try {
    const data = await fetchJSON("/api/v1/graph?limit=5000");
    STATE.graph = { nodes: data.nodes || [], edges: data.edges || [] };
    STATE.byId = new Map(STATE.graph.nodes.map(n => [n.id, n]));
    STATE.byCategory = new Map();
    for (const n of STATE.graph.nodes) {
      const c = n.category || "dependency";
      if (!STATE.byCategory.has(c)) STATE.byCategory.set(c, []);
      STATE.byCategory.get(c).push(n);
    }
  } catch (exc) {
    console.error("graph fetch failed", exc);
    STATE.graph = { nodes: [], edges: [] };
  }
}

async function loadMetaOverview() {
  try {
    STATE.meta = await fetchJSON("/api/v1/meta-overview");
  } catch (exc) {
    console.error("meta-overview fetch failed", exc);
    STATE.meta = { nodes: [], links: [] };
  }
}

async function loadAwsServices() {
  try {
    STATE.aws = await fetchJSON("/api/v1/aws-services");
  } catch (exc) {
    console.error("aws-services fetch failed", exc);
    STATE.aws = { total_resources: 0, service_categories: [] };
  }
  if (document.body.classList.contains("view-dashboard")) {
    renderArchTiles();
  }
}

async function loadCompliance() {
  try {
    STATE.compliance = await fetchJSON("/api/v1/compliance");
  } catch (exc) {
    console.error("compliance fetch failed", exc);
    STATE.compliance = { total: 0, by_severity: {}, by_rule: {}, findings: [] };
  }
  if (document.body.classList.contains("view-compliance")) {
    renderCompliance();
  }
}

async function loadCommunities(opts = {}) {
  // Build the same filter the visible graph has: union of active categories
  // is hard to encode as one ?layer=, so we send no layer filter and rely on
  // the API's default 5000-node cap. For "filter to a single category" we
  // can add a layer hint via STATE.communityFilter (a single layer name).
  const params = new URLSearchParams();
  params.set("algorithm", "modularity");
  params.set("limit", "5000");
  if (STATE.communityFilter) params.set("layer", STATE.communityFilter);
  const url = "/api/v1/communities?" + params.toString();
  try {
    STATE.communities = await fetchJSON(url);
  } catch (exc) {
    console.error("communities fetch failed", exc);
    STATE.communities = { communities: {}, sizes: [], modularity: 0, community_count: 0 };
  }
  return STATE.communities;
}

// ---- Dashboard rendering ----------------------------------------------
function renderDashboard() {
  renderOverlaysStrip();
  renderKeyFacts();
  renderArchTiles();
}

function renderOverlaysStrip() {
  const host = $("#overlays-strip");
  host.innerHTML = "";
  // Build per-layer summary from stats.per_layer (authoritative) ∪ layers list.
  const per = (STATE.stats && STATE.stats.per_layer) || {};
  const seen = new Set();
  const rows = [];
  for (const [layer, info] of Object.entries(per)) {
    rows.push({ layer, nodes: info.nodes || 0 });
    seen.add(layer);
  }
  for (const row of STATE.layers) {
    if (!row) continue;
    const name = row.layer || row.name;
    if (!name || seen.has(name)) continue;
    rows.push({ layer: name, nodes: row.nodes || row.node_count || 0 });
  }
  rows.sort((a, b) => b.nodes - a.nodes);
  if (!rows.length) {
    host.innerHTML = '<span class="muted">no layers populated yet</span>';
    return;
  }
  for (const row of rows) {
    const cat = layerToCategory(row.layer);
    const dot = el("span", { class: "dot" });
    dot.style.background = CATEGORY_COLORS[cat] || "#888";
    host.appendChild(el(
      "span", { class: "overlay-chip", title: row.layer },
      dot,
      el("span", { class: "name" }, row.layer),
      el("span", { class: "ct" }, String(row.nodes)),
    ));
  }
}

function layerToCategory(layer) {
  const map = {
    code: "iac_files", static: "iac_files", iac: "iac_files",
    terragrunt: "iac_files", treesitter: "iac_files",
    state: "tg_state", tg_state: "tg_state", tofu_state: "tg_state",
    k8s: "k8s_resources", kubernetes: "k8s_resources", kubectl: "k8s_resources",
    docs: "docs", doc: "docs",
    cue: "cue", schema: "cue", cue_schema: "cue",
    ci_cd: "ci_cd", image_build: "ci_cd", github_actions: "ci_cd",
    applications: "applications", rendered: "applications", rendered_apps: "applications",
    components: "applications",
    logs: "live_observability", metrics: "live_observability",
    traces: "live_observability", alerts: "live_observability",
    live: "live_observability", live_observability: "live_observability",
    profiles: "live_observability", compliance: "live_observability",
    cost: "live_observability", dns: "live_observability", secrets: "live_observability",
    aws: "aws", aws_network: "aws", aws_iam: "aws",
    aws_compute: "aws", aws_storage: "aws", aws_rds: "aws", aws_s3: "aws",
    dependency: "dependency", deps: "dependency",
    meta: "meta",
  };
  if (map[layer]) return map[layer];
  if (layer && layer.startsWith("aws")) return "aws";
  if (layer && layer.startsWith("k8s")) return "k8s_resources";
  return "dependency";
}

function renderKeyFacts() {
  let k8sVer = "—";
  for (const n of STATE.graph.nodes) {
    if ((n.type || "").toLowerCase() === "node" || (n.id || "").startsWith("k8s_node:")) {
      const v = (n.label || "").match(/v?\d+\.\d+\.\d+/);
      if (v) { k8sVer = v[0]; break; }
    }
  }
  const apps = (STATE.byCategory.get("applications") || []).length;
  $("#kpi-k8s").textContent = k8sVer;
  $("#kpi-apps").textContent = apps > 0 ? String(apps) : "—";
}

// ---- AWS Architecture tiles (real data via /api/v1/aws-services) ------
const AWS_ICON = {
  "Compute":           "simple-icons:awslambda",
  "Storage":           "mdi:database",
  "Database":          "mdi:database-outline",
  "Network":           "mdi:lan",
  "Security/IAM":      "mdi:shield-lock-outline",
  "Edge/CDN":          "mdi:earth",
  "Monitoring":        "mdi:chart-line",
  "Lambda/Serverless": "simple-icons:awslambda",
  "Other":             "simple-icons:amazonaws",
};

function renderArchTiles() {
  const host = $("#arch-categories");
  const summary = $("#arch-summary");
  host.innerHTML = "";
  if (!STATE.aws || !STATE.aws.service_categories || STATE.aws.service_categories.length === 0) {
    host.innerHTML = `<div class="arch-empty muted">No AWS scanner data yet — populate the graph then refresh.</div>`;
    summary.textContent = "—";
    return;
  }
  summary.textContent = `— ${STATE.aws.total_resources} resources across ${STATE.aws.category_count} categories`;
  for (const cat of STATE.aws.service_categories) {
    const block = el("div", { class: "arch-cat" });
    const head = el("div", { class: "arch-cat-head" },
      el("iconify-icon", { icon: AWS_ICON[cat.category] || "simple-icons:amazonaws", width: "18", height: "18" }),
      el("span", { class: "arch-cat-name" }, cat.category),
      el("span", { class: "arch-cat-count" }, `${cat.resource_count} resources · ${cat.service_count} services`),
    );
    block.appendChild(head);
    const grid = el("div", { class: "arch-tiles" });
    for (const svc of cat.services) {
      const tile = el("div", {
        class: "arch-tile",
        title: `${svc.service} (${svc.node_type})`,
        onclick: () => switchToGraphFilteredByType(svc.node_type),
      },
        el("div", { class: "arch-tile-head" },
          el("iconify-icon", { icon: AWS_ICON[cat.category] || "simple-icons:amazonaws", width: "16", height: "16" }),
          el("span", { class: "svc" }, svc.service),
          el("span", { class: "ct" }, String(svc.count)),
        ),
        el("div", { class: "arch-tile-meta" }, svc.node_type),
        el("div", { class: "sample" }, svc.sample_label || svc.sample_id),
      );
      grid.appendChild(tile);
    }
    block.appendChild(grid);
    host.appendChild(block);
  }
}

function switchToGraphFilteredByType(nodeType) {
  // Switch to graph tab and filter by AWS category + search by type.
  switchTab("graph");
  STATE.activeCategories = new Set(["aws"]);
  STATE.search = nodeType.toLowerCase();
  $("#search").value = nodeType.toLowerCase();
  renderChips();
  renderGraph();
}

function switchToGraphFilteredByLayer(layerName) {
  switchTab("graph");
  STATE.activeCategories = new Set([layerToCategory(layerName)]);
  STATE.search = "";
  $("#search").value = "";
  renderChips();
  renderGraph();
}

function focusGraphOnNode(nodeId) {
  switchTab("graph");
  // Make sure the node's category is on.
  const node = STATE.byId.get(nodeId);
  if (node && node.category) STATE.activeCategories.add(node.category);
  STATE.search = "";
  $("#search").value = "";
  renderChips();
  renderGraph();
  setTimeout(() => {
    if (node && STATE.Graph3D) onNodeClick(node);
  }, 600);
}

// ---- Graph (3D) --------------------------------------------------------
function ensureForceGraph() {
  if (STATE.Graph3D) return STATE.Graph3D;
  if (typeof ForceGraph3D !== "function") return null;
  const host = $("#graph-3d");
  const initW = host.clientWidth || window.innerWidth || 1024;
  const initH = host.clientHeight || window.innerHeight || 768;
  STATE.Graph3D = ForceGraph3D({ controlType: "orbit" })(host)
    .backgroundColor("#090b0d")
    .width(initW)
    .height(initH)
    .nodeId("id")
    .nodeLabel(n => `<div style="font-family:Geist,system-ui,sans-serif;font-size:12px;padding:6px 8px;background:rgba(20,24,30,0.95);border:1px solid rgba(255,255,255,0.18);border-radius:6px;color:#fff;">${escapeHtml(n.label || n.id)}<br><span style="opacity:0.6;font-family:JetBrains Mono,ui-monospace,monospace;font-size:10px;">${escapeHtml(n.type || "")} · ${escapeHtml(n.layer || "")}</span></div>`)
    .nodeRelSize(5)
    .nodeOpacity(1.0)
    .nodeColor(nodeColorFn)
    .nodeResolution(12)
    .linkColor(() => "rgba(255,255,255,0.10)")
    .linkOpacity(0.7)
    .linkWidth(1.0)
    .linkDirectionalParticles(1)
    .linkDirectionalParticleSpeed(0.005)
    .linkDirectionalParticleWidth(2.0)
    .enableNodeDrag(true)
    .cooldownTime(15000)
    .warmupTicks(60)
    .onNodeClick(onNodeClick)
    .onBackgroundClick(() => closeSidebar());

  if (STATE.Graph3D.d3Force) {
    const c = STATE.Graph3D.d3Force("charge");
    if (c && c.strength) c.strength(-50);
    const l = STATE.Graph3D.d3Force("link");
    if (l && l.distance) l.distance(40);
  }

  window.addEventListener("resize", resyncGraphSize);
  if (typeof ResizeObserver === "function") {
    const ro = new ResizeObserver(() => resyncGraphSize());
    ro.observe(host);
  }
  return STATE.Graph3D;
}

function resyncGraphSize() {
  if (!STATE.Graph3D) return;
  const host = $("#graph-3d");
  if (!host) return;
  const w = host.clientWidth;
  const h = host.clientHeight;
  if (w > 0 && h > 0) {
    STATE.Graph3D.width(w).height(h);
  }
}

function nodeColorFn(node) {
  if (STATE.search) {
    const q = STATE.search.toLowerCase();
    const hit = (node.id || "").toLowerCase().includes(q) ||
                (node.label || "").toLowerCase().includes(q);
    if (hit) return "#ffffff";
    return "rgba(255,255,255,0.10)";
  }
  if (STATE.groupBy === "community") {
    const idx = STATE.communities && STATE.communities.communities
      ? STATE.communities.communities[node.id] : undefined;
    return communityColor(idx);
  }
  if (STATE.groupBy === "layer") {
    return CATEGORY_COLORS[layerToCategory(node.layer || "")] || "#888";
  }
  if (STATE.groupBy === "type") {
    const palette = ["#1677ff", "#ff9900", "#ff5552", "#a259ff", "#3ddc84", "#ff4f9c", "#f5b800", "#9da3ad", "#c0c4cc"];
    let h = 0; const s = node.type || "";
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return palette[Math.abs(h) % palette.length];
  }
  return CATEGORY_COLORS[node.category] || "#888";
}

function visibleGraphData() {
  const nodes = STATE.graph.nodes.filter(n => STATE.activeCategories.has(n.category || "dependency"));
  const visIds = new Set(nodes.map(n => n.id));
  const edges = STATE.graph.edges.filter(e => visIds.has(e.source) && visIds.has(e.target))
                                 .map(e => ({ source: e.source, target: e.target, relation: e.relation }));
  return { nodes: nodes.map(n => ({ ...n })), links: edges };
}

function renderGraph() {
  const G = ensureForceGraph();
  if (!G) return;
  const data = visibleGraphData();
  G.graphData(data);
  G.nodeColor(nodeColorFn);
  if (G.d3ReheatSimulation) G.d3ReheatSimulation();
  $("#stats").textContent = `${data.nodes.length} nodes · ${data.links.length} edges`;
  const empty = data.nodes.length === 0;
  $("#graph-empty-hint").classList.toggle("hidden", !empty);
  renderCommunityLegend();
}

function renderChips() {
  const host = $("#graph-chips");
  host.innerHTML = "";
  for (const cat of CATEGORY_ORDER) {
    const list = STATE.byCategory.get(cat) || [];
    if (list.length === 0 && cat !== "aws") continue;
    const on = STATE.activeCategories.has(cat);
    const chip = el("span", {
      class: `chip ${on ? "on" : "off"}`,
      "data-cat": cat,
      onclick: () => {
        if (STATE.activeCategories.has(cat)) STATE.activeCategories.delete(cat);
        else STATE.activeCategories.add(cat);
        renderChips();
        renderGraph();
      },
    });
    const dot = el("span", { class: "dot" });
    dot.style.background = CATEGORY_COLORS[cat];
    chip.appendChild(dot);
    chip.appendChild(el("span", {}, CATEGORY_LABELS[cat] || cat.toUpperCase()));
    chip.appendChild(el("span", { class: "ct" }, String(list.length)));
    host.appendChild(chip);
  }
}

function renderCommunityLegend() {
  const host = $("#community-legend");
  if (STATE.groupBy !== "community" || !STATE.communities || !STATE.communities.sizes) {
    host.classList.add("hidden");
    return;
  }
  host.innerHTML = "";
  host.appendChild(el("div", { class: "cl-title" },
    `Communities · ${STATE.communities.community_count} groups · modularity ${(STATE.communities.modularity || 0).toFixed(3)}`));
  const top = STATE.communities.sizes.slice(0, 12);
  const list = el("div", { class: "cl-list" });
  for (const s of top) {
    list.appendChild(el("div", { class: "cl-row" },
      Object.assign(el("span", { class: "cl-dot" }), { style: { background: communityColor(s.community) } }) ?
        Object.assign(document.createElement("span"), { className: "cl-dot", style: `background: ${communityColor(s.community)}` }) :
        el("span", { class: "cl-dot" }),
      el("span", { class: "cl-label" }, `community ${s.community}`),
      el("span", { class: "cl-ct" }, `${s.size} nodes`),
    ));
  }
  // simpler implementation — re-build cleanly
  list.innerHTML = "";
  for (const s of top) {
    const dot = document.createElement("span");
    dot.className = "cl-dot";
    dot.style.background = communityColor(s.community);
    const row = el("div", { class: "cl-row" }, dot,
      el("span", { class: "cl-label" }, `c${s.community}`),
      el("span", { class: "cl-ct" }, `${s.size}`));
    list.appendChild(row);
  }
  host.appendChild(list);
  host.classList.remove("hidden");
}

// ---- Sidebar (node detail) --------------------------------------------
async function onNodeClick(node) {
  STATE.selected = node.id;
  openSidebar(node);
  if (STATE.Graph3D && node.x != null) {
    STATE.Graph3D.cameraPosition(
      { x: node.x, y: node.y, z: (node.z || 0) + 220 }, node, 800
    );
  }
}

function openSidebar(node) {
  const body = $("#sidebar-body");
  body.innerHTML = "";
  body.appendChild(el("h3", {}, node.label || node.id));
  body.appendChild(el("div", { class: "meta" }, `${node.type || "?"} · ${node.layer || "?"}`));

  const kv = el("div", { class: "kv" });
  for (const [k, v] of [["id", node.id], ["type", node.type], ["layer", node.layer], ["category", node.category]]) {
    kv.appendChild(el("span", { class: "k" }, k));
    kv.appendChild(el("span", { class: "v" }, String(v ?? "")));
  }
  body.appendChild(el("div", { class: "section-title" }, "metadata"));
  body.appendChild(kv);

  body.appendChild(el("div", { class: "section-title" }, "neighbors"));
  const ul = el("ul", { class: "neighbors" }, el("li", { class: "muted" }, "loading…"));
  body.appendChild(ul);

  body.appendChild(el("div", { class: "section-title" }, "neighbourhood"));
  const mhost = el("div", { class: "mermaid-host" }, "loading…");
  body.appendChild(mhost);

  $("#sidebar").classList.add("open");

  fetchJSON(`/api/v1/nodes/${encodeURIComponent(node.id)}/neighbors`).then(j => {
    ul.innerHTML = "";
    const all = [
      ...(j.incoming || []).map(x => ({ ...x, dir: "in", peer: x.source })),
      ...(j.outgoing || []).map(x => ({ ...x, dir: "out", peer: x.target })),
    ];
    if (!all.length) {
      ul.appendChild(el("li", { class: "muted" }, "no neighbors"));
    } else {
      for (const e of all.slice(0, 60)) {
        const arrow = e.dir === "in" ? "←" : "→";
        const li = el("li", {
          onclick: () => {
            const peer = STATE.byId.get(e.peer);
            if (peer) onNodeClick(peer);
          },
        },
          el("span", { class: "rel" }, `${arrow} ${e.relation || "rel"}`),
          el("span", {}, e.label || e.peer),
        );
        ul.appendChild(li);
      }
    }
    return j;
  }).then(async (j) => {
    if (!j) return;
    const id = node.id;
    const lines = ["graph LR"];
    const safe = (s) => `n${(s || "").replace(/[^a-zA-Z0-9_]/g, "_")}`;
    lines.push(`${safe(id)}([${(node.label || id).slice(0, 28)}])`);
    for (const e of (j.incoming || []).slice(0, 20)) {
      lines.push(`${safe(e.source)}([${(e.label || e.source).slice(0, 24)}]) -->|${e.relation || ""}| ${safe(id)}`);
    }
    for (const e of (j.outgoing || []).slice(0, 20)) {
      lines.push(`${safe(id)} -->|${e.relation || ""}| ${safe(e.target)}([${(e.label || e.target).slice(0, 24)}])`);
    }
    try {
      const { svg } = await mermaid.render(`m_${Date.now()}`, lines.join("\n"));
      mhost.innerHTML = svg;
    } catch (exc) {
      mhost.textContent = `mermaid render failed: ${exc.message}`;
    }
  }).catch(exc => {
    ul.innerHTML = `<li class="muted">neighbor fetch failed: ${escapeHtml(exc.message)}</li>`;
  });
}

function closeSidebar() {
  $("#sidebar").classList.remove("open");
  STATE.selected = null;
  if (STATE.Graph3D) STATE.Graph3D.nodeColor(nodeColorFn);
}

// ---- Stack Overview (meta-layer graph-of-graphs) -----------------------
function ensureStackGraph() {
  if (STATE.StackGraph) return STATE.StackGraph;
  if (typeof ForceGraph3D !== "function") return null;
  const host = $("#stack-canvas");
  const w = host.clientWidth || 800;
  const h = host.clientHeight || 600;
  // 2D-ish flat camera: still use 3d-force-graph for consistency, but flatten.
  STATE.StackGraph = ForceGraph3D({ controlType: "orbit" })(host)
    .backgroundColor("#090b0d")
    .width(w).height(h)
    .nodeId("id")
    .nodeLabel(n => `<div style="font-family:Geist,system-ui,sans-serif;font-size:12px;padding:6px 8px;background:rgba(20,24,30,0.95);border:1px solid rgba(255,255,255,0.18);border-radius:6px;color:#fff;">${escapeHtml(n.name)}<br><span style="opacity:0.7;font-family:JetBrains Mono,ui-monospace,monospace;font-size:10px;">${n.node_count}n · ${n.edge_count}e · ${escapeHtml(n.layer_type)}</span></div>`)
    .nodeRelSize(8)
    .nodeVal(n => Math.max(2, Math.sqrt(Math.max(1, n.node_count))))
    .nodeColor(n => LAYER_TYPE_COLORS[n.layer_type] || LAYER_TYPE_COLORS.unknown)
    .nodeOpacity(1.0)
    .linkColor(() => "rgba(255,255,255,0.20)")
    .linkOpacity(0.85)
    .linkWidth(1.5)
    .linkDirectionalArrowLength(5)
    .linkDirectionalArrowRelPos(0.95)
    .linkDirectionalParticles(1)
    .linkDirectionalParticleSpeed(0.006)
    .onNodeClick(n => switchToGraphFilteredByLayer(n.name));

  if (STATE.StackGraph.d3Force) {
    const c = STATE.StackGraph.d3Force("charge");
    if (c && c.strength) c.strength(-180);
    const l = STATE.StackGraph.d3Force("link");
    if (l && l.distance) l.distance(80);
  }
  if (typeof ResizeObserver === "function") {
    const ro = new ResizeObserver(() => {
      const w2 = host.clientWidth; const h2 = host.clientHeight;
      if (w2 && h2 && STATE.StackGraph) STATE.StackGraph.width(w2).height(h2);
    });
    ro.observe(host);
  }
  return STATE.StackGraph;
}

function renderStack() {
  const G = ensureStackGraph();
  if (!G) return;
  const meta = STATE.meta || { nodes: [], links: [] };
  if (!meta.nodes.length) {
    $("#stack-empty-hint").classList.remove("hidden");
    return;
  }
  $("#stack-empty-hint").classList.add("hidden");
  // Filter out summarized_by edges by default — they all point to meta and
  // crowd the layout. feeds_into is the actual layer-DAG.
  const links = (meta.links || []).filter(l => l.relation === "feeds_into");
  G.graphData({ nodes: meta.nodes.map(n => ({ ...n })), links });
  // Legend
  const legend = $("#stack-legend");
  legend.innerHTML = "";
  for (const [t, c] of Object.entries(LAYER_TYPE_COLORS)) {
    const dot = document.createElement("span");
    dot.className = "cl-dot";
    dot.style.background = c;
    legend.appendChild(el("span", { class: "stack-legend-item" }, dot, el("span", {}, t)));
  }
  setTimeout(() => {
    if (G.zoomToFit) G.zoomToFit(800, 60);
  }, 300);
}

// ---- Compliance --------------------------------------------------------
function renderCompliance() {
  if (!STATE.compliance) return;
  const c = STATE.compliance;
  $("#cmp-total").textContent = String(c.total || 0);
  $("#cmp-high").textContent = String(c.by_severity?.HIGH || 0);
  $("#cmp-medium").textContent = String(c.by_severity?.MEDIUM || 0);
  $("#cmp-low").textContent = String(c.by_severity?.LOW || 0);

  // Severity chips.
  const sevHost = $("#cmp-sev-chips");
  sevHost.innerHTML = "";
  const sevs = ["HIGH", "MEDIUM", "LOW"];
  for (const sev of sevs) {
    const ct = c.by_severity?.[sev] || 0;
    const on = STATE.cmpFilters.severity === sev;
    const chip = el("span", {
      class: `chip cmp-chip ${on ? "on" : "off"}`,
      onclick: () => {
        STATE.cmpFilters.severity = on ? null : sev;
        renderCompliance();
      },
    },
      el("span", { class: "dot", style: `background:${sevColor(sev)}` }),
      el("span", {}, sev),
      el("span", { class: "ct" }, String(ct)),
    );
    sevHost.appendChild(chip);
  }
  // Rule chips.
  const ruleHost = $("#cmp-rule-chips");
  ruleHost.innerHTML = "";
  const ruleEntries = Object.entries(c.by_rule || {}).sort((a, b) => b[1] - a[1]);
  for (const [rule, ct] of ruleEntries) {
    const on = STATE.cmpFilters.rule_id === rule;
    ruleHost.appendChild(el("span", {
      class: `chip cmp-chip ${on ? "on" : "off"}`,
      onclick: () => {
        STATE.cmpFilters.rule_id = on ? null : rule;
        renderCompliance();
      },
    },
      el("span", {}, rule),
      el("span", { class: "ct" }, String(ct)),
    ));
  }

  // Rows.
  let rows = c.findings || [];
  if (STATE.cmpFilters.severity) rows = rows.filter(r => r.severity === STATE.cmpFilters.severity);
  if (STATE.cmpFilters.rule_id) rows = rows.filter(r => r.rule_id === STATE.cmpFilters.rule_id);
  if (STATE.cmpFilters.sortBy) {
    const k = STATE.cmpFilters.sortBy;
    const dir = STATE.cmpFilters.sortDir;
    rows = rows.slice().sort((a, b) => {
      const av = (a[k] || ""); const bv = (b[k] || "");
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }

  const tbody = $("#cmp-tbody");
  tbody.innerHTML = "";
  if (!rows.length) {
    tbody.appendChild(el("tr", {}, el("td", { colspan: 6, class: "muted" }, "no findings match the current filters")));
    return;
  }
  for (const r of rows) {
    const tr = el("tr", {},
      el("td", { class: "mono" }, r.rule_id || ""),
      el("td", {}, el("span", { class: `sev-pill sev-${(r.severity || "").toLowerCase()}` }, r.severity || "")),
      el("td", { class: "mono" }, r.resource_type || ""),
      el("td", { class: "mono" },
        el("a", { href: "#", onclick: (ev) => { ev.preventDefault(); focusGraphOnNode(r.target_resource); } },
          (r.target_resource || "").length > 60 ? (r.target_resource.slice(0, 57) + "…") : (r.target_resource || ""))
      ),
      el("td", {}, r.description || ""),
      el("td", { class: "muted" }, r.recommendation || ""),
    );
    tbody.appendChild(tr);
  }
}

function sevColor(sev) {
  if (sev === "HIGH") return "#ff5552";
  if (sev === "MEDIUM") return "#f5b800";
  if (sev === "LOW") return "#3ddc84";
  return "#9da3ad";
}

function wireCompliance() {
  for (const th of $$("#cmp-table thead th")) {
    const k = th.getAttribute("data-sort");
    if (!k) continue;
    th.addEventListener("click", () => {
      if (STATE.cmpFilters.sortBy === k) STATE.cmpFilters.sortDir *= -1;
      else { STATE.cmpFilters.sortBy = k; STATE.cmpFilters.sortDir = 1; }
      renderCompliance();
    });
  }
}

// ---- Tabs --------------------------------------------------------------
function wireTabs() {
  for (const btn of $$(".tabs button")) {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  }
}

function switchTab(tab) {
  for (const btn of $$(".tabs button")) {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  }
  document.body.classList.remove("view-graph", "view-dashboard", "view-stack", "view-compliance");
  document.body.classList.add(`view-${tab}`);

  $("#view-graph").hidden = tab !== "graph";
  $("#view-stack").hidden = tab !== "stack";
  $("#view-compliance").hidden = tab !== "compliance";

  if (tab === "graph") {
    requestAnimationFrame(() => {
      const G = ensureForceGraph();
      if (G) {
        resyncGraphSize();
        renderGraph();
        setTimeout(() => {
          resyncGraphSize();
          if (G.refresh) G.refresh();
          if (G.zoomToFit) G.zoomToFit(800, 60);
        }, 200);
        setTimeout(() => G.zoomToFit(800, 60), 800);
      }
    });
  }
  if (tab === "stack") {
    requestAnimationFrame(() => {
      if (!STATE.meta) {
        loadMetaOverview().then(renderStack);
      } else {
        renderStack();
      }
    });
  }
  if (tab === "compliance") {
    if (!STATE.compliance) {
      loadCompliance();
    } else {
      renderCompliance();
    }
  }
}

// ---- Empty banner / refresh -------------------------------------------
function wireEmptyBanner() {
  $("#empty-copy")?.addEventListener("click", () => {
    navigator.clipboard?.writeText("kuberly-platform call regenerate_all").catch(() => {});
    $("#empty-copy").textContent = "copied";
    setTimeout(() => { $("#empty-copy").textContent = "copy"; }, 1500);
  });
  $("#empty-refresh")?.addEventListener("click", () => location.reload());
}

function wireSidebar() {
  $("#sidebar-close")?.addEventListener("click", closeSidebar);
}

function wireGraphControls() {
  $("#search").addEventListener("input", (e) => {
    STATE.search = e.target.value || "";
    if (STATE.Graph3D) STATE.Graph3D.nodeColor(nodeColorFn);
    $("#stats").textContent = `${STATE.graph.nodes.length} nodes · ${STATE.graph.edges.length} edges${STATE.search ? " · search: " + STATE.search : ""}`;
  });
  $("#graph-group-by").addEventListener("change", async (e) => {
    STATE.groupBy = e.target.value;
    if (STATE.groupBy === "community") {
      // Lazy-load communities for the *current* visible filter.
      // Use null/all-layers since active categories aren't a single layer.
      await loadCommunities();
      if (STATE.Graph3D) STATE.Graph3D.nodeColor(nodeColorFn);
      renderCommunityLegend();
    } else {
      renderCommunityLegend();
      if (STATE.Graph3D) STATE.Graph3D.nodeColor(nodeColorFn);
    }
  });
  $("#filters-reset").addEventListener("click", () => {
    STATE.activeCategories = new Set(CATEGORY_ORDER);
    STATE.search = "";
    $("#search").value = "";
    renderChips();
    renderGraph();
  });
  wireCompliance();
}
