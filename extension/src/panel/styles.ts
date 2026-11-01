// Scoped to the extension's Shadow DOM. Uses GitHub's light/dark palettes.
export const css = `
:host { all: initial; }
.root {
  --bg: #fff; --fg: #1f2328; --muted: #59636e; --line: #d1d9e0; --soft: #f6f8fa; --hover: #eff2f5;
  --ok: #1a7f37; --bad: #d1242f; --warn: #9a6700; --accent: #0969da;
  position: fixed; right: 16px; bottom: 16px; z-index: 2147483000;
  width: min(470px, calc(100vw - 32px)); max-height: min(86vh, 900px);
  display: flex; flex-direction: column; overflow: hidden;
  background: var(--bg); color: var(--fg); border: 1px solid var(--line); border-radius: 14px;
  box-shadow: 0 12px 40px rgba(31,35,40,.18);
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif;
  text-align: left; overflow-wrap: anywhere; color-scheme: light;
}
.root.dark {
  --bg: #151b23; --fg: #f0f6fc; --muted: #a0a8b3; --line: #3d444d; --soft: #1c232d; --hover: #262c36;
  --ok: #3fb950; --bad: #ff7b72; --warn: #d29922; --accent: #79b8ff;
  color-scheme: dark; box-shadow: 0 12px 40px rgba(1,4,9,.6);
}
*, *::before, *::after { box-sizing: border-box; }
.root.collapsed { width: auto; max-width: calc(100vw - 32px); }
button { font: inherit; color: inherit; }
button, a, summary { -webkit-tap-highlight-color: transparent; }
button { cursor: pointer; }
button:disabled { opacity: .4; cursor: default; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
p, h2, h3, h4, dl, dd { margin: 0; }
h3 { font-size: 14px; font-weight: 650; } h4 { font-size: 12px; font-weight: 650; }
ul, ol { margin: 0; padding-left: 20px; } li + li { margin-top: 5px; }
code { font: .91em/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background: var(--soft); border-radius: 3px; padding: 1px 3px; }
.head { display: flex; align-items: center; gap: 7px; padding: 11px 12px 11px 16px; border-bottom: 1px solid var(--line); flex: none; }
.brand-mark { width: 22px; height: 24px; display: grid; place-items: center; background: var(--fg); color: var(--bg); border-radius: 6px; font-size: 14px; font-weight: 800; flex: none; }
.title { font-size: 12px; font-weight: 750; letter-spacing: .06em; }
.spacer { flex: 1; }
.run-state { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); white-space: nowrap; }
.run-state.partial { color: var(--warn); } .run-state.error { color: var(--bad); }
.iconbtn { background: none; border: 0; color: var(--muted); width: 26px; height: 28px; border-radius: 6px; display: grid; place-items: center; font-size: 16px; flex: none; }
.iconbtn:hover:not(:disabled) { background: var(--hover); color: var(--fg); }
.body { overflow-y: auto; overflow-x: hidden; min-height: 0; overscroll-behavior: contain; scrollbar-gutter: stable; }
.summary { padding: 18px 20px; display: flex; flex-direction: column; gap: 10px; }
.target { font-size: 12px; color: var(--muted); } .target a { color: inherit; }
.finding-label { display: flex; gap: 7px; align-items: center; font-size: 12px; font-weight: 650; }
.finding-label.ok { color: var(--ok); } .finding-label.bad { color: var(--bad); } .finding-label.warn { color: var(--warn); } .finding-label.neu { color: var(--muted); }
h2 { font-size: 20px; font-weight: 650; line-height: 1.35; letter-spacing: -.02em; }
.dot { width: 15px; height: 15px; border-radius: 50%; flex: none; display: inline-grid; place-items: center; font-size: 10px; font-weight: 750; color: var(--bg); line-height: 1; }
.dot.ok { background: var(--ok); } .dot.bad { background: var(--bad); } .dot.warn { background: var(--warn); } .dot.neu { background: var(--muted); }
.scope-grid .suggested-label { grid-column: 1 / -1; }
.scope-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding-top: 12px; margin-top: 2px; border-top: 1px solid var(--line); font-size: 12px; }
dt, .field-label { color: var(--muted); font-size: 11px; } .scope-grid dd:not(.note) { font-weight: 550; margin-top: 2px; }
.mockbar { margin: 14px 20px 0; padding: 8px 10px; border-radius: 6px; font-size: 12px; color: var(--warn); background: color-mix(in srgb, var(--warn) 8%, var(--bg)); border: 1px solid color-mix(in srgb, var(--warn) 35%, var(--line)); }
.attention { padding: 12px 20px; border-top: 1px solid color-mix(in srgb, var(--warn) 35%, var(--line)); background: color-mix(in srgb, var(--warn) 7%, var(--bg)); }
.attention h3 { color: var(--warn); margin-bottom: 3px; } .attention p { font-size: 12px; }
.section { padding: 16px 20px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; }
.section-heading { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.count { display: inline-block; border-radius: 20px; padding: 0 6px; font-size: 11px; color: var(--muted); font-weight: 550; background: var(--hover); margin-left: 4px; }
.filters { display: flex; flex-wrap: wrap; gap: 5px; }
.filter { border: 1px solid var(--line); border-radius: 6px; padding: 4px 7px; background: var(--bg); color: var(--muted); font-size: 11px; }
.filter span { margin-left: 2px; font-variant-numeric: tabular-nums; }
.filter:hover { background: var(--hover); } .filter[aria-pressed="true"] { color: var(--fg); border-color: var(--fg); background: var(--soft); font-weight: 650; }
.filter.supported span { color: var(--ok); } .filter.contradicted span { color: var(--bad); }
.claim { border: 1px solid var(--line); border-radius: 9px; overflow: hidden; }
.claim.primary { border-color: color-mix(in srgb, var(--accent) 40%, var(--line)); }
.claim-row { display: flex; gap: 10px; align-items: flex-start; width: 100%; text-align: left; background: none; border: 0; padding: 12px; }
.claim-row:hover { background: var(--soft); }
.claim-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 7px; }
.claim-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.eyebrow { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; font-weight: 650; }
.primary .eyebrow { color: var(--accent); }
.badge { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 600; }
.badge.ok { color: var(--ok); } .badge.bad { color: var(--bad); } .badge.neu { color: var(--muted); }
.claim-text { font-weight: 550; font-size: 14px; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.open .claim-text { display: block; -webkit-line-clamp: unset; }
.caret { color: var(--muted); flex: none; margin-top: 1px; transition: transform .15s; } .open .caret { transform: rotate(180deg); }
.detail { padding: 0 12px 12px; display: flex; flex-direction: column; gap: 12px; font-size: 13px; }
.expl { border-top: 1px solid var(--line); padding-top: 12px; }
.verification { color: var(--muted); font-size: 12px; } .field-label { margin-right: 6px; }
.evidence-block, .limitations { display: flex; flex-direction: column; gap: 7px; }
.ev-list { display: flex; flex-direction: column; gap: 8px; }
.ev { border-left: 2px solid var(--line); padding: 1px 0 1px 10px; font-size: 12px; line-height: 1.55; min-width: 0; }
.ev-meta { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 7px; color: var(--muted); font-size: 10px; margin-bottom: 4px; }
.ev-id { margin-left: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.relation { font-weight: 650; } .relation.supports { color: var(--ok); } .relation.contradicts { color: var(--bad); }
.source { margin-top: 5px; font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
.excerpt { margin-top: 5px; }
pre { margin: 8px 0 0; max-height: 220px; overflow: auto; background: var(--soft); border: 1px solid var(--line); border-radius: 5px; padding: 10px; font-size: 11px; white-space: pre; tab-size: 4; }
pre code { padding: 0; background: none; font-size: inherit; }
.linkbtn { align-self: flex-start; background: none; border: 0; padding: 3px 0; color: var(--accent); font-size: 12px; text-align: left; }
.linkbtn:hover { text-decoration: underline; }
.note { color: var(--muted); font-size: 12px; line-height: 1.5; } .ev .note { font-size: 11px; margin-top: 5px; }
.limitations { padding: 9px 10px; background: var(--soft); border-radius: 6px; color: var(--muted); font-size: 12px; }
.limitations h4 { color: var(--fg); }
.votes { display: flex; gap: 6px; align-items: center; color: var(--muted); font-size: 11px; padding-top: 5px; }
.vote { background: none; border: 1px solid var(--line); border-radius: 5px; padding: 2px 8px; font-size: 11px; }
.vote:hover { background: var(--soft); border-color: var(--muted); }
details > summary { cursor: pointer; color: var(--muted); font-size: 12px; list-style: none; }
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: "▸ "; } details[open] > summary::before { content: "▾ "; }
details .inner { margin-top: 10px; color: var(--muted); font-size: 12px; display: flex; flex-direction: column; gap: 9px; }
.inner h4 { color: var(--fg); margin-top: 5px; }
.blockers { background: var(--soft); } .blocker { font-size: 12px; } .blocker + .blocker { border-top: 1px solid var(--line); padding-top: 10px; }
.next-step { color: var(--muted); margin-top: 5px; }
.btn { font-size: 12px; padding: 7px 12px; border-radius: 6px; border: 1px solid var(--line); background: var(--soft); align-self: flex-start; }
.btn:hover { border-color: var(--muted); }
.empty { padding: 12px 0; color: var(--muted); font-size: 13px; }
.stages { list-style: none; padding: 0; } .stages li { padding: 5px 0; border-bottom: 1px solid var(--line); }
.stages li > div { display: flex; justify-content: space-between; gap: 12px; } .stages p { font-size: 11px; margin-top: 3px; }
.stage-status { font-size: 11px; } .stage-ok .stage-status { color: var(--ok); } .stage-failed .stage-status { color: var(--bad); } .stage-degraded .stage-status { color: var(--warn); }
.provenance { display: grid; gap: 5px; margin-top: 5px; } .provenance > div { display: grid; grid-template-columns: 55px 1fr; gap: 10px; }
.provenance dd { font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; min-width: 0; }
.privacy { font-size: 11px; } .error-text { color: var(--bad); font-size: 13px; }
.spinner { width: 12px; height: 12px; border: 2px solid var(--line); border-top-color: var(--accent); border-radius: 50%; display: inline-block; animation: spin .8s linear infinite; flex: none; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (max-width: 420px) { .root { right: 8px; bottom: 8px; width: calc(100vw - 16px); max-height: calc(100dvh - 16px); } .head { padding: 9px 10px; gap: 4px; } .title { font-size: 11px; } .summary, .section { padding: 14px; } .mockbar { margin: 12px 14px 0; } h2 { font-size: 18px; } .section-heading { align-items: flex-start; } .section-heading > .note { display: none; } }
@media (prefers-reduced-motion: reduce) { .spinner { animation: none; } .caret { transition: none; } }
button:focus-visible, a:focus-visible, summary:focus-visible, pre:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
`;
