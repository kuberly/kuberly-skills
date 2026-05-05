# Split from kuberly_platform.py — string.Template source for `.kuberly/graph.html`.
# Placeholders: $NODES_JSON, $EDGES_JSON, $VERSION_CHIP
# Post-process: __DASHBOARD_JSON__ replaced with escaped JSON (see write_graph_html).

GRAPH_HTML_TEMPLATE_RAW = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>kuberly · stack intelligence</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%3E%3Cpath%20d%3D%22M11.3647%202.92733C11.7021%202.73258%2012.1173%202.73119%2012.4559%202.92369L19.8781%207.14305C20.2213%207.33813%2020.4333%207.70247%2020.4333%208.09721V16.5758C20.4333%2016.9679%2020.224%2017.3303%2019.8844%2017.5263L19.5582%2017.7146V18.6654C19.5582%2019.4476%2019.3772%2020.2041%2019.0449%2020.8836L21.1282%2019.6809C22.2376%2019.0404%2022.9211%2017.8568%2022.9211%2016.5758V8.09721C22.9211%206.80772%2022.2286%205.61756%2021.1076%204.98029L13.6854%200.760927C12.5793%200.132111%2011.2228%200.136639%2010.1208%200.772828L7.66167%202.19263C6.55236%202.83309%205.86899%204.01672%205.86899%205.29765V13.7891C5.86899%2015.07%206.55236%2016.2536%207.66167%2016.8941L12.2536%2019.5452L14.1436%2018.4542V17.7638L8.90558%2014.7396C8.56599%2014.5435%208.3568%2014.1812%208.3568%2013.7891V5.29765C8.3568%204.90553%208.56599%204.54319%208.90558%204.34713L11.3647%202.92733Z%22%20fill%3D%22%231677ff%22%2F%3E%3Cpath%20d%3D%22M11.6634%204.44474L9.82021%205.5089V6.25864L15.0519%209.23272C15.395%209.42781%2015.607%209.79214%2015.607%2010.1869V18.6655C15.607%2019.0576%2015.3978%2019.4199%2015.0582%2019.616L12.5307%2021.0751C12.1911%2021.2711%2011.7727%2021.2711%2011.4332%2021.075L4.07931%2016.8293C3.73972%2016.6332%203.53053%2016.2709%203.53053%2015.8788V7.38732C3.53053%206.9952%203.73972%206.63287%204.07931%206.43681L4.40558%206.24844V5.29767C4.40558%204.51538%204.58658%203.75886%204.91902%203.07933L2.83541%204.2823C1.72609%204.92277%201.04272%206.10639%201.04272%207.38732V15.8788C1.04272%2017.1597%201.72609%2018.3433%202.83541%2018.9838L10.1893%2023.2295C11.2985%2023.87%2012.6652%2023.87%2013.7745%2023.2296L16.302%2021.7706C17.4114%2021.1301%2018.0948%2019.9464%2018.0948%2018.6655V10.1869C18.0948%208.8974%2017.4023%207.70723%2016.2813%207.06996L11.6634%204.44474Z%22%20fill%3D%22%231677ff%22%2F%3E%3C%2Fsvg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/3d-force-graph@1.73.0/dist/3d-force-graph.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<script src="https://code.iconify.design/iconify-icon/2.1.0/iconify-icon.min.js"></script>
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
    --k8s-red:    #e44d4d;
    --k8s-red-soft: #ff7676;
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
  .layer-toggle[data-layer=k8s]    .dot { background: var(--k8s-red); }
  .layer-toggle[data-layer=docs]   .dot { background: var(--ink-mute); }
  #graph-view-mode,
  #graph-group-by {
    background: rgba(255,255,255,0.04);
    color: var(--ink);
    border: 1px solid var(--ink-line);
    padding: 6px 10px;
    border-radius: var(--radius);
    font-size: 13px;
    cursor: pointer;
    max-width: min(280px, 42vw);
  }
  .filters-toggle, .filters-reset {
    background: rgba(255,255,255,0.04);
    color: var(--ink);
    border: 1px solid var(--ink-line);
    padding: 6px 12px;
    border-radius: var(--radius);
    font-size: 13px;
    cursor: pointer;
    font-family: inherit;
  }
  .filters-toggle:hover, .filters-reset:hover { border-color: var(--blue-soft); }
  .filters-toggle[aria-expanded=true] {
    background: rgba(22,119,255,0.16);
    border-color: var(--blue);
    color: #fff;
  }
  /* Filter panel: drops down below the actual topbar bottom edge. The
     `top` value is set in JS at open time (topbar.getBoundingClientRect().bottom)
     because the topbar is `flex-wrap: wrap` — when controls wrap to a
     second row the topbar grows past 56px and a hardcoded top offset
     would hide the wrapped controls underneath this panel. */
  #graph-filter-panel {
    position: fixed;
    top: 56px; left: 0; right: 0;
    z-index: 9;
    background: rgba(11,14,18,0.96);
    border-bottom: 1px solid var(--ink-line);
    padding: 14px 20px 16px;
    display: none;
    grid-template-columns: 0.6fr 1fr 1fr 0.6fr;
    gap: 18px;
    backdrop-filter: blur(6px);
  }
  #graph-filter-panel:not([hidden]) { display: grid; }
  body:not(.view-graph) #graph-filter-panel { display: none !important; }
  #graph-filter-panel .fp-close {
    position: absolute;
    top: 6px;
    right: 8px;
    width: 28px; height: 28px;
    border: 1px solid var(--ink-line);
    background: rgba(255,255,255,0.04);
    color: var(--ink);
    border-radius: 50%;
    cursor: pointer;
    font-size: 16px;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  #graph-filter-panel .fp-close:hover {
    border-color: var(--blue-soft);
    background: rgba(22,119,255,0.12);
  }
  .fp-label {
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--ink-faint);
    margin-bottom: 6px;
  }
  .fp-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .fp-multi {
    max-height: 96px;
    overflow-y: auto;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 4px;
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    background: rgba(255,255,255,0.02);
  }
  .fp-chip {
    user-select: none;
    cursor: pointer;
    background: rgba(255,255,255,0.04);
    color: var(--ink-mute);
    border: 1px solid var(--ink-line);
    border-radius: 999px;
    padding: 2px 10px;
    font-size: 11px;
    font-family: var(--font-mono);
  }
  .fp-chip:hover { color: var(--ink); }
  .fp-chip.on {
    background: rgba(22,119,255,0.16);
    border-color: var(--blue);
    color: #fff;
  }
  .fp-chip .ct {
    color: var(--ink-faint);
    margin-left: 5px;
    font-size: 10px;
  }
  .fp-chip.on .ct { color: #cfe2ff; }
  @media (max-width: 1100px) {
    #graph-filter-panel { grid-template-columns: 1fr 1fr; }
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
  .kpi.kpi-warn { border-left: 3px solid #f5b042; }
  .kpi.kpi-ok   { border-left: 3px solid #5fd098; }
  .kpi.kpi-blue { border-left: 3px solid var(--blue-soft); }
  .kpi.kpi-warn .value { color: #f5d9a0; }
  .kpi.kpi-ok   .value { color: #b6e8c8; }

  /* v0.35.0 dashboard sections — Findings / Module age / App→Secret / Network */
  .findings-tier {
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 10px 14px 12px;
    background: var(--bg-card);
    margin-bottom: 10px;
  }
  .findings-tier h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--ink-faint);
    margin-bottom: 8px;
  }
  .findings-tier.high   { border-left: 3px solid #ff5e5e; }
  .findings-tier.medium { border-left: 3px solid #f5b042; }
  .findings-tier.low    { border-left: 3px solid var(--ink-mute); }
  .finding-row {
    display: grid;
    grid-template-columns: 200px 1fr 200px;
    gap: 10px;
    padding: 4px 0;
    border-bottom: 1px solid var(--ink-line-soft);
    font-size: 12px;
    color: var(--ink);
  }
  .finding-row:last-child { border-bottom: none; }
  .finding-row .rule { font-family: var(--font-mono); color: #ff8b8b; font-size: 11px; }
  .findings-tier.medium .finding-row .rule { color: #f5b042; }
  .findings-tier.low    .finding-row .rule { color: var(--ink-mute); }
  .finding-row .where {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--ink-faint);
    text-align: right;
  }

  /* Module age heatmap */
  .age-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 6px;
  }
  .age-cell {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 8px 10px;
    font-size: 11px;
  }
  .age-cell .name {
    font-family: var(--font-mono);
    color: var(--ink);
    font-weight: 600;
  }
  .age-cell .meta {
    color: var(--ink-faint);
    font-family: var(--font-mono);
    font-size: 10px;
    margin-top: 3px;
  }
  .age-cell.fresh { border-left: 3px solid #5fd098; }
  .age-cell.warm  { border-left: 3px solid #f5b042; }
  .age-cell.cold  { border-left: 3px solid #ff8b3d; }
  .age-cell.frozen{ border-left: 3px solid var(--ink-mute); }

  /* App → Secret → IAM */
  .asi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 10px;
  }
  .asi-card {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 10px 14px 12px;
  }
  .asi-card .head {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--ink);
    font-weight: 600;
    margin-bottom: 4px;
    word-break: break-all;
  }
  .asi-card .meta {
    color: var(--ink-mute);
    font-size: 11px;
    font-family: var(--font-mono);
    margin-bottom: 6px;
  }
  .asi-card .role {
    color: var(--blue-soft);
    font-size: 11px;
    font-family: var(--font-mono);
    word-break: break-all;
  }
  .asi-card .pol {
    color: var(--ink-faint);
    font-size: 10px;
    font-family: var(--font-mono);
    margin-top: 4px;
  }

  /* Network reachability table */
  .net-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 10px;
  }
  .net-card {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 10px 14px 12px;
  }
  .net-card.world { border-left: 3px solid #ff5e5e; }
  .net-card .name {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--ink);
    font-weight: 600;
  }
  .net-card .desc {
    color: var(--ink-mute);
    font-size: 11px;
    margin-top: 2px;
  }
  .net-card .row {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 11px;
    font-family: var(--font-mono);
    padding: 3px 0;
    border-bottom: 1px solid var(--ink-line-soft);
  }
  .net-card .row:last-child { border-bottom: none; }
  .net-card .dir {
    text-transform: uppercase;
    font-size: 9px;
    letter-spacing: 0.1em;
    color: var(--ink-faint);
    width: 50px;
  }
  .net-card .src.world { color: #ff8b8b; }

  /* v0.34.0: category cards (Compute / Data / Identity / Networking / ...) */
  .cat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 14px;
    margin-bottom: 28px;
  }
  .cat-card {
    position: relative;
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius-lg);
    padding: 16px 18px;
    transition: border-color 140ms ease, box-shadow 140ms ease;
    overflow: hidden;
  }
  .cat-card::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0; height: 3px;
    background: var(--cat-color, var(--blue));
    opacity: 0.85;
  }
  .cat-card:hover { border-color: var(--cat-color, var(--blue-soft)); }
  .cat-card[data-open="true"] {
    border-color: var(--cat-color, var(--blue-soft));
    box-shadow: 0 28px 60px -38px rgba(0,0,0,0.6),
                0 0 0 1px var(--cat-color, var(--blue-soft));
  }
  .cat-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    cursor: pointer;
    user-select: none;
  }
  .cat-head .title {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--ink-soft);
  }
  .cat-head .title .icon {
    color: var(--cat-color, var(--blue));
    font-size: 14px;
  }
  .cat-head .count {
    font-size: 30px;
    font-weight: 600;
    line-height: 1;
    letter-spacing: -0.02em;
    color: var(--ink);
  }
  .cat-card .head-sub {
    margin-top: 6px;
    font-size: 11px;
    color: var(--ink-mute);
    line-height: 1.5;
    font-family: var(--font-mono);
  }
  .cat-card .findings {
    margin-top: 10px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .finding-pill {
    background: rgba(255,75,75,0.10);
    border: 1px solid rgba(255,75,75,0.40);
    color: #ff8b8b;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 11px;
    font-family: var(--font-mono);
  }
  .cat-card .kind-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 12px;
  }
  .kind-chip {
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--ink-line);
    border-radius: 999px;
    padding: 2px 10px;
    font-size: 11px;
    color: var(--ink-mute);
    font-family: var(--font-mono);
  }
  .kind-chip strong {
    color: var(--ink);
    margin-left: 4px;
  }
  .cat-card .body {
    margin-top: 14px;
    border-top: 1px solid var(--ink-line);
    padding-top: 12px;
    display: none;
    max-height: 360px;
    overflow-y: auto;
  }
  .cat-card[data-open="true"] .body { display: block; }
  .cat-row {
    display: flex;
    flex-direction: column;
    padding: 7px 0;
    border-bottom: 1px solid var(--ink-line-soft);
    font-size: 12px;
  }
  .cat-row:last-child { border-bottom: none; }
  .cat-row .row-head {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    color: var(--ink);
  }
  .cat-row .row-kind {
    color: var(--ink-mute);
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .cat-row .row-addr {
    font-family: var(--font-mono);
    word-break: break-all;
  }
  .cat-row .row-meta {
    color: var(--ink-faint);
    font-size: 11px;
    margin-top: 2px;
    font-family: var(--font-mono);
  }
  .cat-row .row-display {
    color: var(--ink-soft);
    margin-top: 2px;
    font-size: 11px;
  }
  .cat-row .row-pills {
    margin-top: 4px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .principal-pill {
    background: rgba(22,119,255,0.08);
    border: 1px solid rgba(22,119,255,0.30);
    color: var(--blue-soft);
    border-radius: 8px;
    padding: 1px 8px;
    font-size: 10px;
    font-family: var(--font-mono);
    word-break: break-all;
  }
  .principal-pill.kind-service {
    background: rgba(57,196,122,0.08);
    border-color: rgba(57,196,122,0.30);
    color: #5fd098;
  }
  .principal-pill.kind-federated {
    background: rgba(216,150,20,0.10);
    border-color: rgba(216,150,20,0.35);
    color: #f5b042;
  }
  .principal-pill.kind-aws {
    background: rgba(162,102,255,0.08);
    border-color: rgba(162,102,255,0.30);
    color: #c39cff;
  }

  /* Charts row sits above the category grid */
  .chart-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 14px;
    margin-bottom: 24px;
  }
  .chart-card {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius-lg);
    padding: 14px 16px 18px;
    min-height: 220px;
  }
  .chart-card h4 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--ink-faint);
    margin-bottom: 10px;
    font-weight: 500;
  }
  .chart-card canvas { max-height: 200px !important; }
  .chart-card .chart-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 180px;
    color: var(--ink-faint);
    font-family: var(--font-mono);
    font-size: 11px;
    text-align: center;
    line-height: 1.6;
  }
  .chart-card .chart-empty .kbd {
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--ink-line);
    border-radius: 4px;
    padding: 1px 6px;
    color: var(--blue-soft);
  }
  .chart-card .chart-empty .kbd-copy {
    margin-top: 8px;
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: 11px;
    border-radius: 6px;
    padding: 4px 10px;
    transition: border-color 120ms, background 120ms;
  }
  .chart-card .chart-empty .kbd-copy:hover {
    border-color: var(--blue-soft);
    background: rgba(22,119,255,0.10);
  }
  .chart-card .chart-empty .kbd-tick {
    margin-left: 6px;
    color: var(--ink-faint);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 9px;
  }

  /* IAM section */
  .iam-banner {
    padding: 10px 14px;
    border-radius: var(--radius);
    border: 1px solid var(--ink-line);
    background: var(--bg-card);
    font-size: 12px;
    margin-bottom: 14px;
    line-height: 1.6;
  }
  .iam-banner.ok   { border-color: rgba(57,196,122,0.35); color: #b6e8c8; }
  .iam-banner.warn { border-color: rgba(216,150,20,0.35); color: #f5d9a0; }
  .iam-banner .kbd {
    background: rgba(255,255,255,0.05);
    border: 1px solid var(--ink-line);
    border-radius: 4px;
    padding: 1px 6px;
    color: var(--ink);
  }
  .iam-groups {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
    gap: 12px;
  }
  .iam-group {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 10px 14px 14px;
  }
  .iam-group summary {
    list-style: none;
    cursor: pointer;
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--ink-line-soft);
    margin-bottom: 8px;
    user-select: none;
  }
  .iam-group summary::-webkit-details-marker { display: none; }
  .iam-mod {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--ink);
    flex: 1;
  }
  .iam-counts {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--blue-soft);
  }
  .iam-envs {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--ink-faint);
  }
  .iam-roles {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .iam-role {
    border-left: 2px solid rgba(255,153,0,0.35);
    padding: 6px 0 6px 12px;
  }
  .iam-role-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
  }
  .iam-role-name {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--ink);
    word-break: break-all;
  }
  .iam-role-env {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--ink-mute);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .iam-role-meta {
    margin-top: 3px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .iam-meta {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--ink-mute);
  }
  .iam-oidc {
    margin-top: 14px;
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 10px 14px 12px;
  }
  .iam-oidc-title {
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--ink-faint);
    margin-bottom: 8px;
  }
  .iam-oidc-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .iam-oidc-row {
    display: grid;
    grid-template-columns: 1fr 1.5fr 1fr;
    gap: 10px;
    font-size: 11px;
    color: var(--ink-mute);
    padding: 3px 0;
    border-bottom: 1px solid var(--ink-line-soft);
  }
  .iam-oidc-row:last-child { border-bottom: none; }
  .iam-oidc-row .iam-oidc-mod { color: var(--ink); }

  /* v0.34.6: AWS-style architecture diagram (replaces the old mermaid
     stack-flow). Each architectural layer is a band; each band holds
     service tiles with the AWS iconify icon, service label, count, and
     a hover-revealed sample of resource addresses. Tiles click through
     to the Graph view filtered by that resource_type. */
  .arch {
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius-lg);
    padding: 16px 18px 18px;
    margin-bottom: 24px;
    overflow: hidden;
  }
  .arch-summary {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--ink-faint);
    margin-bottom: 12px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
  }
  .arch-summary strong { color: var(--ink); margin: 0 4px; }
  .arch-layer {
    display: grid;
    grid-template-columns: 180px 1fr;
    gap: 14px;
    padding: 10px 0;
    border-top: 1px solid var(--ink-line-soft);
    align-items: stretch;
  }
  .arch-layer:first-of-type { border-top: none; }
  .arch-layer-head {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
  }
  .arch-layer-title {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--ink-soft);
  }
  .arch-layer-sub {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--ink-faint);
  }
  /* Per-layer accent stripe — hooks via data-layer */
  .arch-layer[data-layer=edge]       .arch-layer-title { color: #ff8b3d; }
  .arch-layer[data-layer=compute]    .arch-layer-title { color: #3c89e8; }
  .arch-layer[data-layer=data]       .arch-layer-title { color: #5fd098; }
  .arch-layer[data-layer=network]    .arch-layer-title { color: #f5b042; }
  .arch-layer[data-layer=identity]   .arch-layer-title { color: #ff9900; }
  .arch-layer[data-layer=secrets]    .arch-layer-title { color: #c39cff; }
  .arch-layer[data-layer=registries] .arch-layer-title { color: #a266ff; }
  .arch-layer[data-layer=ops]        .arch-layer-title { color: #22a1c4; }
  .arch-layer[data-layer=k8s]        .arch-layer-title { color: #39c47a; }
  .arch-tiles {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 8px;
    align-items: stretch;
  }
  .arch-tile {
    position: relative;
    background: rgba(255,255,255,0.025);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 10px 12px 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    cursor: pointer;
    transition: border-color 120ms, background 120ms, transform 120ms;
    min-height: 84px;
  }
  .arch-tile:hover {
    border-color: var(--blue-soft);
    background: rgba(22,119,255,0.05);
    transform: translateY(-1px);
  }
  .arch-tile-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .arch-tile iconify-icon {
    font-size: 22px;
    /* iconify-icon honors color when icons use currentColor; AWS logos
       have their own color baked in so iconify ignores ours — ok. */
    flex: 0 0 auto;
  }
  .arch-tile-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--ink);
    line-height: 1.2;
  }
  .arch-tile-count {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--blue-soft);
    font-weight: 600;
  }
  .arch-tile-meta {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--ink-faint);
    line-height: 1.4;
    margin-top: 2px;
    word-break: break-all;
  }
  .arch-tile[data-empty="true"] {
    background: rgba(255,255,255,0.015);
    color: var(--ink-faint);
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
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
    color: var(--ink-mute);
    font-family: var(--font-mono);
  }
  .ll-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 10px 4px 8px;
    border: 1px solid var(--ink-line);
    border-radius: 999px;
    background: rgba(255,255,255,0.02);
    cursor: help;
  }
  .ll-pill .ll-dot { width: 7px; height: 7px; border-radius: 50%; flex: 0 0 auto; }
  .ll-pill.ll-static { border-color: rgba(22,119,255,0.30); }
  .ll-pill.ll-static .ll-dot { background: var(--blue); box-shadow: 0 0 6px var(--blue-glow); }
  .ll-pill.ll-state  { border-color: rgba(255,153,0,0.30); }
  .ll-pill.ll-state  .ll-dot { background: var(--aws); box-shadow: 0 0 6px rgba(255,153,0,0.4); }
  .ll-pill.ll-k8s    { border-color: rgba(228,77,77,0.32); }
  .ll-pill.ll-k8s    .ll-dot { background: var(--k8s-red); box-shadow: 0 0 6px rgba(228,77,77,0.45); }
  .ll-pill.ll-docs   { border-color: var(--ink-line); }
  .ll-pill.ll-docs   .ll-dot { background: var(--ink-mute); }
  .ll-pill strong { color: var(--ink); margin-left: 2px; }

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
  /* v0.34.0: 3D force-directed graph (3d-force-graph + three.js). Nodes
     float in WebGL; the WebGL canvas is mounted into #graph-3d by the
     library. The fixed dotted background is rendered behind the canvas. */
  #graph-3d {
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background-color: var(--bg);
    background-image: radial-gradient(circle, rgba(255,255,255,0.04) 1px, transparent 1.4px);
    background-size: 22px 22px;
  }
  #graph-3d canvas { display: block; }
  #graph-empty-msg {
    position: absolute;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    color: var(--ink-mute);
    font-family: var(--font-mono);
    font-size: 13px;
    pointer-events: none;
  }
  #graph-3d.is-empty + #graph-empty-msg { display: flex; }
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
  #sidebar .chip.layer-k8s   { color: var(--k8s-red-soft); border-color: rgba(228,77,77,0.32); background: rgba(228,77,77,0.10); }
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
      <label class="layer-toggle active" data-layer="static"
             title="IaC files — Infrastructure-as-Code in this repo (HCL / JSON / CUE). The source of truth before anything is applied.">
        <input type="checkbox" data-layer="static" checked><span class="dot"></span>IaC files</label>
      <label class="layer-toggle active" data-layer="state"
             title="Terragrunt / OpenTofu state — resources actually created for this cluster/env. What the IaC files produced after `terragrunt apply`.">
        <input type="checkbox" data-layer="state" checked><span class="dot"></span>TG / OpenTofu state</label>
      <label class="layer-toggle inactive" data-layer="k8s"
             title="Live Kubernetes resources — Deployments, Services, ConfigMaps, etc. read from the running cluster overlay.">
        <input type="checkbox" data-layer="k8s"><span class="dot"></span>K8s resources</label>
      <label class="layer-toggle active" data-layer="docs"
             title="Documentation references — module READMEs, runbooks, ADRs that mention the modules in this graph.">
        <input type="checkbox" data-layer="docs" checked><span class="dot"></span>Docs</label>
    </div>
    <select id="graph-view-mode" title="Graph scope — start with overview on large stacks">
      <option value="overview">Overview (module deps)</option>
      <option value="full" selected>Full graph</option>
    </select>
    <select id="graph-group-by" title="Group nodes by — drives both color and cluster placement">
      <option value="source_layer" selected>Group by layer</option>
      <option value="environment">Group by env</option>
      <option value="type">Group by node type</option>
      <option value="resource_type">Group by resource type</option>
      <option value="module">Group by module</option>
      <option value="provider">Group by provider</option>
    </select>
    <button type="button" id="graph-filters-toggle" class="filters-toggle" aria-expanded="false">Filters ▾</button>
    <button type="button" id="graph-reset" class="filters-reset" title="Clear filters and recenter">Reset</button>
    <span id="layout-badge" title="3D force-directed (d3-force-3d + three.js)">force · 3D</span>
    <span class="stats" id="stats"></span>
  </div>
  <div id="graph-filter-panel" hidden>
    <button type="button" class="fp-close" id="graph-filter-close" aria-label="Close filters" title="Close (Esc)">×</button>
    <div class="fp-section">
      <div class="fp-label">Environment</div>
      <div class="fp-row" id="fp-envs"></div>
    </div>
    <div class="fp-section">
      <div class="fp-label">Module</div>
      <div class="fp-multi" id="fp-modules"></div>
    </div>
    <div class="fp-section">
      <div class="fp-label">Resource type</div>
      <div class="fp-multi" id="fp-rtypes"></div>
    </div>
    <div class="fp-section">
      <div class="fp-label">Node type</div>
      <div class="fp-row" id="fp-ntypes"></div>
    </div>
  </div>
</div>

<div id="dashboard-wrap"></div>

<div id="graph-shell">
  <div id="graph-3d"></div>
  <div id="graph-empty-msg">no nodes match the current layer + view filter</div>
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

/* v0.34.2: Dedicated IAM section — every role grouped by module with
 * its trust principals, attached/inline policy counts, and OIDC provider
 * panel. When schema-v3 essentials aren't present yet, principals show a
 * "regen state" hint instead of an empty pane. */
function renderIamView(iam) {
  if (!iam || !iam.groups || !iam.groups.length) {
    return `<p style="color:var(--ink-faint)">No <span class="mono">aws_iam_role</span> resources in the state overlay.</p>`;
  }
  const banner = iam.has_essentials
    ? `<div class="iam-banner ok">$${iam.total_roles} roles · $${iam.principals_total} trust principals across $${iam.groups.length} module$${iam.groups.length === 1 ? "" : "s"}</div>`
    : `<div class="iam-banner warn">$${iam.total_roles} roles across $${iam.groups.length} module$${iam.groups.length === 1 ? "" : "s"} · trust principals not in current state overlay.<br><span class="mono">Run <span class="kbd">state_graph.py generate</span> to populate schema-v3 essentials.</span></div>`;
  const principalChip = (p) => {
    const m = /^([^:]+):(.*)$$/.exec(p);
    const kind = m ? m[1] : "";
    const val  = m ? m[2] : p;
    return `<span class="principal-pill kind-$${esc(kind)}">$${esc(kind)} · $${esc(val)}</span>`;
  };
  const groupBlocks = iam.groups.map(g => {
    const rolesRows = g.roles.map(r => {
      const pillsHtml = (r.principals || []).slice(0, 16).map(principalChip).join("");
      const principalsRow = pillsHtml
        ? `<div class="row-pills">$${pillsHtml}</div>`
        : (iam.has_essentials
            ? `<div class="row-meta" style="color:var(--ink-faint)">no trust principals declared</div>`
            : "");
      const policyMeta = (r.attached_policies || r.policies_inline)
        ? `<span class="iam-meta">$${r.attached_policies} attached · $${r.policies_inline} inline</span>`
        : `<span class="iam-meta" style="color:var(--ink-faint)">policy count requires essentials</span>`;
      const session = (typeof r.max_session_duration === "number")
        ? `<span class="iam-meta">$${r.max_session_duration}s session</span>` : "";
      return `<div class="iam-role">
        <div class="iam-role-head">
          <div class="iam-role-name">$${esc(r.name || r.address)}</div>
          <div class="iam-role-env">$${esc(r.env || "—")}</div>
        </div>
        <div class="iam-role-meta">$${policyMeta}$${session}</div>
        $${principalsRow}
      </div>`;
    }).join("");
    return `<details class="iam-group" open>
      <summary>
        <span class="iam-mod">$${esc(g.module || "(no module)")}</span>
        <span class="iam-counts">$${g.role_count} role$${g.role_count === 1 ? "" : "s"}$${g.principal_count ? ` · $${g.principal_count} principal$${g.principal_count === 1 ? "" : "s"}` : ""}</span>
        <span class="iam-envs">$${(g.envs || []).map(esc).join(", ")}</span>
      </summary>
      <div class="iam-roles">$${rolesRows}</div>
    </details>`;
  }).join("");
  /* Optional OIDC provider strip — small, useful for debugging IRSA. */
  const oidcHtml = (iam.oidc_providers || []).length
    ? `<div class="iam-oidc"><div class="iam-oidc-title">OIDC providers ($${iam.oidc_providers.length})</div><div class="iam-oidc-list">`
      + iam.oidc_providers.map(o =>
          `<div class="iam-oidc-row">
             <span class="iam-oidc-mod">$${esc(o.module || "(no module)")} · $${esc(o.env || "—")}</span>
             <span class="iam-oidc-url mono">$${esc(o.url || "—")}</span>
             <span class="iam-oidc-clients mono">$${esc((o.client_id_list || []).slice(0, 4).join(", "))}</span>
           </div>`).join("")
      + "</div></div>"
    : "";
  /* IRSA bindings (k8s SA → role) belong here visually. */
  const irsaHtml = (iam.irsa_bindings || []).length
    ? `<div class="iam-oidc"><div class="iam-oidc-title">IRSA bindings ($${iam.irsa_bindings.length}) — k8s ServiceAccount → AWS role</div><div class="iam-oidc-list">`
      + iam.irsa_bindings.slice(0, 50).map(b =>
          `<div class="iam-oidc-row">
             <span class="iam-oidc-mod">$${esc(b.env || "—")} · ns:$${esc(b.ns || "—")}</span>
             <span class="iam-oidc-url mono">$${esc(b.sa)}</span>
             <span class="iam-oidc-clients mono">→ $${esc(b.role)}</span>
           </div>`).join("")
      + "</div></div>"
    : "";
  return `$${banner}<div class="iam-groups">$${groupBlocks}</div>$${oidcHtml}$${irsaHtml}`;
}

/* v0.35.0: Secret refs (HCL inputs / component JSONs → Secrets Manager). */
function renderSecretRefs(sr) {
  const secrets = (sr && sr.secrets) || [];
  const orphans = (sr && sr.orphan_refs) || [];
  if (!secrets.length && !orphans.length) {
    return `<p style="color:var(--ink-mute)">No <span class="mono">aws_secretsmanager_secret</span> resources discovered yet.</p>`;
  }
  const rows = secrets.slice(0, 100).map(s => {
    const refs = (s.referenced_by || []).map(r =>
      `<div class="row-meta">$${esc(r.file)} <span style="color:var(--ink-faint)">→ $${esc(r.key)}</span></div>`
    ).join("");
    return `<div class="cat-row">
      <div class="row-head">
        <div class="row-addr">$${esc(s.name || s.address)}</div>
        <div class="row-kind">$${esc(s.module)} · $${esc(s.env)}</div>
      </div>
      $${refs || `<div class="row-meta" style="color:var(--ink-faint)">no component file references this secret name verbatim</div>`}
    </div>`;
  }).join("");
  const orphanHtml = orphans.length
    ? `<details style="margin-top:10px"><summary style="color:var(--ink-mute);font-family:var(--font-mono);font-size:11px;cursor:pointer">$${orphans.length} orphan secret-key reference$${orphans.length === 1 ? "" : "s"} (no matching aws_secretsmanager_secret found)</summary>`
      + orphans.map(r =>
          `<div class="row-meta">$${esc(r.file)} <span style="color:var(--ink-faint)">→ $${esc(r.key)} = $${esc(r.value)}</span></div>`
        ).join("") + `</details>`
    : "";
  return `<div>$${rows}$${orphanHtml}</div>`;
}

/* v0.35.0: CUE schema tree — files + their top-level fields. */
function renderCueSchemas(cs) {
  const files = (cs && cs.files) || [];
  if (!files.length) {
    return `<p style="color:var(--ink-mute)">No <span class="mono">cue/</span> schema files found in this repo.</p>`;
  }
  return `<p style="color:var(--ink-faint);font-size:11px;font-family:var(--font-mono);margin-bottom:8px">$${cs.file_count} schema file$${cs.file_count === 1 ? "" : "s"} · $${cs.field_count} top-level fields</p>` + files.slice(0, 30).map(f => {
    const fields = (f.fields || []).map(fld =>
      `<span class="kind-chip"><strong>$${esc(fld.name)}</strong> $${esc(fld.type)}</span>`
    ).join("");
    return `<details class="cat-card" style="margin-bottom:8px"><summary style="cursor:pointer">
      <span class="mono" style="color:var(--ink)">$${esc(f.file)}</span>
      <span style="color:var(--ink-faint);font-family:var(--font-mono);font-size:11px;margin-left:8px">$${f.field_count} fields$${f.package ? ' · pkg ' + esc(f.package) : ''}</span>
    </summary><div class="kind-row" style="margin-top:8px">$${fields}</div></details>`;
  }).join("");
}

/* v0.35.0: CI/CD workflow origins. */
function renderWorkflows(wf) {
  const flows = (wf && wf.workflows) || [];
  if (!flows.length) {
    return `<p style="color:var(--ink-mute)">No <span class="mono">.github/workflows</span> files found.</p>`;
  }
  return flows.map(f => {
    const mods = (f.module_refs || []).map(m =>
      `<span class="kind-chip">$${esc(m)}</span>`).join("") ||
      `<span style="color:var(--ink-faint);font-size:11px">no module refs</span>`;
    const comps = (f.component_refs || []).map(c =>
      `<span class="kind-chip">$${esc(c.env)}/$${esc(c.name)}</span>`).join("") || "";
    const triggers = (f.triggers || []).map(t =>
      `<span class="finding-pill" style="background:rgba(22,119,255,0.10);border-color:rgba(22,119,255,0.30);color:#cfe2ff">$${esc(t)}</span>`
    ).join("");
    return `<div class="cat-card" style="margin-bottom:10px">
      <div class="cat-head" style="cursor:default">
        <div class="title"><span class="icon">⚙</span><span class="mono">$${esc(f.file)}</span></div>
        <div class="findings">$${triggers}</div>
      </div>
      <div class="kind-row" style="margin-top:8px">$${mods}</div>
      $${comps ? `<div class="kind-row" style="margin-top:6px">$${comps}</div>` : ""}
    </div>`;
  }).join("");
}

/* v0.35.0: Rendered-apps panel (output of manual render_apps.py). */
function renderRenderedApps(byEnv, drift) {
  const envs = Object.keys(byEnv || {});
  if (!envs.length) {
    return `<p style="color:var(--ink-mute)">No rendered manifest data yet. Run the manual renderer to populate this view:</p>
      <button type="button" class="kbd kbd-copy" data-copy="python3 apm_modules/kuberly/kuberly-skills/scripts/render_apps.py" title="Copy">
        render_apps.py <span class="kbd-tick">copy</span>
      </button>
      <button type="button" class="kbd kbd-copy" data-copy="python3 apm_modules/kuberly/kuberly-skills/scripts/diff_apps.py" title="Copy" style="margin-left:6px">
        diff_apps.py <span class="kbd-tick">copy</span>
      </button>`;
  }
  return envs.map(env => {
    const e = byEnv[env];
    const driftEnv = (drift && drift[env]) || null;
    const driftByApp = {};
    if (driftEnv) {
      for (const a of (driftEnv.apps || [])) driftByApp[a.app] = a;
    }
    const apps = (e.apps || []).map(a => {
      const kinds = Object.entries(a.kind_counts || {})
        .sort((x, y) => y[1] - x[1])
        .map(([k, n]) => `<span class="kind-chip">$${esc(k)}<strong>$${n}</strong></span>`).join("");
      const d = driftByApp[a.app];
      const driftSub = d
        ? `<span style="color:$${(d.summary.missing_in_cluster || d.summary.extra_in_cluster) ? '#f5b042' : '#5fd098'};font-family:var(--font-mono);font-size:11px">declared $${d.summary.declared} · running $${d.summary.running} · missing $${d.summary.missing_in_cluster} · extra $${d.summary.extra_in_cluster}</span>`
        : `<span style="color:var(--ink-faint);font-size:11px;font-family:var(--font-mono)">no drift data — run diff_apps.py</span>`;
      const errBlock = a.ok ? "" :
        `<div style="color:#ff8b8b;font-family:var(--font-mono);font-size:11px">render failed: $${esc((a.error || []).join(' / '))}</div>`;
      return `<div class="cat-card" style="margin-bottom:8px">
        <div class="cat-head" style="cursor:default">
          <div class="title"><span class="mono" style="color:var(--ink)">$${esc(a.app)}</span></div>
          <div class="count" style="font-size:18px">$${a.resource_count || 0}</div>
        </div>
        <div class="head-sub">$${driftSub}</div>
        $${errBlock}
        <div class="kind-row" style="margin-top:8px">$${kinds}</div>
      </div>`;
    }).join("");
    return `<details class="cat-card" open style="margin-bottom:12px">
      <summary style="cursor:pointer"><span class="mono" style="color:var(--ink)">env: $${esc(env)}</span>
        <span style="color:var(--ink-faint);font-family:var(--font-mono);font-size:11px;margin-left:8px">$${e.app_count} apps · $${e.resource_count} rendered resources · $${esc(e.generated_at)}</span>
      </summary>
      <div style="margin-top:8px">$${apps}</div>
    </details>`;
  }).join("");
}

/* v0.35.0: Security findings — high/medium/low tiers, expandable. */
function renderFindings(f) {
  const items = (f && f.items) || {};
  const sum = (f && f.summary) || {};
  if (!sum.total) {
    return `<p style="color:var(--ink-mute)">No security findings detected — all clear.</p>`;
  }
  const tier = (sev, label) => {
    const rows = (items[sev] || []);
    if (!rows.length) return "";
    return `<details class="findings-tier $${sev}" $${sev === 'high' ? 'open' : ''}>
      <summary><h3>$${esc(label)} ($${rows.length})</h3></summary>
      $${rows.slice(0, 50).map(r => `<div class="finding-row">
        <div class="rule">$${esc(r.rule)}</div>
        <div>$${esc(r.detail || "")}</div>
        <div class="where">$${esc(r.module || "")} · $${esc(r.env || "")}</div>
      </div>`).join("")}
      $${rows.length > 50 ? `<div style="color:var(--ink-faint);font-size:11px;margin-top:6px;font-family:var(--font-mono)">… $${rows.length - 50} more</div>` : ""}
    </details>`;
  };
  return tier("high", "High") + tier("medium", "Medium") + tier("low", "Low");
}

/* v0.35.0: Module age heatmap. */
function renderModuleAge(rows) {
  if (!rows || !rows.length) {
    return `<p style="color:var(--ink-mute)">No state overlay loaded — run state_graph.py to populate.</p>`;
  }
  function bucket(s) {
    if (s == null)        return ["frozen", "—"];
    if (s < 86400)        return ["fresh", Math.floor(s / 3600) + "h"];
    if (s < 7 * 86400)    return ["warm",  Math.floor(s / 86400) + "d"];
    if (s < 30 * 86400)   return ["cold",  Math.floor(s / 86400) + "d"];
    return ["frozen", Math.floor(s / 86400) + "d"];
  }
  const cells = rows.slice(0, 100).map(r => {
    const [klass, age] = bucket(r.age_seconds);
    return `<div class="age-cell $${klass}">
      <div class="name">$${esc(r.module)}</div>
      <div class="meta">$${age} ago · $${r.resources} res · $${(r.envs || []).join(",")}</div>
    </div>`;
  }).join("");
  return `<div class="age-grid">$${cells}</div>`;
}

/* v0.35.0: App → ServiceAccount → IAM Role → Secret rollup. */
function renderAppSecretIam(rows) {
  if (!rows || !rows.length) {
    return `<p style="color:var(--ink-mute)">No IRSA bindings loaded — needs k8s overlay (k8s_graph.py) + state overlay with IAM roles.</p>`;
  }
  const cards = rows.map(r => {
    const wls = (r.workloads || []).slice(0, 4)
      .map(w => `$${esc(w.kind)} $${esc(w.name)}`).join(", ");
    return `<div class="asi-card">
      <div class="head">$${esc(r.service_account)} <span style="color:var(--ink-faint);font-weight:400">@ $${esc(r.ns)}</span></div>
      $${wls ? `<div class="meta">used by: $${wls}</div>` : `<div class="meta" style="color:var(--ink-faint)">no workload binds this SA</div>`}
      <div class="role">role: $${esc(r.iam_role)}</div>
      <div class="pol">$${r.policy_attachments} attached policies · $${r.inline_policies} inline · env $${esc(r.env || "—")}</div>
    </div>`;
  }).join("");
  return `<div class="asi-grid">$${cards}</div>`;
}

/* v0.35.0: Network reachability — per-SG ingress sources. */
function renderNetwork(rows) {
  if (!rows || !rows.length) {
    return `<p style="color:var(--ink-mute)">No security groups loaded — schema-v3 state overlay needed.</p>`;
  }
  const sources = (srcs) => (srcs || []).map(s => {
    const isWorld = s === "0.0.0.0/0";
    const cls = isWorld ? "src world" : "src";
    return `<span class="$${cls}">$${esc(s)}</span>`;
  }).join(", ") || `<span style="color:var(--ink-faint)">—</span>`;
  const cards = rows.slice(0, 60).map(sg => {
    const cardCls = "net-card" + (sg.open_to_world ? " world" : "");
    const ing = (sg.ingress || []).slice(0, 8).map(r =>
      `<div class="row"><span class="dir">in</span><span>$${esc(r.proto)} $${esc(r.ports)}</span> from $${sources(r.sources)}</div>`
    ).join("");
    const eg = (sg.egress || []).slice(0, 4).map(r =>
      `<div class="row"><span class="dir">out</span><span>$${esc(r.proto)} $${esc(r.ports)}</span> to $${sources(r.sources)}</div>`
    ).join("");
    return `<div class="$${cardCls}">
      <div class="name">$${esc(sg.name || sg.addr)}</div>
      $${sg.description ? `<div class="desc">$${esc(sg.description)}</div>` : ""}
      <div class="meta" style="color:var(--ink-faint);font-size:10px;font-family:var(--font-mono);margin:6px 0">$${esc(sg.module || "")} · $${esc(sg.env || "")}</div>
      $${ing || `<div style="color:var(--ink-faint);font-size:11px">no ingress rules collected</div>`}
      $${eg}
    </div>`;
  }).join("");
  return `<div class="net-grid">$${cards}</div>`;
}

/* v0.34.6: AWS-style layered architecture diagram. Each architectural
 * band ("Compute", "Data & Storage", ...) holds service tiles with the
 * AWS iconify icon, label, count, and a sample address. Click a tile
 * to switch to the Graph view filtered to that resource_type. */
function renderArchitectureDiagram(arch) {
  const layers = (arch && arch.layers) || [];
  if (!layers.length) {
    return `<p style="color:var(--ink-faint)">No deployed resources detected — run <span class="mono">state_graph.py generate --resources</span> to populate the architecture view.</p>`;
  }
  const summary = `<div class="arch-summary"><strong>$${arch.total_resources}</strong> deployed resources across <strong>$${arch.total_services}</strong> AWS services in <strong>$${layers.length}</strong> architectural layer$${layers.length === 1 ? '' : 's'}</div>`;
  const bands = layers.map(L => {
    const tiles = L.services.map(s => {
      const sample = s.items[0] || {};
      const sampleMeta = sample.address ? `$${esc(sample.address)}` : "";
      const moreCount = s.count > 1 ? ` <span style="color:var(--ink-faint)">+ $${s.count - 1} more</span>` : "";
      const sampleLine = sampleMeta ? `<div class="arch-tile-meta" title="$${esc(sampleMeta)}">$${sampleMeta}$${moreCount}</div>` : "";
      return `<div class="arch-tile" data-rtype="$${esc(s.rtype)}" title="Click to filter the Graph view by $${esc(s.rtype)} ($${s.count} resource$${s.count === 1 ? '' : 's'})">
        <div class="arch-tile-row">
          <iconify-icon icon="$${esc(s.icon)}" aria-hidden="true"></iconify-icon>
          <div class="arch-tile-label">$${esc(s.label)}</div>
          <div class="arch-tile-count">$${s.count}</div>
        </div>
        $${sampleLine}
      </div>`;
    }).join("");
    return `<div class="arch-layer" data-layer="$${esc(L.key)}">
      <div class="arch-layer-head">
        <div class="arch-layer-title">$${esc(L.title)}</div>
        <div class="arch-layer-sub">$${L.service_count} service$${L.service_count === 1 ? '' : 's'} · $${L.total} resource$${L.total === 1 ? '' : 's'}</div>
      </div>
      <div class="arch-tiles">$${tiles}</div>
    </div>`;
  }).join("");
  /* Wire click handlers AFTER innerHTML insert — kept out of render
   * function so it runs after dashboard root.innerHTML is in the DOM. */
  setTimeout(() => {
    document.querySelectorAll(".arch-tile[data-rtype]").forEach(tile => {
      if (tile.__wired) return;
      tile.__wired = true;
      tile.addEventListener("click", () => {
        const rtype = tile.getAttribute("data-rtype");
        if (!rtype) return;
        /* Switch to Graph view + apply the filter. setView triggers
         * buildGraph3D on first use, which registers the helper. */
        if (typeof setView === "function") setView("graph");
        /* Defer until Graph3D + GRAPH_STATE are initialized. */
        setTimeout(() => {
          if (typeof window.__kuberlyFilterByResourceType === "function") {
            window.__kuberlyFilterByResourceType(rtype);
          }
        }, 80);
      });
    });
  }, 0);
  return `<div class="arch">$${summary}$${bands}</div>`;
}

/* v0.34.0: Build the small Mermaid flowchart that sits above the category
 * cards. Pure string-builder — Mermaid takes the textContent later. The
 * counts feed live from DASHBOARD.categories so a stack with no Identity
 * still draws "0" rather than 404'ing the diagram. */
function buildStackFlowDiagram(cats) {
  const c = (k) => (cats && cats[k] && cats[k].count) || 0;
  return [
    "flowchart LR",
    `  net["Networking · $${c('networking')}"] --> compute["Compute · $${c('compute')}"] --> data["Data · $${c('data')}"]`,
    `  identity["Identity · $${c('identity')}"] --> compute`,
    "  identity --> data",
    `  secrets["Secrets / KMS · $${c('secrets')}"] --> compute`,
    `  registries["Registries · $${c('registries')}"] --> compute`,
    "  classDef net fill:#d8961422,stroke:#d89614,color:#f5b042;",
    "  classDef compute fill:#1677ff22,stroke:#1677ff,color:#3c89e8;",
    "  classDef data fill:#3c89e822,stroke:#3c89e8,color:#3c89e8;",
    "  classDef identity fill:#ff990022,stroke:#ff9900,color:#ff9900;",
    "  classDef secrets fill:#a266ff22,stroke:#a266ff,color:#c39cff;",
    "  classDef registries fill:#7c5cff22,stroke:#7c5cff,color:#a266ff;",
    "  class net net; class compute compute; class data data; class identity identity; class secrets secrets; class registries registries;",
  ].join("\n");
}

/* v0.34.0: Render the per-category cards (Compute / Data / Identity / ...).
 * Each card gets a colored top stripe, a headline count, kind chips, and an
 * expandable body of resource rows with their essentials. The expand state
 * is stored on the card element itself, no global state needed. */
function renderCategoryCards(cats) {
  const order = ["compute", "data", "identity", "networking",
                 "secrets", "registries", "queues", "k8s"];
  const cards = order.map(key => {
    const c = (cats || {})[key];
    if (!c || !c.count) return "";
    const findingsHtml = (c.findings || []).length
      ? `<div class="findings">$${(c.findings || []).slice(0, 4).map(f =>
          `<span class="finding-pill">$${esc(f)}</span>`).join("")}</div>`
      : "";
    const kindChips = Object.entries(c.kind_counts || {})
      .sort((a, b) => b[1] - a[1])
      .map(([k, n]) => `<span class="kind-chip">$${esc(k)}<strong>$${n}</strong></span>`)
      .join("");
    /* Per-row drill-down — capped at 100 to keep DOM small for big stacks. */
    const rows = (c.items || []).slice(0, 100).map(it => {
      const meta = [it.module, it.env].filter(Boolean).join(" · ");
      const display = it.display ? `<div class="row-display">$${esc(it.display)}</div>` : "";
      const principalPills = (it.principals || []).slice(0, 12).map(p => {
        const m = /^([^:]+):(.*)$$/.exec(p);
        const kind = m ? m[1] : "";
        const val  = m ? m[2] : p;
        return `<span class="principal-pill kind-$${esc(kind)}">$${esc(kind)} · $${esc(val)}</span>`;
      }).join("");
      const principalRow = principalPills
        ? `<div class="row-pills">$${principalPills}</div>` : "";
      const findRow = (it.findings || []).length
        ? `<div class="row-pills">$${(it.findings || []).map(f =>
            `<span class="finding-pill">$${esc(f)}</span>`).join("")}</div>`
        : "";
      return `<div class="cat-row">
        <div class="row-head">
          <div class="row-addr">$${esc(it.address)}</div>
          <div class="row-kind">$${esc(it.kind)}</div>
        </div>
        $${meta ? `<div class="row-meta">$${esc(meta)}</div>` : ""}
        $${display}
        $${principalRow}
        $${findRow}
      </div>`;
    }).join("");
    const moreNote = (c.items || []).length > 100
      ? `<div style="margin-top:8px;color:var(--ink-faint);font-size:11px;font-family:var(--font-mono)">… $${(c.items||[]).length - 100} more not shown</div>`
      : "";
    /* Sub-line with category-specific totals (EBS GB, helm chart count, ...). */
    let subline = "";
    if (key === "data" && c.totals && c.totals.ebs_total_gb) {
      subline = `EBS total <strong>$${c.totals.ebs_total_gb}</strong> GB`;
    } else if (key === "identity" && c.totals && c.totals.principal_kinds) {
      const pk = c.totals.principal_kinds;
      subline = Object.entries(pk).map(([k,v]) =>
        `<strong>$${v}</strong> $${esc(k)}`).join(" · ") || "";
    } else if (key === "k8s" && c.totals && (c.totals.helm_charts || []).length) {
      subline = `charts: $${(c.totals.helm_charts || []).slice(0, 6).map(esc).join(", ")}`;
    }
    return `<div class="cat-card" style="--cat-color:$${esc(c.color)}" data-cat="$${esc(key)}">
      <div class="cat-head">
        <div class="title"><span class="icon">$${esc(c.icon)}</span>$${esc(c.title)}</div>
        <div class="count">$${c.count}</div>
      </div>
      $${subline ? `<div class="head-sub">$${subline}</div>` : ""}
      <div class="kind-row">$${kindChips}</div>
      $${findingsHtml}
      <div class="body">$${rows}$${moreNote}</div>
    </div>`;
  }).join("");
  return cards
    ? `<div class="cat-grid">$${cards}</div>`
    : `<p style="color:var(--ink-faint)">No state-overlay essentials yet — run <span class="mono">state_graph.py generate</span> against your envs.</p>`;
}

/* v0.34.0: Charts (Chart.js doughnut + bar). Called AFTER renderDashboard
 * has injected the canvas elements; safe no-op if Chart.js failed to load. */
function renderDashboardCharts(cats) {
  if (typeof Chart === "undefined") return;
  Chart.defaults.color = "rgba(255,255,255,0.65)";
  Chart.defaults.borderColor = "rgba(255,255,255,0.10)";
  Chart.defaults.font.family = 'JetBrains Mono, ui-monospace, monospace';
  const palette = ["#1677ff","#ff9900","#d89614","#39c47a","#a266ff","#22a1c4","#7c5cff","#3c89e8","#f5b042","#5fd098"];
  function destroyOnEl(id) {
    const el = document.getElementById(id);
    if (el && el.__chart) { el.__chart.destroy(); el.__chart = null; }
    return el;
  }
  /* Doughnut: category share of total resources. */
  (() => {
    const el = destroyOnEl("chart-cat-share");
    if (!el) return;
    const labels = [], data = [], colors = [];
    Object.entries(cats || {}).forEach(([k, c]) => {
      if (c && c.count) { labels.push(c.title); data.push(c.count); colors.push(c.color || "#888"); }
    });
    if (!labels.length) return;
    el.__chart = new Chart(el, {
      type: "doughnut",
      data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "62%",
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 8, padding: 8, font: { size: 10 } } },
          tooltip: { backgroundColor: "rgba(20,24,30,0.95)" },
        },
      },
    });
  })();
  /* Bar: IAM principal-kind distribution. */
  (() => {
    const el = destroyOnEl("chart-iam-principals");
    if (!el) return;
    const pk = (cats && cats.identity && cats.identity.totals && cats.identity.totals.principal_kinds) || {};
    const labels = Object.keys(pk);
    if (!labels.length) {
      /* Replace canvas with helpful placeholder rather than blank box.
       * Click-to-copy: clicking the command copies it to clipboard with
       * a visible "copied" tick — saves the user from typing the long
       * apm_modules path. */
      const card = el.parentElement;
      if (card && !card.querySelector(".chart-empty")) {
        const cmd = "python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/state_graph.py generate --env prod --resources --output .kuberly/state_overlay_prod.json && python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py generate . -o .kuberly";
        const ph = document.createElement("div");
        ph.className = "chart-empty";
        ph.innerHTML =
          'no trust principals in current state overlay<br>' +
          '<span style="margin-top:6px;display:inline-block">click the command to copy &amp; run with AWS SSO active:</span><br>' +
          '<button type="button" class="kbd kbd-copy" data-copy="' + cmd.replace(/"/g, "&quot;") + '" title="Copy to clipboard">' +
            'state_graph.py generate <span class="kbd-tick">copy</span>' +
          '</button>' +
          '<br><span style="margin-top:4px;display:inline-block;color:var(--ink-faint)">requires AWS SSO authenticated for this account</span>';
        el.style.display = "none";
        card.appendChild(ph);
        const btn = ph.querySelector(".kbd-copy");
        if (btn) {
          btn.addEventListener("click", async () => {
            const text = btn.getAttribute("data-copy") || "";
            try {
              await navigator.clipboard.writeText(text);
              const tick = btn.querySelector(".kbd-tick");
              if (tick) {
                tick.textContent = "copied ✓";
                setTimeout(() => { tick.textContent = "copy"; }, 1600);
              }
            } catch (e) {
              /* Clipboard API may be blocked on file:// — fall back to
               * select-all so the user can ⌘C manually. */
              const r = document.createRange(); r.selectNodeContents(btn);
              const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
            }
          });
        }
      }
      return;
    }
    el.__chart = new Chart(el, {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "principals", data: labels.map(k => pk[k]),
          backgroundColor: labels.map((_, i) => palette[i % palette.length]),
          borderRadius: 4 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 10 } } },
          y: { beginAtZero: true, ticks: { font: { size: 10 }, precision: 0 }, grid: { color: "rgba(255,255,255,0.06)" } },
        },
      },
    });
  })();
  /* Bar: top resource types overall. */
  (() => {
    const el = destroyOnEl("chart-top-rtypes");
    if (!el) return;
    const top = (DASHBOARD.state && DASHBOARD.state.top_resource_types) || [];
    if (!top.length) return;
    const sliced = top.slice(0, 10);
    el.__chart = new Chart(el, {
      type: "bar",
      data: {
        labels: sliced.map(x => x.type),
        datasets: [{ label: "count", data: sliced.map(x => x.count),
          backgroundColor: sliced.map((_, i) => palette[i % palette.length]),
          borderRadius: 4 }],
      },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { font: { size: 10 }, precision: 0 }, grid: { color: "rgba(255,255,255,0.06)" } },
          y: { grid: { display: false }, ticks: { font: { size: 9 } } },
        },
      },
    });
  })();
}

