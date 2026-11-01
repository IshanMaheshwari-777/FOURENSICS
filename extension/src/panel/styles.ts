// Panel styles. Used inside Chrome's side panel (an extension page) and the dev preview's Shadow DOM.
// Neutral graphite and white; colour is reserved for meaning (green supported, red contradicted,
// amber mixed), each with a soft tint for backgrounds.
export const css = `
:host { all: initial; }
.root {
  --bg: #ffffff; --bg-subtle: #f5f5f5; --surface: #ffffff; --raised: #fafafa;
  --fg: #171717; --fg-2: #404040; --muted: #6b6b6b; --faint: #a3a3a3;
  --line: #e8e8e8; --line-strong: #d4d4d4;
  --accent: #171717; --on-accent: #ffffff; --accent-soft: #f2f2f2; --accent-line: #d4d4d4;
  --ok: #15803d; --ok-soft: #effaf3; --ok-line: #bfe8cd;
  --bad: #c62828; --bad-soft: #fdf1f1; --bad-line: #f4c7c7;
  --warn: #a15c07; --warn-soft: #fdf6e8; --warn-line: #f0d9a7;
  --neu: #525252; --neu-soft: #f3f3f3; --neu-line: #e0e0e0;
  --shadow: 0 1px 2px rgba(0,0,0,.05), 0 1px 1px rgba(0,0,0,.03);
  --sans: "Inter", ui-sans-serif, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", Roboto, "Noto Sans", sans-serif;
  --mono: "JetBrains Mono", "SF Mono", ui-monospace, Menlo, Consolas, "Liberation Mono", monospace;
  height: 100%; display: flex; flex-direction: column; min-width: 0;
  background: var(--bg); color: var(--fg);
  font: 14px/1.55 var(--sans); font-feature-settings: "cv11", "ss01", "ss03";
  -webkit-font-smoothing: antialiased; text-align: left; overflow-wrap: anywhere; color-scheme: light;
}
.root.dark {
  --bg: #0b0b0b; --bg-subtle: #121212; --surface: #161616; --raised: #1b1b1b;
  --fg: #f5f5f5; --fg-2: #d4d4d4; --muted: #a3a3a3; --faint: #737373;
  --line: #262626; --line-strong: #363636;
  --accent: #f5f5f5; --on-accent: #0b0b0b; --accent-soft: rgba(255,255,255,.07); --accent-line: rgba(255,255,255,.2);
  --ok: #4ade80; --ok-soft: rgba(74,222,128,.1); --ok-line: rgba(74,222,128,.28);
  --bad: #f87171; --bad-soft: rgba(248,113,113,.1); --bad-line: rgba(248,113,113,.28);
  --warn: #fbbf24; --warn-soft: rgba(251,191,36,.1); --warn-line: rgba(251,191,36,.28);
  --neu: #b5b5b5; --neu-soft: rgba(255,255,255,.06); --neu-line: rgba(255,255,255,.16);
  --shadow: none; color-scheme: dark;
}
*, *::before, *::after { box-sizing: border-box; }
button { font: inherit; color: inherit; cursor: pointer; -webkit-tap-highlight-color: transparent; }
button:disabled { opacity: .45; cursor: default; }
a { color: var(--fg); text-decoration: underline; text-decoration-color: var(--line-strong); text-underline-offset: 2px; }
a:hover { text-decoration-color: currentColor; }
p, h2, h3, h4, dl, dd, figure { margin: 0; }
ul, ol { margin: 0; padding-left: 18px; } li + li { margin-top: 4px; }
code { font: 12px/1.5 var(--mono); background: var(--neu-soft); border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; }
svg { flex: none; }

/* Tones: set --tone and friends on any element that carries a status. */
.ok { --tone: var(--ok); --tone-soft: var(--ok-soft); --tone-line: var(--ok-line); }
.bad { --tone: var(--bad); --tone-soft: var(--bad-soft); --tone-line: var(--bad-line); }
.warn { --tone: var(--warn); --tone-soft: var(--warn-soft); --tone-line: var(--warn-line); }
.neu { --tone: var(--neu); --tone-soft: var(--neu-soft); --tone-line: var(--neu-line); }
.acc { --tone: var(--accent); --tone-soft: var(--accent-soft); --tone-line: var(--accent-line); }

/* Header */
.head { position: sticky; top: 0; z-index: 2; display: flex; align-items: center; gap: 8px; padding: 10px 12px 10px 16px;
  background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: saturate(1.4) blur(10px);
  border-bottom: 1px solid var(--line); flex: none; }
.brand-mark { width: 24px; height: 24px; display: grid; place-items: center; border-radius: 7px; flex: none;
  background: var(--accent); color: var(--on-accent); font-size: 13px; font-weight: 800; letter-spacing: -.02em; }
.title { font-size: 13px; font-weight: 700; letter-spacing: .08em; }
.spacer { flex: 1; }
.run-state { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; font-weight: 600; white-space: nowrap;
  color: var(--tone); background: var(--tone-soft); border: 1px solid var(--tone-line); border-radius: 999px; padding: 2px 9px 2px 7px; }
.run-state::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.run-state.running::before { display: none; }
.iconbtn { background: none; border: 0; color: var(--muted); width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; flex: none; }
.iconbtn:hover:not(:disabled) { background: var(--neu-soft); color: var(--fg); }

.body { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden; overscroll-behavior: contain; scrollbar-gutter: stable;
  padding: 14px 14px 28px; display: flex; flex-direction: column; gap: 12px; }

/* Cards */
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow); }
.mockbar { display: flex; gap: 8px; align-items: center; padding: 8px 12px; border-radius: 10px; font-size: 12.5px; font-weight: 500;
  color: var(--warn); background: var(--warn-soft); border: 1px solid var(--warn-line); }

/* Verdict */
.hero { padding: 16px; display: flex; flex-direction: column; gap: 10px; border-color: var(--tone-line);
  background: linear-gradient(180deg, var(--tone-soft), var(--surface) 70%); }
.target { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--muted); min-width: 0; }
.target a { color: inherit; text-decoration: none; display: inline-flex; align-items: center; gap: 4px; min-width: 0; }
.target a:hover { color: var(--fg); }
.finding-label { align-self: flex-start; display: inline-flex; gap: 6px; align-items: center; font-size: 12px; font-weight: 650;
  color: var(--tone); background: var(--surface); border: 1px solid var(--tone-line); border-radius: 999px; padding: 3px 10px 3px 4px; }
h2 { font-size: 18.5px; font-weight: 680; line-height: 1.35; letter-spacing: -.018em; color: var(--fg); }
.dot { width: 18px; height: 18px; border-radius: 50%; flex: none; display: inline-grid; place-items: center;
  background: var(--tone); color: var(--surface); font-size: 10.5px; font-weight: 800; line-height: 1; }
.progress { height: 3px; border-radius: 3px; background: var(--accent-soft); overflow: hidden; position: relative; }
.progress::after { content: ""; position: absolute; inset: 0 auto 0 0; width: 40%; border-radius: 3px; background: var(--accent); animation: slide 1.4s ease-in-out infinite; }
@keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(260%); } }

/* Facts */
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }
.fact { padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.fact.wide { grid-column: 1 / -1; }
.fact dt { font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
.fact dd { font-size: 14px; font-weight: 600; color: var(--fg); }
.fact dd.note { font-weight: 400; } .fact dd.note::first-letter { text-transform: uppercase; }
.chip { display: inline-flex; align-items: center; gap: 5px; align-self: flex-start; font-size: 12.5px; font-weight: 650;
  color: var(--tone); background: var(--tone-soft); border: 1px solid var(--tone-line); border-radius: 7px; padding: 1px 8px; margin: 2px 0 1px; }

/* Sections */
.section { padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.claims-section { padding: 4px 0 0; }
.section-heading { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
h3 { font-size: 13.5px; font-weight: 680; letter-spacing: -.005em; display: flex; align-items: center; gap: 6px; }
h4 { font-size: 11.5px; font-weight: 650; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
.count { display: inline-grid; place-items: center; min-width: 20px; height: 18px; border-radius: 999px; padding: 0 6px;
  font-size: 11px; font-weight: 600; color: var(--muted); background: var(--neu-soft); font-variant-numeric: tabular-nums; }
.attention { padding: 12px 14px; border-color: var(--warn-line); background: var(--warn-soft); }
.attention h3 { color: var(--warn); margin-bottom: 2px; } .attention p { font-size: 12.5px; color: var(--fg-2); }
.blockers { border-color: var(--warn-line); }
.blocker { font-size: 13px; color: var(--fg-2); } .blocker + .blocker { border-top: 1px solid var(--line); padding-top: 10px; }
.next-step { color: var(--muted); margin-top: 5px; font-size: 12.5px; }

/* Filters: a segmented control */
.filters { display: flex; gap: 2px; padding: 3px; overflow-x: auto; scrollbar-width: none; background: var(--bg-subtle); border: 1px solid var(--line); border-radius: 10px; }
.filter { flex: 1 0 auto; border: 0; border-radius: 7px; padding: 5px 6px; background: none; color: var(--muted);
  font-size: 11.5px; font-weight: 550; white-space: nowrap; display: inline-flex; justify-content: center; align-items: center; gap: 5px; }
.filter span { font-variant-numeric: tabular-nums; font-weight: 650; }
.filter:hover { color: var(--fg); }
.filter[aria-pressed="true"] { background: var(--surface); color: var(--fg); box-shadow: 0 1px 2px rgba(0,0,0,.08); }
.root.dark .filter[aria-pressed="true"] { background: var(--raised); box-shadow: inset 0 0 0 1px var(--line-strong); }
.filter.supported span { color: var(--ok); } .filter.contradicted span { color: var(--bad); }

/* Claims */
.claims { display: flex; flex-direction: column; gap: 8px; }
.claim { border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: var(--surface); box-shadow: inset 3px 0 0 var(--tone); }
.claim.open { border-color: var(--line-strong); }
.claim-row { display: flex; gap: 10px; align-items: flex-start; width: 100%; text-align: left; background: none; border: 0; padding: 11px 12px 11px 15px; }
.claim-row:hover { background: var(--raised); }
.claim-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.claim-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.eyebrow { color: var(--muted); font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; font-weight: 700; }
.primary .eyebrow { color: var(--fg); }
.badge { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 650; color: var(--tone);
  background: var(--tone-soft); border-radius: 999px; padding: 1px 8px 1px 3px; }
.badge .dot { width: 15px; height: 15px; font-size: 9px; }
.claim-text { font-weight: 550; font-size: 13.5px; line-height: 1.5; color: var(--fg); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.open .claim-text { display: block; -webkit-line-clamp: unset; }
.caret { color: var(--faint); flex: none; margin-top: 2px; transition: transform .15s; display: grid; } .open .caret { transform: rotate(180deg); }
.detail { padding: 0 12px 12px 15px; display: flex; flex-direction: column; gap: 12px; font-size: 13px; }
.expl { border-top: 1px solid var(--line); padding-top: 11px; color: var(--fg-2); line-height: 1.6; }
.verification { display: flex; gap: 8px; align-items: baseline; font-size: 12.5px; color: var(--fg-2); }
.field-label { color: var(--muted); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
.evidence-block, .limitations { display: flex; flex-direction: column; gap: 8px; }
.evidence-block h4 { display: flex; align-items: center; gap: 6px; }

/* Evidence */
.ev-list { display: flex; flex-direction: column; gap: 6px; }
.ev { border: 1px solid var(--line); border-radius: 9px; padding: 9px 10px; font-size: 12.5px; line-height: 1.55; min-width: 0; background: var(--raised); color: var(--fg-2); }
.ev-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; color: var(--muted); font-size: 11px; margin-bottom: 5px; }
.ev-id { margin-left: auto; font: 10.5px/1 var(--mono); color: var(--faint); }
.relation { font-weight: 650; font-size: 10.5px; text-transform: uppercase; letter-spacing: .05em; color: var(--tone); background: var(--tone-soft); border-radius: 5px; padding: 1px 6px; }
.source { margin-top: 6px; font: 11.5px/1.5 var(--mono); overflow-wrap: anywhere; display: flex; gap: 5px; align-items: flex-start; }
.source svg { margin-top: 2px; color: var(--faint); }
.excerpt { margin-top: 6px; }
pre { margin: 7px 0 0; max-height: 240px; overflow: auto; background: var(--bg-subtle); border: 1px solid var(--line); border-radius: 7px; padding: 10px; font: 11.5px/1.55 var(--mono); white-space: pre; tab-size: 4; color: var(--fg); }
pre code { padding: 0; background: none; border: 0; font: inherit; }
.linkbtn { align-self: flex-start; background: none; border: 0; padding: 2px 0; color: var(--fg); text-decoration: underline; text-decoration-color: var(--line-strong); text-underline-offset: 2px; font-size: 12.5px; font-weight: 550; }
.linkbtn:hover { text-decoration-color: currentColor; }
.note { color: var(--muted); font-size: 12.5px; line-height: 1.5; } .ev .note { font-size: 11.5px; margin-top: 5px; }
.limitations { padding: 10px 12px; background: var(--bg-subtle); border-radius: 8px; color: var(--fg-2); font-size: 12.5px; }
.votes { display: flex; gap: 6px; align-items: center; color: var(--muted); font-size: 12px; padding-top: 2px; }
.vote { background: var(--surface); border: 1px solid var(--line); border-radius: 7px; padding: 2px 10px; font-size: 12px; font-weight: 550; }
.vote:hover { border-color: var(--line-strong); background: var(--raised); }
details > summary { cursor: pointer; color: var(--muted); font-size: 12.5px; font-weight: 550; list-style: none; display: flex; align-items: center; gap: 6px; }
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: ""; width: 6px; height: 6px; border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor; transform: rotate(-45deg); transition: transform .15s; margin: 0 2px; }
details[open] > summary::before { transform: rotate(45deg); }
details > summary:hover { color: var(--fg); }
details .inner { margin-top: 10px; color: var(--fg-2); font-size: 12.5px; display: flex; flex-direction: column; gap: 9px; }
.inner h4 { margin-top: 6px; }

/* Missing information and reply */
.missing li { color: var(--fg-2); font-size: 13px; }
.btn { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 600; padding: 8px 14px; border-radius: 9px;
  border: 1px solid transparent; background: var(--accent); color: var(--on-accent); align-self: flex-start; }
.btn:hover { opacity: .88; }
.empty { padding: 8px 0; color: var(--muted); font-size: 13px; }

/* Details */
.stages { list-style: none; padding: 0; } .stages li { padding: 6px 0; border-bottom: 1px solid var(--line); margin: 0; }
.stages li > div { display: flex; justify-content: space-between; gap: 12px; text-transform: capitalize; } .stages p { font-size: 11.5px; margin-top: 3px; color: var(--muted); text-transform: none; }
.stage-status { font-size: 11px; font-weight: 600; text-transform: lowercase; } .stage-ok .stage-status { color: var(--ok); } .stage-failed .stage-status { color: var(--bad); } .stage-degraded .stage-status { color: var(--warn); } .stage-skipped .stage-status { color: var(--faint); }
.provenance { display: grid; gap: 6px; margin-top: 4px; } .provenance > div { display: grid; grid-template-columns: 64px 1fr; gap: 10px; }
.provenance dt { color: var(--muted); font-size: 12px; } .provenance dd { font: 11.5px/1.5 var(--mono); min-width: 0; color: var(--fg); }
.privacy { font-size: 11.5px; text-align: center; padding: 4px 8px 0; color: var(--faint); }
.error-text { color: var(--bad); font-size: 13px; font-weight: 500; }
.spinner { width: 11px; height: 11px; border: 2px solid var(--accent-line); border-top-color: var(--accent); border-radius: 50%; display: inline-block; animation: spin .8s linear infinite; flex: none; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Welcome (no issue or PR in this tab) */
.idle { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 12px; padding: 32px 24px; }
.idle .brand-mark { width: 44px; height: 44px; border-radius: 13px; font-size: 22px; box-shadow: 0 8px 24px rgba(0,0,0,.18); }
.idle h2 { font-size: 20px; }
.idle p { color: var(--muted); font-size: 13.5px; max-width: 34ch; }
.idle ol { text-align: left; color: var(--fg-2); font-size: 13px; display: flex; flex-direction: column; gap: 6px; padding-left: 20px; max-width: 34ch; }

@media (max-width: 340px) { .head { padding: 8px 8px 8px 12px; gap: 6px; } .title { font-size: 12px; } .body { padding: 10px 10px 24px; } h2 { font-size: 17px; } .filter { font-size: 11px; } }
@media (prefers-reduced-motion: reduce) { .spinner, .progress::after { animation: none; } .caret, details > summary::before { transition: none; } }
button:focus-visible, a:focus-visible, summary:focus-visible, pre:focus-visible { outline: 2px solid var(--fg); outline-offset: 2px; border-radius: 6px; }
`;
