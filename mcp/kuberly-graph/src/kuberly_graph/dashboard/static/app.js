// kuberly-graph dashboard SPA — vanilla JS, no build pipeline.
//
// Two tabs:
//   - dashboard: overlays strip + key facts + AWS architecture tiles
//   - graph: 3D force-directed (3d-force-graph) with filter chips, search,
//     click-to-detail panel that fetches /api/v1/nodes/<id>/neighbors and
//     renders a Mermaid neighbourhood diagram.
//
// All visual tokens come from style.css :root; this file does the data
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
  // Don't pull the full graph until the user opens the Graph tab — but we
  // also need it for the architecture-tile counts on the dashboard.
  await loadGraph();
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
  // Mirrors api._categorize — the front-end falls back when the backend
  // didn't ship a category (e.g. older nodes). For the overlays strip we
  // categorise by layer alone.
  const map = {
    code: "iac_files", static: "iac_files", iac: "iac_files",
    terragrunt: "iac_files",
    state: "tg_state", tg_state: "tg_state", tofu_state: "tg_state",
    k8s: "k8s_resources", kubernetes: "k8s_resources",
    docs: "docs", doc: "docs",
    cue: "cue", schema: "cue", cue_schema: "cue",
    ci_cd: "ci_cd", image_build: "ci_cd", github_actions: "ci_cd",
    applications: "applications", rendered: "applications", rendered_apps: "applications",
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
  // Best-effort derivation. Real values land in Phase 8F when AWS scanner
  // ships native fields; until then we mine what's already in the graph.
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
  // db / cache / public — leave em-dash; real wiring in Phase 8F.
}

function renderArchTiles() {
  const host = $("#arch-tiles");
  host.innerHTML = "";
  // Group AWS-shaped nodes by service. AWS-shaped = category "aws" OR
  // category "tg_state" with id matching ^aws_<svc>_.
  const aws = (STATE.byCategory.get("aws") || []);
  const tg  = (STATE.byCategory.get("tg_state") || []);
  const buckets = new Map();
  function bucketize(node, svc) {
    if (!buckets.has(svc)) buckets.set(svc, []);
    buckets.get(svc).push(node);
  }
  for (const n of aws) {
    const svc = (n.type || "").replace(/^aws_/, "").toUpperCase() || "AWS";
    bucketize(n, svc);
  }
  for (const n of tg) {
    const m = String(n.id || "").match(/(?:^|[:./])aws_([a-z0-9_]+?)(?:_|\.|$)/);
    if (m) bucketize(n, m[1].toUpperCase());
  }
  if (buckets.size === 0) {
    host.innerHTML = `<div class="arch-empty muted">No AWS scanner data yet. Phase 8F will populate this section with native AWS resources (VPC, EKS, RDS, S3, IAM, …) — for now, AWS-shaped resources are inferred from terraform state.</div>`;
    return;
  }
  // Render top tiles by count.
  const sorted = Array.from(buckets.entries()).sort((a, b) => b[1].length - a[1].length);
  for (const [svc, nodes] of sorted.slice(0, 18)) {
    const sample = nodes[0];
    const tile = el("div", {
      class: "arch-tile",
      onclick: () => {
        // Switch to graph tab filtered by these nodes' category.
        switchTab("graph");
        STATE.activeCategories.clear();
        STATE.activeCategories.add(sample.category || "tg_state");
        STATE.search = svc.toLowerCase();
        $("#search").value = svc.toLowerCase();
        renderChips();
        renderGraph();
      },
    },
      el("div", { class: "head" },
        el("span", { class: "svc" }, svc),
        el("span", { class: "ct" }, String(nodes.length)),
      ),
      el("div", { class: "sample" }, sample.label || sample.id),
    );
    host.appendChild(tile);
  }
}

// ---- Graph (3D) --------------------------------------------------------
function ensureForceGraph() {
  if (STATE.Graph3D) return STATE.Graph3D;
  if (typeof ForceGraph3D !== "function") return null;
  const host = $("#graph-3d");
  STATE.Graph3D = ForceGraph3D({ controlType: "orbit" })(host)
    .backgroundColor("#090b0d")
    .width(host.clientWidth)
    .height(host.clientHeight)
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

  window.addEventListener("resize", () => {
    if (!STATE.Graph3D) return;
    STATE.Graph3D.width(host.clientWidth).height(host.clientHeight);
  });
  return STATE.Graph3D;
}

function nodeColorFn(node) {
  if (STATE.search) {
    const q = STATE.search.toLowerCase();
    const hit = (node.id || "").toLowerCase().includes(q) ||
                (node.label || "").toLowerCase().includes(q);
    if (hit) return "#ffffff";
    return "rgba(255,255,255,0.10)";
  }
  if (STATE.groupBy === "layer") {
    return CATEGORY_COLORS[layerToCategory(node.layer || "")] || "#888";
  }
  if (STATE.groupBy === "type") {
    // hash type → palette
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

// ---- Sidebar (node detail) --------------------------------------------
async function onNodeClick(node) {
  STATE.selected = node.id;
  openSidebar(node);
  STATE.Graph3D.cameraPosition(
    { x: node.x, y: node.y, z: (node.z || 0) + 220 }, node, 800
  );
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

  // Async fill
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
    // Build a tiny mermaid neighbourhood from incoming+outgoing.
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
  document.body.classList.toggle("view-graph", tab === "graph");
  document.body.classList.toggle("view-dashboard", tab === "dashboard");
  $("#view-graph").hidden = tab !== "graph";

  if (tab === "graph") {
    // First time → init the force graph and zoom.
    setTimeout(() => {
      const G = ensureForceGraph();
      if (G) {
        renderGraph();
        setTimeout(() => G.zoomToFit(800, 60), 600);
      }
    }, 50);
  }
}

// ---- Empty banner / refresh -------------------------------------------
function wireEmptyBanner() {
  $("#empty-copy")?.addEventListener("click", () => {
    navigator.clipboard?.writeText("kuberly-graph call regenerate_all").catch(() => {});
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
  $("#graph-group-by").addEventListener("change", (e) => {
    STATE.groupBy = e.target.value;
    if (STATE.Graph3D) STATE.Graph3D.nodeColor(nodeColorFn);
  });
  $("#filters-reset").addEventListener("click", () => {
    STATE.activeCategories = new Set(CATEGORY_ORDER);
    STATE.search = "";
    $("#search").value = "";
    renderChips();
    renderGraph();
  });
}
