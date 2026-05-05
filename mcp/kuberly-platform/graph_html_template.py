# Split from kuberly_platform.py — string.Template source for `.kuberly/graph.html`.
# Placeholders: $NODES_JSON, $EDGES_JSON, $VERSION_CHIP
# Post-process: __DASHBOARD_JSON__ replaced with escaped JSON (see write_graph_html).

GRAPH_HTML_TEMPLATE_RAW = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>kuberly · stack intelligence</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.1/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  :root {
    --bg:        #090b0d;
    --bg-raised: #11151a;
    --bg-card:   #161b22;
    --bg-elev:   #1c222b;
    --ink:        #ffffff;
    --ink-soft:   rgba(255,255,255,0.85);
    --ink-mute:   rgba(255,255,255,0.65);
    --ink-faint:  rgba(255,255,255,0.45);
    --ink-line:   rgba(255,255,255,0.10);
    --ink-line-soft: rgba(255,255,255,0.06);
    --blue:       #1677ff;
    --blue-soft:  #3c89e8;
    --blue-deep:  #1554ad;
    --blue-glow:  rgba(22,119,255,0.22);
    --aws:        #ff9900;
    --aws-soft:   #ffb84d;
    --amber:      #d89614;
    --amber-warm: #f5b042;
    --blue-glow:  rgba(22,119,255,0.22);
    --radius:     14px;
    --radius-lg:  22px;
    --lift-modal:  0 30px 80px -30px rgba(0,0,0,0.6);
    --font-sans: "Geist", -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", system-ui, sans-serif;
    --font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    font-family: var(--font-sans);
    background: var(--bg);
    color: var(--ink);
  }
  body.view-dashboard { overflow-x: hidden; overflow-y: auto; min-height: 100%; }
  body.view-graph { overflow: hidden; }

  #topbar {
    position: fixed;
    top: 0; left: 0; right: 0;
    min-height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    padding: 10px 20px;
    background: rgba(15,20,25,0.88);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--ink-line);
    z-index: 20;
    font-size: 13px;
  }
  #topbar .brand { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  #topbar .logo { display: inline-flex; color: var(--ink); }
  #topbar .wordmark {
    font-weight: 600; font-size: 15px;
    letter-spacing: -0.02em; color: var(--ink);
  }
  #topbar .tagline {
    font-size: 11px; color: var(--ink-faint);
    max-width: 280px; line-height: 1.35;
  }
  #topbar .eyebrow {
    font-family: var(--font-mono);
    font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink-faint);
    padding: 2px 8px; border: 1px solid var(--ink-line); border-radius: 999px;
  }
  .tabs {
    display: inline-flex;
    gap: 4px;
    padding: 4px;
    background: rgba(255,255,255,0.04);
    border-radius: 999px;
    border: 1px solid var(--ink-line);
  }
  .tabs button {
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 500;
    padding: 8px 16px;
    border: none;
    border-radius: 999px;
    background: transparent;
    color: var(--ink-mute);
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }
  .tabs button:hover { color: var(--ink-soft); }
  .tabs button.active {
    background: rgba(22,119,255,0.22);
    color: var(--ink);
  }
  #graph-controls {
    display: none;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  body.view-graph #graph-controls { display: flex; }
  #search {
    background: rgba(255,255,255,0.04);
    color: var(--ink);
    border: 1px solid var(--ink-line);
    padding: 6px 10px;
    border-radius: var(--radius);
    font-size: 13px;
    width: 200px;
    outline: none;
  }
  #search:focus { border-color: var(--blue); }
  .layer-toggles { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .layer-toggle {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 10px; border-radius: 999px;
    font-family: var(--font-mono); font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.16em;
    color: var(--ink-soft);
    border: 1px solid var(--ink-line);
    cursor: pointer; user-select: none;
    transition: all 0.15s ease;
  }
  .layer-toggle.active { background: rgba(255,255,255,0.04); }
  .layer-toggle.inactive { opacity: 0.45; }
  .layer-toggle input { display: none; }
  .layer-toggle .dot { width: 6px; height: 6px; border-radius: 50%; }
  .layer-toggle[data-layer=static] .dot { background: var(--blue); }
  .layer-toggle[data-layer=state]  .dot { background: var(--aws); }
  .layer-toggle[data-layer=k8s]    .dot { background: var(--amber); }
  .layer-toggle[data-layer=docs]   .dot { background: var(--ink-mute); }
  #graph-view-mode {
    background: rgba(255,255,255,0.04);
    color: var(--ink);
    border: 1px solid var(--ink-line);
    padding: 6px 10px;
    border-radius: var(--radius);
    font-size: 13px;
    cursor: pointer;
    max-width: min(280px, 42vw);
  }
  #layout-badge {
    display: inline-flex;
    align-items: center;
    padding: 6px 12px;
    border-radius: var(--radius);
    border: 1px solid var(--ink-line);
    background: rgba(22,119,255,0.08);
    color: var(--blue-soft);
    font-family: var(--font-mono);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    white-space: nowrap;
    user-select: none;
  }
  #stats { color: var(--ink-mute); font-size: 12px; font-family: var(--font-mono); }

  #dashboard-wrap {
    display: none;
    padding: 76px 24px 56px;
    max-width: 1480px;
    margin: 0 auto;
  }
  body.view-dashboard #dashboard-wrap { display: block; }

  .hero {
    margin-bottom: 28px;
  }
  .hero h1 {
    font-size: 26px;
    font-weight: 600;
    letter-spacing: -0.03em;
    margin-bottom: 8px;
  }
  .hero .meta-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-mute);
    border: 1px solid var(--ink-line);
    background: rgba(255,255,255,0.03);
  }
  .chip strong { color: var(--ink-soft); font-weight: 500; }

  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 14px;
    margin-bottom: 28px;
  }
  .kpi {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius-lg);
    padding: 18px 20px;
    box-shadow: 0 20px 50px -40px var(--blue-glow);
  }
  .kpi .label {
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--ink-faint);
    margin-bottom: 8px;
  }
  .kpi .value {
    font-size: 28px;
    font-weight: 600;
    letter-spacing: -0.02em;
    line-height: 1.1;
  }
  .kpi .sub {
    margin-top: 8px;
    font-size: 12px;
    color: var(--ink-mute);
    line-height: 1.4;
  }

  .section {
    margin-bottom: 36px;
  }
  .section h2 {
    font-size: 14px;
    font-weight: 600;
    letter-spacing: -0.01em;
    margin-bottom: 14px;
    color: var(--ink-soft);
  }

  .coverage-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 8px;
  }
  .layer-legend {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    font-size: 12px;
    color: var(--ink-mute);
    font-family: var(--font-mono);
  }

  .env-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
  }
  .env-card {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 16px;
  }
  .env-card h3 {
    font-size: 15px;
    margin-bottom: 10px;
    font-weight: 600;
  }
  .env-card .grid-mini {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 12px;
    font-size: 12px;
    color: var(--ink-mute);
  }
  .env-card .grid-mini span { color: var(--ink-faint); }
  .drift-pill {
    margin-top: 10px;
    font-size: 11px;
    color: var(--amber-warm);
    font-family: var(--font-mono);
  }

  .critical-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .critical-list .item {
    background: var(--bg-elev);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 10px 14px;
    font-size: 12px;
    max-width: 100%;
  }
  .critical-list .item .name { font-weight: 500; color: var(--ink); word-break: break-all; }
  .critical-list .item .deg { font-family: var(--font-mono); font-size: 10px; color: var(--ink-faint); margin-top: 4px; }

  .drift-columns {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 16px;
  }
  .drift-box {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 16px;
    font-size: 12px;
  }
  .drift-box h4 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--ink-faint);
    margin-bottom: 10px;
    font-family: var(--font-mono);
  }
  .drift-box ul { list-style: none; max-height: 200px; overflow-y: auto; }
  .drift-box li { padding: 4px 0; border-bottom: 1px solid var(--ink-line-soft); color: var(--ink-mute); }
  .drift-box li:last-child { border-bottom: none; }

  .chain-block {
    background: rgba(255,255,255,0.02);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 12px 14px;
    margin-bottom: 8px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-mute);
    word-break: break-all;
    line-height: 1.5;
  }

  .blast-acc { margin-top: 12px; }
  .blast-acc details {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    margin-bottom: 8px;
    padding: 4px 12px;
  }
  .blast-acc summary {
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    padding: 10px 4px;
    color: var(--ink-soft);
  }
  .mermaid-wrap {
    padding: 12px 8px 20px;
    overflow-x: auto;
    background: var(--bg-raised);
    border-radius: var(--radius);
    margin: 8px 0;
  }
  /* Mermaid dark theme still paints a white SVG rect on some builds — keep on-brand. */
  .mermaid-wrap svg { background: transparent !important; max-width: 100%; height: auto; }
  .mermaid-fallback {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-mute);
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 280px;
    overflow: auto;
    padding: 8px;
    border: 1px dashed var(--ink-line);
    border-radius: var(--radius);
  }

  .table-wrap {
    overflow-x: auto;
    border: 1px solid var(--ink-line);
    border-radius: var(--radius-lg);
    background: var(--bg-card);
  }
  table.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  table.data-table th {
    text-align: left;
    padding: 10px 12px;
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ink-faint);
    background: rgba(255,255,255,0.03);
    border-bottom: 1px solid var(--ink-line);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
  }
  table.data-table th:hover { color: var(--blue-soft); }
  table.data-table td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--ink-line-soft);
    color: var(--ink-mute);
    vertical-align: top;
  }
  table.data-table tr:hover td { background: rgba(22,119,255,0.06); color: var(--ink-soft); }
  table.data-table .mono { font-family: var(--font-mono); font-size: 11px; word-break: break-all; }

  .spotlight {
    display: grid;
    grid-template-columns: 1fr 1.2fr;
    gap: 16px;
  }
  @media (max-width: 960px) {
    .spotlight { grid-template-columns: 1fr; }
  }
  .spotlight input {
    width: 100%;
    padding: 12px 14px;
    border-radius: var(--radius);
    border: 1px solid var(--ink-line);
    background: var(--bg-raised);
    color: var(--ink);
    font-size: 14px;
    margin-bottom: 12px;
  }
  .spotlight .detail {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius-lg);
    padding: 18px;
    font-size: 12px;
    min-height: 120px;
  }
  .spotlight .detail h4 { margin-bottom: 10px; font-size: 13px; color: var(--ink); }
  .spotlight .detail .neigh { margin-top: 12px; }
  .spotlight .detail .neigh h5 {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--ink-faint);
    margin: 8px 0 4px;
    font-family: var(--font-mono);
  }
  .spotlight .detail a {
    color: var(--blue-soft);
    cursor: pointer;
    word-break: break-all;
  }

  #graph-shell {
    display: none;
    position: fixed;
    top: 56px;
    left: 0; right: 0; bottom: 0;
    background: var(--bg);
  }
  body.view-graph #graph-shell { display: block; }
  /* 3D presentation: concentric graph stays 2D in Cytoscape; stage tilts in perspective. */
  #cy-3d-stage {
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    overflow: hidden;
    perspective: 1680px;
    perspective-origin: 50% 44%;
  }
  #cy-3d-float {
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    transform-style: preserve-3d;
    transform-origin: 50% 48%;
    will-change: transform;
    animation: kuberlyNeuralFloat 28s ease-in-out infinite;
  }
  @keyframes kuberlyNeuralFloat {
    0%, 100% {
      transform: rotateX(5deg) rotateY(-14deg) translateZ(0) translateY(0);
    }
    25% {
      transform: rotateX(11deg) rotateY(6deg) translateZ(36px) translateY(-10px);
    }
    50% {
      transform: rotateX(7deg) rotateY(18deg) translateZ(-14px) translateY(8px);
    }
    75% {
      transform: rotateX(12deg) rotateY(-8deg) translateZ(22px) translateY(-4px);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    #cy-3d-float {
      animation: none !important;
      transform: none !important;
    }
  }
  #cy {
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background-color: var(--bg);
    background-image: radial-gradient(circle, rgba(255,255,255,0.05) 1px, transparent 1.4px);
    background-size: 22px 22px;
    transform: translateZ(0);
    backface-visibility: hidden;
  }
  #sidebar {
    position: absolute;
    top: 16px; right: 16px; bottom: 16px;
    width: 320px;
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius-lg);
    transform: translateX(calc(100% + 32px));
    transition: transform 180ms ease-out;
    overflow-y: auto;
    padding: 24px;
    font-size: 13px;
    color: var(--ink);
    box-shadow: var(--lift-modal);
    z-index: 9;
  }
  #sidebar.open { transform: translateX(0); }
  #sidebar h2 {
    font-size: 13px; font-weight: 500;
    color: var(--ink); margin-bottom: 12px;
    word-break: break-all; line-height: 1.3;
    font-family: var(--font-mono);
  }
  #sidebar .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
  #sidebar .chip {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 8px; border-radius: 999px;
    font-family: var(--font-mono); font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.16em;
    color: var(--ink-mute);
    border: 1px solid var(--ink-line);
    background: rgba(255,255,255,0.04);
  }
  #sidebar .chip.layer-static { color: var(--blue); border-color: rgba(22,119,255,0.30); background: rgba(22,119,255,0.08); }
  #sidebar .chip.layer-state  { color: var(--aws); border-color: rgba(255,153,0,0.30); background: rgba(255,153,0,0.08); }
  #sidebar .chip.layer-k8s   { color: var(--amber-warm); border-color: rgba(245,176,66,0.30); background: rgba(245,176,66,0.08); }
  #sidebar .chip.layer-docs  { color: var(--ink-mute); border-color: var(--ink-line); background: rgba(255,255,255,0.04); }
  #sidebar h3 {
    font-family: var(--font-mono);
    font-size: 10px; font-weight: 500;
    text-transform: uppercase; letter-spacing: 0.18em;
    color: var(--ink-faint);
    margin: 14px 0 6px;
  }
  #sidebar details {
    background: rgba(255,255,255,0.02);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 8px 10px;
  }
  #sidebar details summary {
    cursor: pointer; font-size: 12px; color: var(--ink-mute);
    font-family: var(--font-mono);
  }
  #sidebar .attrs { font-family: var(--font-mono); font-size: 11px; line-height: 1.5; word-break: break-all; }
  #sidebar .attrs .k { color: var(--ink-faint); }
  #sidebar .attrs .v { color: var(--ink); }
  #sidebar .edges a {
    display: block; padding: 4px 6px; border-radius: 4px;
    color: var(--ink-soft); text-decoration: none; font-size: 12px;
    font-family: var(--font-mono);
    word-break: break-all;
  }
  #sidebar .edges a:hover { background: rgba(22,119,255,0.10); color: var(--blue); }
  #sidebar .edges .rel { color: var(--ink-faint); font-size: 10px; margin-left: 4px; }
  #sidebar .actions { display: flex; gap: 8px; margin-top: 14px; }
  #sidebar .btn {
    flex: 1;
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    padding: 8px 14px; border-radius: var(--radius);
    background: var(--blue); color: white; border: none;
    font-weight: 500; font-size: 13px;
    cursor: pointer; transition: background 0.15s ease;
  }
  #sidebar .btn:hover { background: var(--blue-soft); }
  #sidebar .btn:active { background: var(--blue-deep); }
  #sidebar .btn.ghost {
    background: transparent; color: var(--ink-soft);
    border: 1px solid var(--ink-line);
  }
  #sidebar .btn.ghost:hover { background: rgba(255,255,255,0.04); }
  #sidebar #close-btn {
    position: absolute; top: 12px; right: 12px;
    background: transparent; border: none; color: var(--ink-faint);
    cursor: pointer; font-size: 18px; padding: 4px 8px;
  }
  #sidebar #close-btn:hover { color: var(--ink); }
  .pulse { animation: pulse 0.9s ease-in-out 3; }
  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(22,119,255,0.6); }
    50%      { box-shadow: 0 0 0 6px rgba(22,119,255,0.0); }
  }