function renderDashboard() {
  const root = document.getElementById("dashboard-wrap");
  const m = DASHBOARD.meta || {};
  const kpis = DASHBOARD.kpis || {};

  /* v0.35.0: customer-focused KPIs.
   *  findings:   security findings count + severity breakdown
   *  resources:  AWS resources actually deployed
   *  state_age:  youngest state snapshot age + oldest
   *  app_health: live workload health from k8s overlay
   *  applications: app JSON sidecars
   *  drift:      cross-env drift count
   */
  const kpiLabels = { findings: "Security findings", resources: "AWS resources",
    state_age: "State age", app_health: "App health",
    applications: "Applications", drift: "Cross-env drift" };
  const kpiAccent = { findings: "warn", resources: "blue",
    state_age: "blue", app_health: "ok",
    applications: "blue", drift: "warn" };
  const kpiHtml = ["findings", "state_age", "app_health", "resources", "applications", "drift"].map(k => {
    const x = kpis[k] || {};
    return `<div class="kpi kpi-$${kpiAccent[k] || ''}"><div class="label">$${esc(kpiLabels[k] || k)}</div><div class="value">$${esc(x.value)}</div><div class="sub">$${esc(x.sub || "")}</div></div>`;
  }).join("");

  const cov = DASHBOARD.coverage || {};
  const layers = DASHBOARD.source_layers || {};
  /* v0.34.3: descriptive labels for each source layer.
     - static: IaC files in this repo (HCL / JSON / CUE)
     - state:  Terragrunt / OpenTofu state — what's actually applied
     - k8s:    live cluster overlay (Deployments, Services, ...)
     - docs:   README / runbook references */
  const LAYER_LABELS = {
    static: "IaC files",
    state:  "TG / OpenTofu state",
    k8s:    "K8s resources",
    docs:   "Docs",
  };
  const layerLegend = ["static", "state", "k8s", "docs"].map(L =>
    `<span class="ll-pill ll-$${L}" title="$${esc(L === 'static' ? 'Infrastructure-as-Code files in the repo (HCL / JSON / CUE)' : L === 'state' ? 'Resources actually created by Terragrunt / OpenTofu for this cluster/env' : L === 'k8s' ? 'Live Kubernetes cluster resources (Deployments, Services, Pods, ...)' : 'Documentation references — module READMEs, runbooks, ADRs')}"><span class="ll-dot"></span>$${esc(LAYER_LABELS[L])}: <strong>$${layers[L] || 0}</strong></span>`
  ).join(" ");

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
      <h2>Infrastructure essentials</h2>
      <div class="chart-row">
        <div class="chart-card"><h4>Category share</h4><canvas id="chart-cat-share"></canvas></div>
        <div class="chart-card"><h4>IAM role trust — by principal kind</h4><canvas id="chart-iam-principals"></canvas></div>
        <div class="chart-card"><h4>Top resource types</h4><canvas id="chart-top-rtypes"></canvas></div>
      </div>
      $${renderArchitectureDiagram(DASHBOARD.architecture || {})}
      $${renderCategoryCards(DASHBOARD.categories || {})}
    </section>

    <section class="section">
      <h2>Security findings</h2>
      $${renderFindings(DASHBOARD.findings || {})}
    </section>

    <section class="section">
      <h2>Module age — last applied</h2>
      $${renderModuleAge(DASHBOARD.module_age || [])}
    </section>

    <section class="section">
      <h2>IAM identity & access</h2>
      $${renderIamView(DASHBOARD.iam || {})}
    </section>

    <section class="section">
      <h2>Apps → IAM → Secrets</h2>
      $${renderAppSecretIam(DASHBOARD.app_secret_iam || [])}
    </section>

    <section class="section">
      <h2>Network reachability — security groups</h2>
      $${renderNetwork(DASHBOARD.network || [])}
    </section>

    <section class="section">
      <h2>Secrets — references and Secrets Manager</h2>
      $${renderSecretRefs(DASHBOARD.secret_refs || {})}
    </section>

    <section class="section">
      <h2>Application manifests — rendered from CUE</h2>
      $${renderRenderedApps(DASHBOARD.rendered_apps || {}, DASHBOARD.app_drift || {})}
    </section>

    <section class="section">
      <h2>CUE schemas</h2>
      $${renderCueSchemas(DASHBOARD.cue_schemas || {})}
    </section>

    <section class="section">
      <h2>CI/CD — workflows by module</h2>
      $${renderWorkflows(DASHBOARD.workflows || {})}
    </section>

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
    /* Render the Compute → Data → Identity flow strip on the dashboard. */
    const flowEl = document.getElementById("stack-flow-mmd");
    if (flowEl) {
      try {
        const p = mermaid.run({ nodes: [flowEl] });
        if (p && typeof p.then === "function") p.catch((e) => console.warn("mermaid flow", e));
      } catch (e2) { console.warn("mermaid flow run", e2); }
    }
  } catch (e) { console.warn("mermaid", e); }

  /* Charts + category-card click-to-expand wiring. */
  try { renderDashboardCharts(DASHBOARD.categories || {}); }
  catch (e) { console.warn("charts", e); }
  /* v0.35.0: generic click-to-copy for any .kbd-copy button rendered on
   * the dashboard (render_apps.py / diff_apps.py prompts, etc.) */
  document.querySelectorAll(".kbd-copy").forEach(btn => {
    if (btn.__wired) return;
    btn.__wired = true;
    btn.addEventListener("click", async () => {
      const text = btn.getAttribute("data-copy") || "";
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        const tick = btn.querySelector(".kbd-tick");
        if (tick) {
          const prev = tick.textContent;
          tick.textContent = "copied ✓";
          setTimeout(() => { tick.textContent = prev || "copy"; }, 1600);
        }
      } catch (e) {
        const r = document.createRange(); r.selectNodeContents(btn);
        const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      }
    });
  });
  document.querySelectorAll(".cat-card").forEach(card => {
    const head = card.querySelector(".cat-head");
    if (!head) return;
    head.addEventListener("click", () => {
      const open = card.getAttribute("data-open") === "true";
      card.setAttribute("data-open", open ? "false" : "true");
    });
  });

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