</style>
</head>
<body class="view-dashboard">

<div id="topbar">
  <div class="brand">
    <span class="logo">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M11.3647 2.92733C11.7021 2.73258 12.1173 2.73119 12.4559 2.92369L19.8781 7.14305C20.2213 7.33813 20.4333 7.70247 20.4333 8.09721V16.5758C20.4333 16.9679 20.224 17.3303 19.8844 17.5263L19.5582 17.7146V18.6654C19.5582 19.4476 19.3772 20.2041 19.0449 20.8836L21.1282 19.6809C22.2376 19.0404 22.9211 17.8568 22.9211 16.5758V8.09721C22.9211 6.80772 22.2286 5.61756 21.1076 4.98029L13.6854 0.760927C12.5793 0.132111 11.2228 0.136639 10.1208 0.772828L7.66167 2.19263C6.55236 2.83309 5.86899 4.01672 5.86899 5.29765V13.7891C5.86899 15.07 6.55236 16.2536 7.66167 16.8941L12.2536 19.5452L14.1436 18.4542V17.7638L8.90558 14.7396C8.56599 14.5435 8.3568 14.1812 8.3568 13.7891V5.29765C8.3568 4.90553 8.56599 4.54319 8.90558 4.34713L11.3647 2.92733Z" fill="currentColor"/>
        <path d="M11.6634 4.44474L9.82021 5.5089V6.25864L15.0519 9.23272C15.395 9.42781 15.607 9.79214 15.607 10.1869V18.6655C15.607 19.0576 15.3978 19.4199 15.0582 19.616L12.5307 21.0751C12.1911 21.2711 11.7727 21.2711 11.4332 21.075L4.07931 16.8293C3.73972 16.6332 3.53053 16.2709 3.53053 15.8788V7.38732C3.53053 6.9952 3.73972 6.63287 4.07931 6.43681L4.40558 6.24844V5.29767C4.40558 4.51538 4.58658 3.75886 4.91902 3.07933L2.83541 4.2823C1.72609 4.92277 1.04272 6.10639 1.04272 7.38732V15.8788C1.04272 17.1597 1.72609 18.3433 2.83541 18.9838L10.1893 23.2295C11.2985 23.87 12.6652 23.87 13.7745 23.2296L16.302 21.7706C17.4114 21.1301 18.0948 19.9464 18.0948 18.6655V10.1869C18.0948 8.8974 17.4023 7.70723 16.2813 7.06996L11.6634 4.44474Z" fill="currentColor"/>
      </svg>
    </span>
    <span class="wordmark">kuberly</span>
    <span class="eyebrow">$VERSION_CHIP</span>
    <span class="tagline">Terragrunt intelligence — drift, blast radius, and live overlays in one surface.</span>
  </div>
  <nav class="tabs" aria-label="Primary view">
    <button type="button" class="tab active" id="tab-dashboard" data-view="dashboard">Dashboard</button>
    <button type="button" class="tab" id="tab-graph" data-view="graph">Graph</button>
  </nav>
  <div id="graph-controls">
    <input id="search" type="text" placeholder="Search nodes…" autocomplete="off" />
    <div class="layer-toggles">
      <label class="layer-toggle active" data-layer="static"><input type="checkbox" data-layer="static" checked><span class="dot"></span>static</label>
      <label class="layer-toggle active" data-layer="state"><input type="checkbox" data-layer="state" checked><span class="dot"></span>state</label>
      <label class="layer-toggle inactive" data-layer="k8s"><input type="checkbox" data-layer="k8s"><span class="dot"></span>k8s</label>
      <label class="layer-toggle active" data-layer="docs"><input type="checkbox" data-layer="docs" checked><span class="dot"></span>docs</label>
    </div>
    <select id="graph-view-mode" title="Graph scope — start with overview on large stacks">
      <option value="overview">Overview (module deps)</option>
      <option value="full" selected>Full graph</option>
    </select>
    <span id="layout-badge" title="Layout: concentric rings in a slow 3D float (CSS perspective)">concentric · 3D</span>
    <span class="stats" id="stats"></span>
  </div>
</div>

<div id="dashboard-wrap"></div>

<div id="graph-shell">
  <div id="cy-3d-stage">
    <div id="cy-3d-float">
      <div id="cy"></div>
    </div>
  </div>
  <aside id="sidebar">
    <button id="close-btn" title="Close (ESC)">&times;</button>
    <div id="sidebar-body"></div>
  </aside>
</div>

<script type="application/json" id="kuberly-dashboard-json">__DASHBOARD_JSON__</script>

<script>
const NODES = $NODES_JSON;
const EDGES = $EDGES_JSON;
const DASHBOARD = JSON.parse(document.getElementById("kuberly-dashboard-json").textContent);

/* --- Dashboard render -------------------------------------------------- */
function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sortRows(rows, key, dir) {
  const mul = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    const va = a[key];
    const vb = b[key];
    if (va === vb) return 0;
    if (va === undefined || va === null) return 1;
    if (vb === undefined || vb === null) return -1;
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * mul;
    return String(va).localeCompare(String(vb)) * mul;
  });
}

function renderSortableTable(containerId, columns, rows, initialSort) {
  const host = document.getElementById(containerId);
  if (!host) return;
  let sortKey = initialSort.key;
  let sortDir = initialSort.dir || "asc";

  function draw() {
    const sorted = sortRows(rows, sortKey, sortDir);
    const ths = columns.map(c => {
      const active = c.key === sortKey ? ` style="color:var(--blue-soft)"` : "";
      return `<th data-k="$${esc(c.key)}"$${active}>$${esc(c.label)}$${c.key === sortKey ? (sortDir === "asc" ? " ▲" : " ▼") : ""}</th>`;
    }).join("");
    const trs = sorted.map(r => "<tr>" + columns.map(c => {
      let v = r[c.key];
      if (Array.isArray(v)) v = v.join(", ");
      if (v === true) v = "yes";
      if (v === false) v = "—";
      return `<td class="$${c.mono ? "mono" : ""}">$${esc(v)}</td>`;
    }).join("") + "</tr>").join("");
    host.innerHTML = `<div class="table-wrap"><table class="data-table"><thead><tr>$${ths}</tr></thead><tbody>$${trs}</tbody></table></div>`;
    host.querySelectorAll("th[data-k]").forEach(th => {
      th.addEventListener("click", () => {
        const k = th.getAttribute("data-k");
        if (k === sortKey) sortDir = sortDir === "asc" ? "desc" : "asc";
        else { sortKey = k; sortDir = "asc"; }
        draw();
      });
    });
  }
  draw();
}