/* --- View switching & 3D force-directed graph (v0.34.0) --------------- *
 * The Graph view used to render via Cytoscape (2D). v0.34.0 swaps in
 * `3d-force-graph` (three.js + d3-force-3d) so the stack reads as a
 * floating gas/neural sphere with real space between nodes. The
 * surrounding UI (topbar, layer toggles, search, sidebar, dashboard) is
 * unchanged — only the rendering engine and click/highlight wiring
 * moved.                                                                  */
let Graph3D = null;            /* the ForceGraph3D instance */
let GRAPH_STATE = null;        /* current filter/search state cached for re-render */

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function setView(mode) {
  document.body.classList.toggle("view-dashboard", mode === "dashboard");
  document.body.classList.toggle("view-graph", mode === "graph");
  document.getElementById("tab-dashboard").classList.toggle("active", mode === "dashboard");
  document.getElementById("tab-graph").classList.toggle("active", mode === "graph");
  if (mode === "graph") {
    if (!Graph3D) buildGraph3D();
    requestAnimationFrame(() => {
      if (!Graph3D) return;
      const w = document.getElementById("graph-3d").clientWidth;
      const h = document.getElementById("graph-3d").clientHeight;
      Graph3D.width(w).height(h);
      Graph3D.zoomToFit(600, 80);
    });
  }
}

document.getElementById("tab-dashboard").addEventListener("click", () => setView("dashboard"));
document.getElementById("tab-graph").addEventListener("click", () => setView("graph"));

function buildGraph3D() {
  const _root = getComputedStyle(document.documentElement);
  const _v = name => _root.getPropertyValue(name).trim();
  const BRAND = {
    bg: _v("--bg"), ink: _v("--ink"),
    inkMute: "rgba(255,255,255,0.65)", inkFaint: "rgba(255,255,255,0.45)",
    inkLine: "rgba(255,255,255,0.10)", inkLineHi: "rgba(255,255,255,0.18)",
    blue: _v("--blue"), blueSoft: _v("--blue-soft"),
    aws: _v("--aws"), amber: _v("--amber"), amberWarm: _v("--amber-warm"),
  };
  const LAYER_COLORS = {
    static: _v("--blue") || "#1677ff",
    state:  _v("--aws")  || "#ff9900",
    k8s:    _v("--k8s-red") || "#e44d4d",
    docs:   "rgba(255,255,255,0.65)",
  };
  const HIGHLIGHT = _v("--blue-soft") || "#3c89e8";
  const DIM_COLOR = "rgba(120,120,140,0.18)";

  /* --- Flatten cytoscape-shaped NODES/EDGES into 3d-force-graph format ---
   *   NODES item:  { data: { id, label, type, source_layer, attrs, ... }, classes: "static" }
   *   EDGES item:  { data: { id, source, target, relation } }
   * 3d-force-graph wants: { nodes: [{id, ...}], links: [{source: id|node, target: id|node, ...}] }
   *
   * We keep ALL_LINKS_RAW (with string source/target) for sidebar lookups,
   * because 3d-force-graph mutates `link.source` / `link.target` to the
   * actual node objects after first render.
   */
  const ALL_NODES = NODES
    .filter(n => n.data && !n.data.compound)
    .map(n => ({
      id: n.data.id,
      label: n.data.label || n.data.id,
      type: n.data.type || "",
      source_layer: n.data.source_layer || (n.classes || "static").split(" ")[0] || "static",
      attrs: n.data.attrs || {},
    }));
  const NODE_BY_ID = new Map(ALL_NODES.map(n => [n.id, n]));
  const ALL_LINKS_RAW = EDGES
    .filter(e => e.data && NODE_BY_ID.has(e.data.source) && NODE_BY_ID.has(e.data.target))
    .map(e => ({
      source: e.data.source,
      target: e.data.target,
      relation: e.data.relation || "",
    }));

  /* incoming / outgoing indices for sidebar + blast — keyed by node id. */
  const IN_BY_ID = new Map(); const OUT_BY_ID = new Map();
  for (const id of NODE_BY_ID.keys()) { IN_BY_ID.set(id, []); OUT_BY_ID.set(id, []); }
  for (const l of ALL_LINKS_RAW) {
    OUT_BY_ID.get(l.source).push(l);
    IN_BY_ID.get(l.target).push(l);
  }

  /* --- View mode (overview = modules-only, full = everything) ---------- */
  const viewSel = document.getElementById("graph-view-mode");
  let viewMode = "full";
  if (viewSel) {
    try {
      const saved = sessionStorage.getItem("kuberlyGraphView");
      if (saved === "overview" || saved === "full") viewSel.value = saved;
      else if (ALL_NODES.length >= 280) viewSel.value = "overview";
    } catch (e) { /* private mode */ }
    viewMode = viewSel.value || "full";
  }

  /* v0.34.2: filter sets — empty = no filter, populated = whitelist of values
   * the node attribute must match. groupBy drives both color and cluster. */
  GRAPH_STATE = {
    layers: { static: true, state: true, k8s: false, docs: true },
    viewMode,
    search: "",
    selectedId: null,
    blast: null, /* {upstream:Set, downstream:Set, edges:Set, focus:id} */
    groupBy: "source_layer",
    filters: {
      envs:    new Set(),
      modules: new Set(),
      rtypes:  new Set(),  /* resource_type from attrs */
      ntypes:  new Set(),  /* node type — module / component / app / resource / k8s_resource / doc */
    },
  };

  /* Resolve a node's value for the current group-by axis. Used by both
   * `nodeColorFn` (color picking) and the cluster force (centroid placement). */
  function groupKey(node) {
    const k = GRAPH_STATE.groupBy;
    if (k === "source_layer") return node.source_layer || "?";
    if (k === "type")         return node.type || "?";
    if (k === "environment")  return (node.attrs && node.attrs.environment) || node.environment || "—";
    if (k === "resource_type")return (node.attrs && node.attrs.resource_type) || node.resource_type || "—";
    if (k === "module")       return (node.attrs && node.attrs.module) || node.module || "—";
    if (k === "provider")     return (node.attrs && node.attrs.provider) || node.provider || "—";
    return "?";
  }
  /* Fast read of common per-node fields irrespective of where attrs live —
   * applies to both 3d-force-graph node objects (flat) and the original
   * cytoscape-shaped data (nested under .attrs). */
  function nodeAttr(node, key) {
    if (!node) return "";
    if (node[key]) return node[key];
    if (node.attrs && node.attrs[key]) return node.attrs[key];
    return "";
  }

  function pickNodesForView(mode) {
    if (mode !== "overview") return ALL_NODES;
    const mods = ALL_NODES.filter(n => n.type === "module");
    return mods.length >= 2 ? mods : ALL_NODES;
  }

  function passesFilters(n) {
    const f = GRAPH_STATE.filters;
    if (f.envs.size    && !f.envs.has(nodeAttr(n, "environment") || "—")) return false;
    if (f.modules.size && !f.modules.has(nodeAttr(n, "module") || "—"))   return false;
    if (f.rtypes.size  && !f.rtypes.has(nodeAttr(n, "resource_type") || "—")) return false;
    if (f.ntypes.size  && !f.ntypes.has(n.type || "—"))                    return false;
    return true;
  }

  function currentGraphData() {
    const baseNodes = pickNodesForView(GRAPH_STATE.viewMode);
    const layers = GRAPH_STATE.layers;
    const visibleNodes = baseNodes.filter(n =>
      layers[n.source_layer] !== false && passesFilters(n));
    const visIds = new Set(visibleNodes.map(n => n.id));
    const links = ALL_LINKS_RAW.filter(l => {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      if (!visIds.has(s) || !visIds.has(t)) return false;
      if (GRAPH_STATE.viewMode === "overview" && l.relation !== "depends_on") return false;
      return true;
    }).map(l => ({
      source: typeof l.source === "object" ? l.source.id : l.source,
      target: typeof l.target === "object" ? l.target.id : l.target,
      relation: l.relation,
    }));
    return { nodes: visibleNodes, links };
  }

  function refreshStats() {
    const { nodes, links } = currentGraphData();
    const parts = [nodes.length + " nodes", links.length + " edges"];
    if (GRAPH_STATE.viewMode === "overview") parts.push("overview");
    document.getElementById("stats").textContent = parts.join(" · ");
    const host = document.getElementById("graph-3d");
    if (host) host.classList.toggle("is-empty", nodes.length === 0);
  }
  refreshStats();

  /* v0.34.2: when grouped by an axis other than source_layer, derive a
   * stable color per group key from the SPIKE_PALETTE. source_layer keeps
   * the original 4-layer brand colors. */
  const GROUP_PALETTE = ["#1677ff", "#ff9900", "#a266ff", "#39c47a", "#3c89e8",
                         "#f5b042", "#5fd098", "#7c5cff", "#22a1c4", "#ff5e9c",
                         "#9aff5e", "#ffd700"];
  const _groupColorMemo = new Map();
  function groupColor(key) {
    if (GRAPH_STATE.groupBy === "source_layer") {
      return LAYER_COLORS[key] || "#999";
    }
    if (_groupColorMemo.has(key)) return _groupColorMemo.get(key);
    /* Stable hash → palette index so colors don't shuffle across renders. */
    let h = 0; const s = String(key || "");
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    const c = GROUP_PALETTE[Math.abs(h) % GROUP_PALETTE.length];
    _groupColorMemo.set(key, c);
    return c;
  }

  /* --- Color resolution: group color, dim if blast/search active and not
       in the highlighted set, bright if matched. ------- */
  function nodeColorFn(node) {
    const id = node.id;
    if (GRAPH_STATE.search) {
      const q = GRAPH_STATE.search;
      const hit = id.toLowerCase().includes(q) || (node.label || "").toLowerCase().includes(q);
      if (hit) return HIGHLIGHT;
      return DIM_COLOR;
    }
    if (GRAPH_STATE.blast) {
      const b = GRAPH_STATE.blast;
      if (id === b.focus) return HIGHLIGHT;
      if (b.upstream.has(id)) return LAYER_COLORS.state;
      if (b.downstream.has(id)) return LAYER_COLORS.static;
      return DIM_COLOR;
    }
    return groupColor(groupKey(node));
  }
  function linkColorFn(link) {
    if (GRAPH_STATE.blast) {
      const sId = typeof link.source === "object" ? link.source.id : link.source;
      const tId = typeof link.target === "object" ? link.target.id : link.target;
      const b = GRAPH_STATE.blast;
      if (b.edges.has(sId + "|" + tId)) return HIGHLIGHT;
      return "rgba(255,255,255,0.04)";
    }
    return "rgba(255,255,255,0.10)";
  }

  /* --- v0.34.1 visuals: bolder nodes/links + neural-spike particles ----- *
   *   Per-link "spike" color picked from a vibrant neon palette using a
   *   stable hash of the endpoints — different pathways glow differently.
   *   A global pulse driven by Date.now() modulates particle width every
   *   frame so the network reads as constantly firing. The cluster force
   *   pulls each source_layer toward its own offset in 3D space so the
   *   floating shape resolves into 3-4 lobes instead of one fuzzy ball. */
  const SPIKE_PALETTE = [
    "#00f5ff", "#3c89e8", "#a266ff", "#ff5e9c",
    "#ffd700", "#ff8b3d", "#9aff5e", "#5fd098",
  ];
  function _hashStr(s) {
    let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  }
  function spikeColorFn(link) {
    const sid = typeof link.source === "object" ? link.source.id : link.source;
    const tid = typeof link.target === "object" ? link.target.id : link.target;
    return SPIKE_PALETTE[_hashStr((sid || "") + "|" + (tid || "")) % SPIKE_PALETTE.length];
  }
  /* v0.34.2: cluster offsets are recomputed whenever GRAPH_STATE.groupBy
   * changes, so swapping "Group by" reflows the layout. Returns
   * { offsets: {key→{x,y,z}}, cluster: bool }. cluster=false skips the
   * d3 cluster force entirely (large-N groups would just produce a wide
   * spread anyway). Limit: only cluster if 2 ≤ N ≤ 12. */
  let CLUSTER_OFFSETS = {};
  let CLUSTER_ACTIVE = true;
  function recomputeClusterOffsets() {
    const data = (Graph3D && Graph3D.graphData()) || { nodes: [] };
    const keys = Array.from(new Set((data.nodes || []).map(groupKey)));
    keys.sort();  /* stable ordering across reflows */
    const N = keys.length;
    CLUSTER_OFFSETS = {};
    CLUSTER_ACTIVE = N >= 2 && N <= 12;
    if (!CLUSTER_ACTIVE) return;
    const R = N <= 4 ? 280 : N <= 8 ? 320 : 360;
    keys.forEach((k, i) => {
      const a = (i / N) * 2 * Math.PI;
      CLUSTER_OFFSETS[k] = {
        x: R * Math.cos(a),
        y: R * Math.sin(a) * 0.7,
        z: ((i % 2) ? -50 : 50),
      };
    });
  }

  /* --- Mount ForceGraph3D --------------------------------------------- */
  const host = document.getElementById("graph-3d");
  Graph3D = ForceGraph3D({ controlType: "orbit" })(host)
    .backgroundColor(BRAND.bg)
    .width(host.clientWidth)
    .height(host.clientHeight)
    .nodeId("id")
    .nodeLabel(n => `<div style="font-family:Geist,system-ui,sans-serif;font-size:12px;padding:6px 8px;background:rgba(20,24,30,0.95);border:1px solid rgba(255,255,255,0.18);border-radius:6px;color:#fff;">$${escapeHtml(n.label || n.id)}<br><span style="opacity:0.6;font-family:JetBrains Mono,ui-monospace,monospace;font-size:10px;">$${escapeHtml(n.type || "")} · $${escapeHtml(n.source_layer)}</span></div>`)
    .nodeVal(n => 2 + Math.min(((OUT_BY_ID.get(n.id) || []).length + (IN_BY_ID.get(n.id) || []).length) * 0.5, 16))
    .nodeRelSize(7)
    .nodeOpacity(1.0)
    .nodeColor(nodeColorFn)
    .nodeResolution(14)
    .linkColor(linkColorFn)
    .linkOpacity(0.75)
    .linkWidth(1.4)
    .linkDirectionalParticles(2)
    .linkDirectionalParticleSpeed(0.006)
    .linkDirectionalParticleWidth(2.5)
    .linkDirectionalParticleColor(spikeColorFn)
    .enableNodeDrag(true)
    .cooldownTime(20000)
    .warmupTicks(80)
    .onNodeClick(n => {
      GRAPH_STATE.selectedId = n.id;
      renderSidebar(n);
      Graph3D.centerAt(n.x, n.y, 600);
      Graph3D.cameraPosition({ x: n.x, y: n.y, z: (n.z || 0) + 220 }, n, 1000);
    })
    .onBackgroundClick(() => {
      GRAPH_STATE.selectedId = null;
      GRAPH_STATE.blast = null;
      document.getElementById("sidebar").classList.remove("open");
      Graph3D.nodeColor(nodeColorFn).linkColor(linkColorFn);
    });

  /* d3-force-3d tuning + a custom per-layer cluster force. Higher charge
     and shorter link distance pack each cluster densely, then the cluster
     force shoves each source_layer toward its own attractor. */
  if (Graph3D.d3Force) {
    const charge = Graph3D.d3Force("charge");
    if (charge && charge.strength) charge.strength(-380);
    const link = Graph3D.d3Force("link");
    if (link && link.distance) link.distance(38);
    const clusterForce = (alpha) => {
      if (!CLUSTER_ACTIVE) return;
      const data = Graph3D.graphData();
      if (!data || !data.nodes) return;
      data.nodes.forEach(n => {
        const t = CLUSTER_OFFSETS[groupKey(n)];
        if (!t) return;
        const k = alpha * 0.18;
        if (typeof n.x === "number") n.vx = (n.vx || 0) + (t.x - n.x) * k;
        if (typeof n.y === "number") n.vy = (n.vy || 0) + (t.y - n.y) * k;
        if (typeof n.z === "number") n.vz = (n.vz || 0) + (t.z - n.z) * k;
      });
    };
    Graph3D.d3Force("cluster", clusterForce);
  }

  Graph3D.graphData(currentGraphData());
  recomputeClusterOffsets();

  /* Global neural-firing pulse: every ~1.6s nudge particle width up and
     back so the whole network looks like it's firing in waves. We also
     periodically rotate the particle color palette by re-applying the
     spike color fn so links shift their "channel" over time.  */
  if (window.__kuberlyPulse) clearInterval(window.__kuberlyPulse);
  let _pulse = 0;
  window.__kuberlyPulse = setInterval(() => {
    if (!Graph3D) return;
    if (!document.body.classList.contains("view-graph")) return;
    _pulse += 1;
    const w = 2.0 + Math.sin(_pulse * 0.35) * 1.4;
    Graph3D.linkDirectionalParticleWidth(Math.max(1.2, w));
    /* Every 4th tick, rotate spike palette so links flash different colors. */
    if (_pulse % 4 === 0) {
      Graph3D.linkDirectionalParticleColor(link => {
        const sid = typeof link.source === "object" ? link.source.id : link.source;
        const tid = typeof link.target === "object" ? link.target.id : link.target;
        return SPIKE_PALETTE[(_hashStr((sid || "") + "|" + (tid || "")) + _pulse) % SPIKE_PALETTE.length];
      });
    }
  }, 220);

  /* Camera fly: sit closer than zoomToFit's default so the cluster fills
     the view from the start. The cluster force settles things over ~20s,
     by which time the user can scroll out / orbit if they want a wide. */
  setTimeout(() => {
    if (Graph3D) {
      Graph3D.cameraPosition({ x: 0, y: 0, z: 520 }, { x: 0, y: 0, z: 0 }, 1200);
    }
  }, 400);

  /* --- Sidebar / search / layer toggle / blast wiring ------------------ */
  const searchEl = document.getElementById("search");
  const sidebar = document.getElementById("sidebar");
  const sidebarBody = document.getElementById("sidebar-body");

  function applyDataAndRefresh() {
    refreshStats();
    Graph3D.graphData(currentGraphData());
    /* Force color refresh for the (possibly) new data set. */
    Graph3D.nodeColor(nodeColorFn).linkColor(linkColorFn);
    recomputeClusterOffsets();
    /* Reheat sim so nodes migrate to new cluster centroids. */
    if (Graph3D.d3ReheatSimulation) Graph3D.d3ReheatSimulation();
  }
  /* v0.34.6: expose a focused-filter helper so the dashboard's
   * architecture-diagram tiles can switch the Graph view to a single
   * resource_type filter without reaching into closure-scoped helpers. */
  window.__kuberlyFilterByResourceType = function (rtype) {
    if (!GRAPH_STATE || !rtype) return;
    GRAPH_STATE.filters.rtypes.clear();
    GRAPH_STATE.filters.rtypes.add(rtype);
    document.querySelectorAll('#fp-rtypes .fp-chip').forEach(c => {
      c.classList.toggle("on", c.dataset.value === rtype);
    });
    applyDataAndRefresh();
  };

  /* v0.34.2: build the filter-panel chip lists from ALL_NODES values.
   * Each section: a flex-wrap of chip elements; click toggles membership
   * in the GRAPH_STATE.filters set for that axis. Counts shown after the
   * label tell the user how big each bucket is. */
  function buildFilterPanel() {
    function tally(getter) {
      const m = new Map();
      ALL_NODES.forEach(n => {
        const v = getter(n);
        if (!v) return;
        m.set(v, (m.get(v) || 0) + 1);
      });
      return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
    }
    const sections = [
      { id: "fp-envs",    set: "envs",
        rows: tally(n => nodeAttr(n, "environment") || "") },
      { id: "fp-modules", set: "modules",
        rows: tally(n => nodeAttr(n, "module") || "") },
      { id: "fp-rtypes",  set: "rtypes",
        rows: tally(n => nodeAttr(n, "resource_type") || "") },
      { id: "fp-ntypes",  set: "ntypes",
        rows: tally(n => n.type || "") },
    ];
    sections.forEach(sec => {
      const host = document.getElementById(sec.id);
      if (!host) return;
      host.innerHTML = "";
      sec.rows.slice(0, 200).forEach(([val, count]) => {
        const chip = document.createElement("span");
        chip.className = "fp-chip";
        chip.dataset.set = sec.set;
        chip.dataset.value = val;
        chip.innerHTML = `$${escapeHtml(val)}<span class="ct">$${count}</span>`;
        chip.addEventListener("click", () => {
          const s = GRAPH_STATE.filters[sec.set];
          if (s.has(val)) s.delete(val); else s.add(val);
          chip.classList.toggle("on", s.has(val));
          applyDataAndRefresh();
        });
        host.appendChild(chip);
      });
    });
  }
  buildFilterPanel();

  function resetFilters() {
    Object.values(GRAPH_STATE.filters).forEach(s => s.clear());
    GRAPH_STATE.search = "";
    GRAPH_STATE.blast = null;
    GRAPH_STATE.selectedId = null;
    const sEl = document.getElementById("search");
    if (sEl) sEl.value = "";
    document.querySelectorAll("#graph-filter-panel .fp-chip.on")
      .forEach(c => c.classList.remove("on"));
    sidebar.classList.remove("open");
    applyDataAndRefresh();
    /* Recenter camera on the recomputed cluster. */
    if (Graph3D) Graph3D.cameraPosition({ x: 0, y: 0, z: 520 }, { x: 0, y: 0, z: 0 }, 800);
  }

  function computeBlast(focusId) {
    const upstream = new Set(); const downstream = new Set();
    const edges = new Set();
    const visit = (start, dir) => {
      const queue = [start]; const seen = new Set([start]);
      while (queue.length) {
        const id = queue.shift();
        const neighbors = (dir === "up" ? IN_BY_ID : OUT_BY_ID).get(id) || [];
        for (const l of neighbors) {
          const s = typeof l.source === "object" ? l.source.id : l.source;
          const t = typeof l.target === "object" ? l.target.id : l.target;
          edges.add(s + "|" + t);
          const next = dir === "up" ? s : t;
          if (!seen.has(next)) { seen.add(next); queue.push(next); (dir === "up" ? upstream : downstream).add(next); }
        }
      }
    };
    visit(focusId, "up"); visit(focusId, "down");
    return { upstream, downstream, edges, focus: focusId };
  }

  function renderSidebar(node) {
    const layer = node.source_layer || "static";
    const incoming = IN_BY_ID.get(node.id) || [];
    const outgoing = OUT_BY_ID.get(node.id) || [];
    const attrs = node.attrs || {};
    const attrEntries = Object.entries(attrs).filter(([k]) => k !== "label" && k !== "id");
    const attrHtml = attrEntries.length
      ? `<details $${attrEntries.length <= 4 ? "open" : ""}><summary>$${attrEntries.length} attribute$${attrEntries.length === 1 ? "" : "s"}</summary><div class="attrs">`
        + attrEntries.map(([k, v]) => {
            const vs = typeof v === "object" ? JSON.stringify(v) : String(v);
            return `<div><span class="k">$${escapeHtml(k)}:</span> <span class="v">$${escapeHtml(vs)}</span></div>`;
          }).join("")
        + `</div></details>`
      : "";
    const linkRow = (otherId, rel) =>
      `<a href="#" data-jump="$${escapeHtml(otherId)}">$${escapeHtml(otherId)}<span class="rel">[$${escapeHtml(rel)}]</span></a>`;
    const inHtml = incoming.length
      ? incoming.map(l => linkRow(typeof l.source === "object" ? l.source.id : l.source, l.relation)).join("")
      : `<div class="rel">none</div>`;
    const outHtml = outgoing.length
      ? outgoing.map(l => linkRow(typeof l.target === "object" ? l.target.id : l.target, l.relation)).join("")
      : `<div class="rel">none</div>`;
    sidebarBody.innerHTML = `
      <h2>$${escapeHtml(node.id)}</h2>
      <div class="chips">
        $${node.type ? `<span class="chip">$${escapeHtml(node.type)}</span>` : ""}
        <span class="chip layer-$${escapeHtml(layer)}">$${escapeHtml(layer)}</span>
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
        const target = NODE_BY_ID.get(a.dataset.jump);
        if (!target) return;
        const live = (Graph3D.graphData().nodes || []).find(n => n.id === target.id) || target;
        GRAPH_STATE.selectedId = target.id;
        renderSidebar(target);
        if (typeof live.x === "number") {
          Graph3D.centerAt(live.x, live.y, 600);
          Graph3D.cameraPosition({ x: live.x, y: live.y, z: (live.z || 0) + 220 }, live, 1000);
        }
      });
    });
    document.getElementById("blast-btn").addEventListener("click", () => {
      GRAPH_STATE.blast = computeBlast(node.id);
      Graph3D.nodeColor(nodeColorFn).linkColor(linkColorFn);
    });
    document.getElementById("center-btn").addEventListener("click", () => {
      const live = (Graph3D.graphData().nodes || []).find(n => n.id === node.id) || node;
      if (typeof live.x === "number") {
        Graph3D.centerAt(live.x, live.y, 600);
        Graph3D.cameraPosition({ x: live.x, y: live.y, z: (live.z || 0) + 220 }, live, 1000);
      }
    });
  }

  if (!window.__kuberlyGraphUiWired) {
    window.__kuberlyGraphUiWired = true;
    document.querySelectorAll("#graph-controls .layer-toggles input").forEach(cb => {
      cb.addEventListener("change", () => {
        if (!GRAPH_STATE) return;
        GRAPH_STATE.layers[cb.dataset.layer] = !!cb.checked;
        const pill = cb.closest(".layer-toggle");
        if (pill) {
          pill.classList.toggle("active", cb.checked);
          pill.classList.toggle("inactive", !cb.checked);
        }
        applyDataAndRefresh();
      });
    });
    if (viewSel) {
      viewSel.addEventListener("change", () => {
        try { sessionStorage.setItem("kuberlyGraphView", viewSel.value); } catch (e) {}
        if (!GRAPH_STATE) return;
        GRAPH_STATE.viewMode = viewSel.value || "full";
        GRAPH_STATE.blast = null;
        applyDataAndRefresh();
      });
    }
    /* v0.34.2: Group-by selector — color + cluster axis. */
    const groupSel = document.getElementById("graph-group-by");
    if (groupSel) {
      groupSel.addEventListener("change", () => {
        if (!GRAPH_STATE) return;
        GRAPH_STATE.groupBy = groupSel.value || "source_layer";
        applyDataAndRefresh();
      });
    }
    /* v0.34.2/v0.34.3: Filters panel open/close.
     * Topbar is flex-wrap so its height isn't fixed — measure on open and
     * pin the panel to the actual bottom edge. ESC + click-outside +
     * dedicated × button all close it. */
    const filterToggle = document.getElementById("graph-filters-toggle");
    const filterPanel  = document.getElementById("graph-filter-panel");
    const filterCloseBtn = document.getElementById("graph-filter-close");
    function closeFilterPanel() {
      if (!filterPanel) return;
      filterPanel.setAttribute("hidden", "");
      if (filterToggle) filterToggle.setAttribute("aria-expanded", "false");
    }
    function openFilterPanel() {
      if (!filterPanel) return;
      const bar = document.getElementById("topbar");
      if (bar) {
        const r = bar.getBoundingClientRect();
        filterPanel.style.top = Math.max(56, Math.round(r.bottom)) + "px";
      }
      filterPanel.removeAttribute("hidden");
      if (filterToggle) filterToggle.setAttribute("aria-expanded", "true");
    }
    if (filterToggle && filterPanel) {
      filterToggle.addEventListener("click", () => {
        const isOpen = !filterPanel.hasAttribute("hidden");
        if (isOpen) closeFilterPanel(); else openFilterPanel();
      });
    }
    if (filterCloseBtn) filterCloseBtn.addEventListener("click", closeFilterPanel);
    /* Click outside the panel closes it (but not when clicking the toggle —
     * its own handler already toggles state). */
    document.addEventListener("mousedown", e => {
      if (!filterPanel || filterPanel.hasAttribute("hidden")) return;
      if (filterPanel.contains(e.target)) return;
      if (filterToggle && filterToggle.contains(e.target)) return;
      closeFilterPanel();
    });
    /* If the topbar reflows (window resize, font load, layout settle),
     * keep the panel pinned to the new bottom edge while it's open. */
    window.addEventListener("resize", () => {
      if (filterPanel && !filterPanel.hasAttribute("hidden")) openFilterPanel();
    });
    const resetBtn = document.getElementById("graph-reset");
    if (resetBtn) resetBtn.addEventListener("click", resetFilters);
    searchEl.addEventListener("input", () => {
      if (!GRAPH_STATE) return;
      GRAPH_STATE.search = searchEl.value.trim().toLowerCase();
      Graph3D.nodeColor(nodeColorFn).linkColor(linkColorFn);
    });
    searchEl.addEventListener("keydown", e => {
      if (e.key !== "Enter" || !Graph3D) return;
      const q = (searchEl.value || "").trim().toLowerCase();
      if (!q) return;
      const data = Graph3D.graphData();
      const hit = (data.nodes || []).find(n =>
        n.id.toLowerCase().includes(q) || (n.label || "").toLowerCase().includes(q));
      if (hit && typeof hit.x === "number") {
        Graph3D.centerAt(hit.x, hit.y, 600);
        Graph3D.cameraPosition({ x: hit.x, y: hit.y, z: (hit.z || 0) + 220 }, hit, 1000);
      }
    });
    document.getElementById("close-btn").addEventListener("click", () => {
      sidebar.classList.remove("open");
      if (GRAPH_STATE) { GRAPH_STATE.blast = null; GRAPH_STATE.selectedId = null; }
      if (Graph3D) Graph3D.nodeColor(nodeColorFn).linkColor(linkColorFn);
    });
    document.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      if (!document.body.classList.contains("view-graph")) return;
      /* Filter panel closes first if open — ESC twice clears search/blast. */
      if (filterPanel && !filterPanel.hasAttribute("hidden")) {
        closeFilterPanel();
        return;
      }
      sidebar.classList.remove("open");
      if (GRAPH_STATE) {
        GRAPH_STATE.blast = null;
        GRAPH_STATE.selectedId = null;
        GRAPH_STATE.search = "";
      }
      searchEl.value = "";
      if (Graph3D) Graph3D.nodeColor(nodeColorFn).linkColor(linkColorFn);
    });
    let __kuberlyResizeTimer = null;
    window.addEventListener("resize", () => {
      if (!Graph3D) return;
      if (!document.body.classList.contains("view-graph")) return;
      clearTimeout(__kuberlyResizeTimer);
      __kuberlyResizeTimer = setTimeout(() => {
        const w = host.clientWidth, h = host.clientHeight;
        Graph3D.width(w).height(h);
      }, 80);
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  renderDashboard();
});
</script>
</body>
</html>
"""