function renderDashboard() {
  const root = document.getElementById("dashboard-wrap");
  const m = DASHBOARD.meta || {};
  const kpis = DASHBOARD.kpis || {};

  const kpiLabels = { modules: "Modules", components: "Components", applications: "Applications",
    k8s_pods: "K8s pods", drift: "Drift gaps", critical: "Top hub" };
  const kpiHtml = ["modules", "components", "applications", "k8s_pods", "drift", "critical"].map(k => {
    const x = kpis[k] || {};
    return `<div class="kpi"><div class="label">$${esc(kpiLabels[k] || k)}</div><div class="value">$${esc(x.value)}</div><div class="sub">$${esc(x.sub || "")}</div></div>`;
  }).join("");

  const cov = DASHBOARD.coverage || {};
  const layers = DASHBOARD.source_layers || {};
  const layerLegend = ["static", "state", "k8s", "docs"].map(L => `$${L}: <strong>$${layers[L] || 0}</strong>`).join(" · ");

  const envCards = (DASHBOARD.environments || []).map(e => {
    const dcomp = (e.drift_components || []).length;
    const dapp = (e.drift_apps || []).length;
    const drift = (dcomp + dapp) ? `<div class="drift-pill">Δ drift: $${dcomp} comp · $${dapp} app</div>` : "";
    return `<div class="env-card"><h3>$${esc(e.name)}</h3><div class="grid-mini">
      <div><span>Cluster</span><br/>$${esc(e.cluster_name || "—")}</div>
      <div><span>Region</span><br/>$${esc(e.region || "—")}</div>
      <div><span>Components</span><br/>$${e.components}</div>
      <div><span>Applications</span><br/>$${e.applications}</div>
      <div><span>Namespaces</span><br/>$${e.k8s_namespaces}</div>
      <div><span>Pods / Depl</span><br/>$${e.k8s_pods} / $${e.k8s_deployments}</div>
    </div>$${drift}</div>`;
  }).join("");

  const critical = (DASHBOARD.critical_nodes || []).slice(0, 12).map(c =>
    `<div class="item"><div class="name">$${esc(c.label)}</div><div class="deg">$${esc(c.type)} · in $${c.in_degree} · out $${c.out_degree}</div></div>`
  ).join("");

  function driftList(title, block) {
    const keys = Object.keys(block || {}).sort();
    if (!keys.length) return `<div class="drift-box"><h4>$${esc(title)}</h4><p style="color:var(--ink-faint)">None — environments align.</p></div>`;
    const lis = keys.map(env => `<li><strong>$${esc(env)}</strong> missing: $${esc((block[env] || []).join(", "))}</li>`).join("");
    return `<div class="drift-box"><h4>$${esc(title)}</h4><ul>$${lis}</ul></div>`;
  }

  const chains = (DASHBOARD.longest_chains || []).map(ch =>
    `<div class="chain-block">$${esc(ch.map(x => x.replace(/^module:/, "")).join(" → "))}</div>`
  ).join("") || `<p style="color:var(--ink-faint)">No multi-hop module chains detected.</p>`;

  const st = DASHBOARD.state || {};
  const stateChips = (st.top_resource_types || []).slice(0, 12).map(t =>
    `<span class="chip">$${esc(String(t.type))} <strong>$${t.count}</strong></span>`
  ).join("");
  const stateIntro = (!st.loaded)
    ? `<p style="color:var(--ink-faint)">No Terraform state in the graph yet. Produce <span class="mono">.kuberly/state_overlay_*.json</span> and re-run graph generate.</p>`
    : `<p class="layer-legend">$${st.layer_nodes} state-layer nodes · $${st.resource_nodes} resource vertices · `
      + `$${st.components_state_confirmed} components confirmed in state · $${st.components_state_only} state-only (no static sidecar)</p>`
      + `<div class="coverage-bar">$${stateChips}</div>`;

  const blasts = (DASHBOARD.blast_diagrams || []);
  const blastHtml = blasts.length
    ? `<div class="blast-acc" id="blast-acc-root"></div>`
    : `<p style="color:var(--ink-faint)">No blast diagrams (run generate after shared-infra nodes exist).</p>`;

  const irsa = (DASHBOARD.k8s && DASHBOARD.k8s.irsa_bindings) || [];
  const irsaRows = irsa.map(r => ({ env: r.env, ns: r.ns, sa: r.sa, role: r.role }));

  const nodeIndex = {};
  NODES.forEach(n => {
    if (n.data && n.data.compound) return;
    const id = n.data.id;
    nodeIndex[id] = { id, label: n.data.label || id, type: n.data.type || "", layer: n.data.source_layer || "" };
  });

  root.innerHTML = `
    <header class="hero">
      <h1>Stack intelligence</h1>
      <div class="meta-row">
        <span class="chip"><strong>$${m.node_count}</strong> nodes</span>
        <span class="chip"><strong>$${m.edge_count}</strong> edges</span>
        <span class="chip"><strong>$${m.env_count}</strong> envs</span>
        <span class="chip"><strong>$${m.module_count}</strong> modules</span>
        <span class="chip"><strong>$${(DASHBOARD.k8s && DASHBOARD.k8s.irsa_bindings && DASHBOARD.k8s.irsa_bindings.length) || 0}</strong> IRSA</span>
      </div>
    </header>
    <div class="kpi-grid">$${kpiHtml}</div>

    <section class="section">
      <h2>Coverage & overlays</h2>
      <div class="coverage-bar">
        <span class="chip">OpenSpec: <strong>$${cov.openspec_present ? cov.openspec_changes + " changes" : "not present"}</strong></span>
        <span class="chip">Docs overlay: <strong>$${(cov.docs_overlay && cov.docs_overlay.generated_at) || "—"}</strong></span>
        <span class="chip">State snapshots: <strong>$${(cov.state_overlay_envs || []).join(", ") || "—"}</strong></span>
        <span class="chip">Doc-linked modules: <strong>$${cov.modules_with_doc_mentions}/$${cov.modules_total}</strong></span>
      </div>
      <div class="layer-legend">$${layerLegend}</div>
    </section>

    <section class="section">
      <h2>Terraform state overlay</h2>
      $${stateIntro}
      <div id="tbl-state-env"></div>
    </section>

    <section class="section">
      <h2>Environments</h2>
      <div class="env-grid">$${envCards}</div>
    </section>

    <section class="section">
      <h2>Most depended-on nodes</h2>
      <div class="critical-list">$${critical}</div>
    </section>

    <section class="section">
      <h2>Cross-environment drift</h2>
      <div class="drift-columns">
        $${driftList("Components", DASHBOARD.drift && DASHBOARD.drift.components)}
        $${driftList("Applications", DASHBOARD.drift && DASHBOARD.drift.applications)}
      </div>
    </section>

    <section class="section">
      <h2>Longest Terragrunt dependency chains</h2>
      $${chains}
    </section>

    <section class="section">
      <h2>Shared-infra blast radius (Mermaid)</h2>
      $${blastHtml}
    </section>

    <section class="section">
      <h2>IRSA — ServiceAccount → IAM role</h2>
      <div id="tbl-irsa"></div>
    </section>

    <section class="section spotlight">
      <div>
        <h2>Node spotlight</h2>
        <input type="search" id="spotlight-q" placeholder="Filter by id or label…" autocomplete="off" />
        <div id="spotlight-pick" style="max-height:220px;overflow-y:auto;font-size:12px"></div>
      </div>
      <div class="detail" id="spotlight-detail"><h4>Neighborhood</h4><p style="color:var(--ink-faint)">Select a node to see inbound/outbound edges.</p></div>
    </section>

    <section class="section">
      <h2>Modules</h2>
      <div id="tbl-modules"></div>
    </section>
    <section class="section">
      <h2>Components (env × name)</h2>
      <div id="tbl-components"></div>
    </section>
    <section class="section">
      <h2>Applications (rollup by name)</h2>
      <div id="tbl-apps-roll"></div>
    </section>
    <section class="section">
      <h2>Applications (per env)</h2>
      <div id="tbl-apps"></div>
    </section>
  `;

  renderSortableTable("tbl-modules", [
    { key: "provider", label: "Provider" },
    { key: "name", label: "Module" },
    { key: "deps", label: "Deps" },
    { key: "dependents", label: "Dependents" },
    { key: "doc_mentions", label: "Docs" },
    { key: "envs", label: "Envs", mono: true },
  ], DASHBOARD.modules || [], { key: "name", dir: "asc" });

  renderSortableTable("tbl-components", [
    { key: "env", label: "Env" },
    { key: "name", label: "Component" },
    { key: "modules", label: "Modules", mono: true },
    { key: "cluster_target", label: "Cluster" },
    { key: "in_state", label: "In state" },
    { key: "resource_count", label: "Resources" },
    { key: "state_snapshot_at", label: "State snapshot", mono: true },
  ], (DASHBOARD.components || []).map(c => ({ ...c, modules: (c.modules || []).join(", "), in_state: c.in_state ? "yes" : "—" })), { key: "env", dir: "asc" });

  renderSortableTable("tbl-apps-roll", [
    { key: "name", label: "App" },
    { key: "envs", label: "Envs", mono: true },
    { key: "runtimes", label: "Runtimes", mono: true },
    { key: "modules_used", label: "Modules", mono: true },
    { key: "images", label: "Image repos", mono: true },
  ], (DASHBOARD.applications_rollup || []).map(r => ({
    ...r,
    envs: (r.envs || []).join(", "),
    runtimes: (r.runtimes || []).join(", "),
    modules_used: (r.modules_used || []).join(", "),
    images: (r.images || []).join(", "),
  })), { key: "name", dir: "asc" });

  renderSortableTable("tbl-apps", [
    { key: "env", label: "Env" },
    { key: "name", label: "App" },
    { key: "runtime", label: "Runtime" },
    { key: "namespace", label: "NS" },
    { key: "image", label: "Image", mono: true },
    { key: "modules_used", label: "Modules", mono: true },
  ], (DASHBOARD.applications || []).map(a => ({ ...a, modules_used: (a.modules_used || []).join(", ") })), { key: "env", dir: "asc" });

  const stRows = (st.by_env || []).map(r => ({
    env: r.env,
    snapshot_at: r.snapshot_at || "—",
    components: r.components,
    confirmed: r.static_confirmed_by_state,
    state_only: r.state_only_components,
    resources: r.resources,
  }));
  renderSortableTable("tbl-state-env", [
    { key: "env", label: "Env" },
    { key: "snapshot_at", label: "State snapshot", mono: true },
    { key: "components", label: "Components" },
    { key: "confirmed", label: "Static ∩ state" },
    { key: "state_only", label: "State-only" },
    { key: "resources", label: "Resource nodes" },
  ], stRows, { key: "env", dir: "asc" });

  renderSortableTable("tbl-irsa", [
    { key: "env", label: "Env" },
    { key: "ns", label: "Namespace" },
    { key: "sa", label: "ServiceAccount" },
    { key: "role", label: "IAM role", mono: true },
  ], irsaRows, { key: "env", dir: "asc" });

  const blastRoot = document.getElementById("blast-acc-root");
  if (blastRoot && blasts.length) {
    blasts.forEach(b => {
      const det = document.createElement("details");
      det.open = false;
      const sum = document.createElement("summary");
      sum.textContent = "shared-infra blast · " + b.env;
      det.appendChild(sum);
      const wrap = document.createElement("div");
      wrap.className = "mermaid-wrap";
      const pre = document.createElement("pre");
      pre.className = "mermaid";
      pre.textContent = b.source;
      wrap.appendChild(pre);
      det.appendChild(wrap);
      blastRoot.appendChild(det);
    });
  }

  try {
    mermaid.initialize({
      startOnLoad: false,
      theme: "dark",
      securityLevel: "loose",
      maxTextSize: 900000,
      themeVariables: { primaryColor: "#161b22", primaryTextColor: "#fff", lineColor: "#1677ff" },
    });
    if (blastRoot && blastRoot.querySelector(".mermaid")) {
      try {
        const p = mermaid.run({ querySelector: "#blast-acc-root .mermaid" });
        if (p && typeof p.then === "function") {
          p.catch((e) => console.warn("mermaid", e));
        }
      } catch (e2) {
        console.warn("mermaid run", e2);
      }
    }
  } catch (e) { console.warn("mermaid", e); }

  /* Spotlight */
  const spotQ = document.getElementById("spotlight-q");
  const spotPick = document.getElementById("spotlight-pick");
  const spotDet = document.getElementById("spotlight-detail");
  const allNodes = Object.values(nodeIndex);

  function spotlightDetail(id) {
    const inc = EDGES.filter(e => e.data.target === id);
    const out = EDGES.filter(e => e.data.source === id);
    const n = nodeIndex[id] || {};
    const inl = inc.map(e => `<div><a data-j="$${esc(e.data.source)}">$${esc(e.data.source)}</a> <span style="color:var(--ink-faint)">[$${esc(e.data.relation)}]</span></div>`).join("") || "<span style='color:var(--ink-faint)'>none</span>";
    const outl = out.map(e => `<div><a data-j="$${esc(e.data.target)}">$${esc(e.data.target)}</a> <span style="color:var(--ink-faint)">[$${esc(e.data.relation)}]</span></div>`).join("") || "<span style='color:var(--ink-faint)'>none</span>";
    spotDet.innerHTML = `<h4>$${esc(id)}</h4><div style="color:var(--ink-mute)">$${esc(n.label)} · $${esc(n.type)} · $${esc(n.layer)}</div>
      <div class="neigh"><h5>Inbound ($${inc.length})</h5>$${inl}</div>
      <div class="neigh"><h5>Outbound ($${out.length})</h5>$${outl}</div>`;
    spotDet.querySelectorAll("a[data-j]").forEach(a => {
      a.addEventListener("click", ev => { ev.preventDefault(); spotlightDetail(a.getAttribute("data-j")); });
    });
  }

  function filterSpotlight() {
    const q = (spotQ.value || "").trim().toLowerCase();
    const hit = !q ? allNodes.slice(0, 40) : allNodes.filter(n => n.id.toLowerCase().includes(q) || (n.label && n.label.toLowerCase().includes(q))).slice(0, 60);
    spotPick.innerHTML = hit.map(n => `<div style="padding:6px 0;border-bottom:1px solid var(--ink-line-soft);cursor:pointer" data-id="$${esc(n.id)}"><span class="mono">$${esc(n.id)}</span><br/><span style="color:var(--ink-faint)">$${esc(n.type)}</span></div>`).join("");
    spotPick.querySelectorAll("[data-id]").forEach(el => {
      el.addEventListener("click", () => spotlightDetail(el.getAttribute("data-id")));
    });
  }
  spotQ.addEventListener("input", filterSpotlight);
  filterSpotlight();

  /* Edge list for spotlight — cytoscape uses elements with data wrapper */
  if (!EDGES[0] || !EDGES[0].data) {
    /* normalize if ever bare */
  }
}

/* --- View switching & cytoscape (lazy) -------------------------------- */
let cy = null;

function setView(mode) {
  document.body.classList.toggle("view-dashboard", mode === "dashboard");
  document.body.classList.toggle("view-graph", mode === "graph");
  document.getElementById("tab-dashboard").classList.toggle("active", mode === "dashboard");
  document.getElementById("tab-graph").classList.toggle("active", mode === "graph");
  if (mode === "graph") {
    if (!cy) buildCy();
    requestAnimationFrame(() => { cy.resize(); cy.fit(undefined, 24); });
  }
}

document.getElementById("tab-dashboard").addEventListener("click", () => setView("dashboard"));
document.getElementById("tab-graph").addEventListener("click", () => setView("graph"));

function buildCy() {
  const _root = getComputedStyle(document.documentElement);
  function _v(name) { return _root.getPropertyValue(name).trim(); }
  const BRAND = {
    bg: _v("--bg"), ink: _v("--ink"),
    inkMute: "rgba(255,255,255,0.65)", inkFaint: "rgba(255,255,255,0.45)",
    inkLine: "rgba(255,255,255,0.10)", inkLineHi: "rgba(255,255,255,0.18)",
    blue: _v("--blue"), blueSoft: _v("--blue-soft"),
    aws: _v("--aws"), amber: _v("--amber"), amberWarm: _v("--amber-warm"),
  };
  const LAYER_COLORS = {
    static: BRAND.blue, state: BRAND.aws, k8s: BRAND.amber, docs: BRAND.inkMute,
  };

  /* Very large compound graphs: strip parent boxes so concentric stays responsive. */
  const leafNodes = NODES.filter(n => n.data && !n.data.compound);
  const leafCount = leafNodes.length;
  const STRIP_COMPOUND_THRESHOLD = 500;
  let graphNodes = NODES;
  let stripCompound = false;
  if (leafCount >= STRIP_COMPOUND_THRESHOLD) {
    stripCompound = true;
    graphNodes = leafNodes.map((n) => {
      const data = Object.assign({}, n.data);
      delete data.parent;
      return Object.assign({}, n, { data });
    });
  }

  function pickElementsForView(mode, nodes, edges) {
    if (mode !== "overview") return { nodes, edges };
    const mods = nodes.filter(n => n.data && !n.data.compound && n.data.type === "module");
    if (mods.length < 2) return { nodes, edges };
    const ids = new Set(mods.map(n => n.data.id));
    const outEdges = edges.filter(e => {
      const d = e.data || {};
      return d.relation === "depends_on" && ids.has(d.source) && ids.has(d.target);
    });
    return { nodes: mods, edges: outEdges };
  }

  const viewSel = document.getElementById("graph-view-mode");
  let viewMode = "full";
  if (viewSel) {
    try {
      const saved = sessionStorage.getItem("kuberlyGraphView");
      if (saved === "overview" || saved === "full") viewSel.value = saved;
      else if (leafCount >= 280) viewSel.value = "overview";
    } catch (e) { /* private mode */ }
    viewMode = viewSel.value || "full";
  }
  const picked = pickElementsForView(viewMode, graphNodes, EDGES);
  const elemsNodes = picked.nodes;
  const elemsEdges = picked.edges;
  const isOverview = viewMode === "overview" && elemsNodes.length < graphNodes.length;

  const dense = leafCount > 600;
  const concentricLayoutOpts = {
    name: "concentric",
    animate: false,
    fit: true,
    padding: isOverview ? 40 : (dense ? 20 : 28),
    spacingFactor: dense ? 1.42 : 1.18,
    minNodeSpacing: dense ? 6 : 12,
    startAngle: -Math.PI / 2,
    sweep: 2 * Math.PI,
    clockwise: true,
    concentric: n => n.degree(),
    levelWidth: () => 1,
  };
  const initialLayout = "concentric";

  const stParts = [];
  if (isOverview) stParts.push("overview");
  if (stripCompound && !isOverview) stParts.push("no compound parents");
  document.getElementById("stats").textContent =
    elemsNodes.filter(n => !n.data.compound).length + " nodes · " + elemsEdges.length + " edges"
    + (stParts.length ? " · " + stParts.join(" · ") : "");

  cy = cytoscape({
    container: document.getElementById("cy"),
    elements: { nodes: elemsNodes, edges: elemsEdges },
    wheelSensitivity: 0.2,
    style: [
      { selector: "node", style: {
        "label": "data(label)", "font-size": isOverview ? 11 : 9,
        "font-family": "Geist, -apple-system, BlinkMacSystemFont, system-ui, sans-serif",
        "color": BRAND.ink, "text-valign": "center", "text-halign": "center",
        "text-outline-color": BRAND.bg, "text-outline-width": 2,
        "background-color": "#999", "width": 18, "height": 18, "border-width": 0,
      }},
      { selector: "node.static", style: { "background-color": LAYER_COLORS.static } },
      { selector: "node.state",  style: { "background-color": LAYER_COLORS.state  } },
      { selector: "node.k8s",    style: { "background-color": LAYER_COLORS.k8s    } },
      { selector: "node.docs",   style: { "background-color": LAYER_COLORS.docs   } },
      { selector: "node:parent", style: {
        "background-color": "rgba(255,255,255,0.04)", "background-opacity": 1,
        "border-color": BRAND.inkLine, "border-width": 1, "shape": "round-rectangle",
        "label": "data(label)", "text-valign": "top", "text-halign": "center",
        "font-size": 10, "font-family": "Geist, system-ui, sans-serif",
        "color": BRAND.inkFaint, "padding": 14, "min-zoomed-font-size": 8,
      }},
      { selector: "node:parent.env", style: {
        "border-color": BRAND.inkLineHi, "background-color": "rgba(255,255,255,0.02)",
        "font-size": 12, "color": BRAND.ink,
      }},
      { selector: "node.k8s.layer-off", style: { "display": "none" } },
      { selector: "node.static.layer-off", style: { "display": "none" } },
      { selector: "node.state.layer-off", style: { "display": "none" } },
      { selector: "node.docs.layer-off", style: { "display": "none" } },
      { selector: "edge", style: {
        "width": 1.2, "line-color": BRAND.inkLine, "target-arrow-color": BRAND.inkLine,
        "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.7, "opacity": 0.7,
      }},
      { selector: "edge.dim", style: { "opacity": 0.08 } },
      { selector: "node.dim", style: { "opacity": 0.15 } },
      { selector: "node:selected", style: { "border-width": 3, "border-color": BRAND.blue, "background-color": "data(color)" } },
      { selector: "node.match", style: { "border-width": 2, "border-color": BRAND.blue } },
      { selector: "node.upstream", style: { "border-width": 3, "border-color": BRAND.aws, "background-color": BRAND.aws } },
      { selector: "node.downstream", style: { "border-width": 3, "border-color": BRAND.blue } },
      { selector: "edge.highlight", style: { "line-color": BRAND.blue, "target-arrow-color": BRAND.blue, "opacity": 1, "width": 2 } },
    ],
    layout: concentricLayoutOpts,
  });

  function applyLayerVisibility(layer, on) {
    cy.batch(() => {
      cy.nodes("." + layer).forEach(n => {
        if (on) n.removeClass("layer-off");
        else n.addClass("layer-off");
      });
    });
  }
  applyLayerVisibility("k8s", false);

  const runLayoutImpl = (_name) => {
    cy.layout({ ...concentricLayoutOpts, animate: false, fit: true }).run();
  };
  window.__kuberlyRunLayout = runLayoutImpl;
  const searchEl = document.getElementById("search");
  const sidebar = document.getElementById("sidebar");
  const sidebarBody = document.getElementById("sidebar-body");

  if (!window.__kuberlyGraphUiWired) {
    window.__kuberlyGraphUiWired = true;
    document.querySelectorAll("#graph-controls .layer-toggles input").forEach(cb => {
      cb.addEventListener("change", () => {
        applyLayerVisibility(cb.dataset.layer, cb.checked);
        const pill = cb.closest(".layer-toggle");
        if (pill) {
          pill.classList.toggle("active", cb.checked);
          pill.classList.toggle("inactive", !cb.checked);
        }
      });
    });
    if (viewSel) {
      viewSel.addEventListener("change", () => {
        try { sessionStorage.setItem("kuberlyGraphView", viewSel.value); } catch (e) {}
        if (cy) { cy.destroy(); cy = null; }
        buildCy();
        requestAnimationFrame(() => { if (cy) { cy.resize(); cy.fit(undefined, 24); } });
      });
    }
    searchEl.addEventListener("input", () => {
      const q = searchEl.value.trim().toLowerCase();
      cy.nodes().removeClass("match pulse");
      if (!q) return;
      cy.nodes().filter(n => {
        if (n.data("compound")) return false;
        const id = (n.id() || "").toLowerCase();
        const lbl = (n.data("label") || "").toLowerCase();
        return id.includes(q) || lbl.includes(q);
      }).addClass("match pulse");
    });
    searchEl.addEventListener("keydown", e => {
      if (e.key !== "Enter") return;
      const first = cy.nodes(".match").first();
      if (first && first.length) cy.animate({ center: { eles: first }, zoom: 1.3 }, { duration: 250 });
    });
    document.getElementById("close-btn").addEventListener("click", () => {
      if (!cy) return;
      sidebar.classList.remove("open");
      cy.nodes().unselect();
      clearBlast();
    });
    document.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      if (!document.body.classList.contains("view-graph")) return;
      if (!cy) return;
      sidebar.classList.remove("open");
      cy.nodes().unselect();
      clearBlast();
      cy.nodes().removeClass("match pulse");
      searchEl.value = "";
    });
  }
  runLayoutImpl(initialLayout);

  function renderSidebar(node) {
    const data = node.data();
    const layer = data.source_layer || "static";
    const incoming = cy.edges(`[target = "$${data.id}"]`);
    const outgoing = cy.edges(`[source = "$${data.id}"]`);
    const attrs = data.attrs || {};
    const attrEntries = Object.entries(attrs).filter(([k]) => k !== "label" && k !== "id");
    let attrHtml = "";
    if (attrEntries.length) {
      attrHtml = `<details $${attrEntries.length <= 4 ? "open" : ""}><summary>$${attrEntries.length} attribute$${attrEntries.length === 1 ? "" : "s"}</summary><div class="attrs">` +
        attrEntries.map(([k, v]) => {
          const vs = typeof v === "object" ? JSON.stringify(v) : String(v);
          return `<div><span class="k">$${k}:</span> <span class="v">$${escapeHtml(vs)}</span></div>`;
        }).join("") + `</div></details>`;
    }
    const inHtml = incoming.map(e => {
      const src = e.source().id();
      const rel = e.data("relation") || "";
      return `<a href="#" data-jump="$${src}">$${escapeHtml(src)}<span class="rel">[$${escapeHtml(rel)}]</span></a>`;
    }).join("") || `<div class="rel">none</div>`;
    const outHtml = outgoing.map(e => {
      const tgt = e.target().id();
      const rel = e.data("relation") || "";
      return `<a href="#" data-jump="$${tgt}">$${escapeHtml(tgt)}<span class="rel">[$${escapeHtml(rel)}]</span></a>`;
    }).join("") || `<div class="rel">none</div>`;
    sidebarBody.innerHTML = `
      <h2>$${escapeHtml(data.id)}</h2>
      <div class="chips">
        $${data.type ? `<span class="chip">$${escapeHtml(data.type)}</span>` : ""}
        <span class="chip layer-$${layer}">$${layer}</span>
      </div>
      $${attrHtml}
      <h3>Incoming ($${incoming.length})</h3>
      <div class="edges">$${inHtml}</div>
      <h3>Outgoing ($${outgoing.length})</h3>
      <div class="edges">$${outHtml}</div>
      <div class="actions">
        <button id="blast-btn" class="btn">Show blast radius</button>
        <button id="center-btn" class="btn ghost">Center</button>
      </div>
    `;
    sidebar.classList.add("open");
    sidebarBody.querySelectorAll("a[data-jump]").forEach(a => {
      a.addEventListener("click", ev => {
        ev.preventDefault();
        const target = cy.getElementById(a.dataset.jump);
        if (target && target.length) {
          cy.nodes().unselect();
          target.select();
          cy.animate({ center: { eles: target }, zoom: 1.3 }, { duration: 250 });
          renderSidebar(target);
        }
      });
    });
    document.getElementById("blast-btn").addEventListener("click", () => showBlast(node));
    document.getElementById("center-btn").addEventListener("click", () => {
      cy.animate({ center: { eles: node }, zoom: 1.3 }, { duration: 250 });
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function showBlast(node) {
    cy.elements().addClass("dim");
    const upstream = node.predecessors("node");
    const downstream = node.successors("node");
    upstream.removeClass("dim").addClass("upstream");
    downstream.removeClass("dim").addClass("downstream");
    node.removeClass("dim");
    node.predecessors("edge").removeClass("dim").addClass("highlight");
    node.successors("edge").removeClass("dim").addClass("highlight");
  }
  function clearBlast() {
    cy.elements().removeClass("dim upstream downstream highlight");
  }

  cy.on("tap", "node", evt => {
    const n = evt.target;
    if (n.data("compound")) {
      const kids = n.children();
      if (kids.first().style("display") === "none") kids.style("display", "element");
      else kids.style("display", "none");
      return;
    }
    clearBlast();
    renderSidebar(n);
  });
  cy.on("tap", evt => {
    if (evt.target === cy) {
      sidebar.classList.remove("open");
      cy.nodes().unselect();
      clearBlast();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  renderDashboard();
});
</script>
</body>
</html>
"""
