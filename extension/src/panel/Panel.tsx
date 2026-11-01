import { useEffect, useId, useState } from "react";
import type { Claim, ClaimAssessment, EvidenceItem, PanelResult, Relation } from "../types";
import { insertReply } from "./reply";
import { present, safeHref, sourceLocation, type Filter } from "./presentation";

type Kind = "ok" | "bad" | "warn" | "neu" | "acc";
const TONE: Record<string, [Kind, string, string]> = {
  supported: ["ok", "✓", "Main claim supported"], contradicted: ["bad", "✕", "Main claim contradicted"],
  mixed: ["warn", "!", "Mixed findings"], neutral: ["neu", "?", "Evidence is inconclusive"],
  not_defect: ["neu", "i", "Not a bug report"], pending: ["acc", "…", "Checking claims"],
};
const VERDICT: Record<string, [Kind, string, string]> = {
  supported: ["ok", "✓", "Supported"], contradicted: ["bad", "✕", "Contradicted"],
  inconclusive: ["neu", "?", "Unresolved"],
};
const VERIFICATION = {
  reproduced: "Reproduced in sandbox", not_reproduced: "Not reproduced in sandbox",
  environment_unavailable: "Execution environment unavailable", not_attempted: "Not executed",
};
const RELIABILITY = {
  exact: "Exact snapshot", "release-matched": "Release matched", inferred: "Approximate snapshot", unknown: "Version match unknown",
};
const LABELS: Record<string, [Kind, string]> = {
  confirmed: ["ok", "Confirmed"], invalid: ["bad", "Invalid"], cannot_reproduce: ["warn", "Cannot reproduce"],
};
const TIERS = {
  E1: ["Execution evidence", "An observation from an execution check; read its scope and result."],
  E2: ["Measured source fact", "A deterministic source check. A matching file or symbol alone does not prove behavior."],
  E3: ["Source context", "Source or documentation interpreted for this claim; not an execution result."],
  E4: ["Search context", "A weak retrieval match; not enough to settle a claim on its own."],
};
const RELATIONS: Record<Relation, [Kind, string]> = { supports: ["ok", "Supports"], contradicts: ["bad", "Contradicts"], context: ["neu", "Context"] };
const STATE: Record<string, [Kind, string]> = {
  running: ["acc", "In progress"], partial: ["warn", "Incomplete"], complete: ["ok", "Finished"], error: ["bad", "Failed"],
};
const VISIBLE_EVIDENCE = 3;

// Small inline icons (stroke follows the text colour).
const Icon = {
  refresh: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>,
  settings: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>,
  close: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>,
  external: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 17 17 7M8 7h9v9"/></svg>,
  chevron: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>,
  file: <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>,
  reply: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 14 4 9l5-5"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>,
};

// Only inline code is formatted. All report/source text stays React-escaped.
export function Text({ value }: { value: string }) {
  return <>{value.split(/(`[^`\n]+`)/g).map((part, i) => part.startsWith("`") && part.endsWith("`")
    ? <code key={i}>{part.slice(1, -1)}</code> : part)}</>;
}
function Dot({ kind, symbol }: { kind: Kind; symbol: string }) {
  return <span className={`dot ${kind}`} aria-hidden="true">{symbol}</span>;
}
function Evidence({ item, relation }: { item: EvidenceItem; relation: Relation }) {
  const href = safeHref(item.permalink);
  const location = sourceLocation(item.path, item.start, item.end);
  const [kind, label] = RELATIONS[relation];
  return <article className="ev">
    <div className="ev-meta">
      <span className={`relation ${kind}`}>{label}</span>
      <span title={TIERS[item.tier][1]}>{TIERS[item.tier][0]}</span>
      <span className="ev-id">{item.id}</span>
    </div>
    <div><Text value={item.result} /></div>
    {(item.path || href) && <div className="source">{Icon.file}
      {href ? <a href={href} target="_blank" rel="noopener noreferrer" title={`Snapshot ${item.sha || "unknown"}`}>{location} ↗</a> : <span>{location}</span>}
    </div>}
    {item.absence && <p className="note">A search miss applies only to the inspected scope.</p>}
    {item.excerpt && <details className="excerpt">
      <summary>View source excerpt</summary>
      <pre tabIndex={0} aria-label={`Source excerpt ${item.id}`}><code>{item.excerpt.slice(0, 12000)}</code></pre>
      {item.excerpt.length > 12000 && <p className="note">Excerpt shortened for display. Open the source for the full context.</p>}
    </details>}
  </article>;
}

function ClaimRow({ claim, a, evidence, initiallyOpen, running, onVote }: {
  claim: Claim; a?: ClaimAssessment; evidence: Map<string, EvidenceItem>; initiallyOpen: boolean; running: boolean;
  onVote: (v: "yes" | "no") => void;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const [showAll, setShowAll] = useState(false);
  const [voted, setVoted] = useState(false);
  useEffect(() => { if (initiallyOpen) setOpen(true); }, [initiallyOpen]);
  const detailId = useId();
  const [kind, symbol, label] = a ? VERDICT[a.technical] : (["neu", running ? "…" : "?", running ? "Checking" : "Unresolved"] as [Kind, string, string]);
  const cited = [...new Set(a?.evidence_ids ?? [])].map((id) => evidence.get(id)).filter(Boolean) as EvidenceItem[];
  const missingCitations = (a?.evidence_ids ?? []).some((id) => !evidence.has(id));
  const shown = showAll ? cited : cited.slice(0, VISIBLE_EVIDENCE);
  const relationOf = (id: string): Relation => !a || a.technical === "inconclusive" ? "context" : a.relations[id] ?? "context";

  return <article className={`claim ${kind}${open ? " open" : ""}${claim.is_primary ? " primary" : ""}`}>
    <button className="claim-row" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls={open ? detailId : undefined}>
      <span className="claim-main">
        <span className="claim-top"><span className="eyebrow">{claim.is_primary ? "Main claim" : `Claim ${claim.id.replace(/^C/, "")}`}</span>
          <span className={`badge ${kind}`}><Dot kind={kind} symbol={symbol} />{label}</span></span>
        <span className="claim-text"><Text value={claim.text} /></span>
      </span>
      <span className="caret" aria-hidden="true">{Icon.chevron}</span>
    </button>
    {open && <div className="detail" id={detailId}>
      <div className="expl"><Text value={a?.explanation || (running ? "Gathering and checking evidence for this claim." : "No assessment was produced for this claim. It remains unresolved.")} /></div>
      {a && <div className="verification"><span className="field-label">Execution</span> {VERIFICATION[a.verification]}</div>}
      <div className="evidence-block">
        <h4>Evidence <span className="count">{cited.length}</span></h4>
        {shown.length ? <div className="ev-list">{shown.map((e) => <Evidence key={e.id} item={e} relation={relationOf(e.id)} />)}</div>
          : <p className="note">{running ? "No cited evidence yet." : "No evidence cited for this claim."}</p>}
        {missingCitations && <p className="note">Some cited records are unavailable in this saved result.</p>}
        {cited.length > VISIBLE_EVIDENCE && <button className="linkbtn" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>
          {showAll ? "Show fewer sources" : `Show ${cited.length - VISIBLE_EVIDENCE} more sources`}
        </button>}
      </div>
      {!!a?.limitations.length && <div className="limitations"><h4>Limitations</h4><ul>{a.limitations.map((item, i) => <li key={i}><Text value={item} /></li>)}</ul></div>}
      {(a?.unconfirmed_reasoning || a?.lean || a?.downgraded_reason || claim.condition || claim.expected || claim.observed) && <details>
        <summary>Reasoning and claim details</summary><div className="inner">
          {claim.condition && <div><strong>Condition:</strong> <Text value={claim.condition} /></div>}
          {claim.expected && <div><strong>Expected:</strong> <Text value={claim.expected} /></div>}
          {claim.observed && <div><strong>Reported:</strong> <Text value={claim.observed} /></div>}
          {a?.lean && <div>The model initially leaned toward “{a.lean}”; the available evidence was insufficient to confirm that conclusion.</div>}
          {a?.unconfirmed_reasoning && <div><strong>Unconfirmed model interpretation:</strong> <Text value={a.unconfirmed_reasoning} /></div>}
          {a?.downgraded_reason && <div><strong>Evidence safeguard:</strong> <Text value={a.downgraded_reason} /></div>}
          {claim.inferred && <div>This claim was inferred from the report rather than quoted directly.</div>}
        </div>
      </details>}
      {a && <div className="votes">{voted ? <span role="status">Thanks for the feedback</span> : <>
        Useful assessment?
        <button className="vote" aria-label={`Claim ${claim.id}: assessment is useful`} onClick={() => { setVoted(true); onVote("yes"); }}>Yes</button>
        <button className="vote" aria-label={`Claim ${claim.id}: assessment needs correction`} onClick={() => { setVoted(true); onVote("no"); }}>No</button>
      </>}</div>}
    </div>}
  </article>;
}

function Header(props: { state?: string; running?: boolean; onRefresh?: () => void; onSettings?: () => void; onClose?: () => void }) {
  const [kind, label] = props.state ? STATE[props.state] : ["neu", ""];
  return <header className="head">
    <span className="brand-mark" aria-hidden="true">F</span><span className="title">FOURENSICS</span>
    <span className="spacer" />
    {props.state && <span className={`run-state ${kind} ${props.state}`} role="status">{props.running && <span className="spinner" />}{label}</span>}
    {props.onRefresh && <button className="iconbtn" title="Run analysis again" aria-label="Run analysis again" onClick={props.onRefresh}>{Icon.refresh}</button>}
    {props.onSettings && <button className="iconbtn" title="Settings" aria-label="Settings" onClick={props.onSettings}>{Icon.settings}</button>}
    {props.onClose && <button className="iconbtn" title="Close" aria-label="Close assessment" onClick={props.onClose}>{Icon.close}</button>}
  </header>;
}

/** Shown when the active tab is not a GitHub issue or pull request. */
export function Idle(props: { dark: boolean; message?: string | null; onSettings?: () => void }) {
  return <div className={`root${props.dark ? " dark" : ""}`} role="complementary" aria-label="FOURENSICS">
    <Header onSettings={props.onSettings} />
    <div className="idle">
      <span className="brand-mark" aria-hidden="true">F</span>
      <h2>Open an issue or pull request</h2>
      <p>FOURENSICS checks each claim in a GitHub issue or PR against the repository's code, with links to the exact lines.</p>
      <ol>
        <li>Go to any public issue or pull request on github.com.</li>
        <li>This panel fills in as the checks finish, usually within a minute.</li>
        <li>Nothing is posted to GitHub; you decide what to do.</li>
      </ol>
      {props.message && <p className="note" role="status">{props.message}</p>}
    </div>
  </div>;
}

export function Panel(props: {
  result: PanelResult | null; error: string | null; notice?: string | null; dark: boolean; startCollapsed?: boolean;
  onRefresh: () => void; onVote: (claimId: string, vote: "yes" | "no") => void; onClose?: () => void;
  onSettings?: () => void; onInsertReply?: (text: string) => Promise<string>;
}) {
  const { result, error, dark } = props;
  const [replyMsg, setReplyMsg] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => { setFilter("all"); setReplyMsg(null); }, [result?.run_id]);
  const view = present(result, error);
  const running = view.state === "running";
  const [toneKind, toneSymbol, toneLabel] = TONE[running ? "pending" : result?.summary_tone ?? "neutral"];
  const heroKind: Kind = view.state === "error" ? "bad" : toneKind;
  const evidence = new Map((result?.evidence ?? []).map((e) => [e.id, e]));
  const snap = result?.snapshot;
  // Evidence-backed labels only; results from before pipeline 30 never had a label, so show nothing for them.
  const label = result?.suggested_label?.basis === "evidence" ? result.suggested_label : null;
  const labelled = !!result && result.report_kind !== "pr" && result.target?.kind !== "pr"
    && Number(result.pipeline_version) >= 30;
  const targetHref = safeHref(result?.target?.url);
  const targetLabel = result?.target ? `${result.target.owner}/${result.target.repo} · ${result.target.kind === "pr" ? "PR" : "Issue"} #${result.target.number}` : null;
  const claims = view.claims.filter((c) => filter === "all" || view.outcome(c) === filter);
  const checks = (result?.evidence ?? []).filter((e) => (e.tier === "E1" || e.tier === "E2") && e.claim_ids.length === 0);
  const filters: [Filter, string, number][] = [["all", "All", view.claims.length], ["supported", "Supported", view.counts.supported],
    ["contradicted", "Contradicted", view.counts.contradicted], ["inconclusive", "Unresolved", view.counts.inconclusive]];
  const reply = props.onInsertReply ?? insertReply;

  return <div className={`root${dark ? " dark" : ""}`} role="complementary" aria-label="FOURENSICS assessment">
    <Header state={view.state} running={running} onRefresh={props.onRefresh} onSettings={props.onSettings} onClose={props.onClose} />
    <div className="body">
      {result?.mock && <div className="mockbar" role="note">Example data · This is not an analysis of this page.</div>}

      <section className={`card hero ${heroKind}`} aria-label="Main finding">
        {targetLabel && <div className="target">{targetHref ? <a href={targetHref} target="_blank" rel="noopener noreferrer">{targetLabel} {Icon.external}</a> : targetLabel}</div>}
        <div className={`finding-label ${heroKind}`}>
          <Dot kind={heroKind} symbol={view.state === "error" ? "!" : toneSymbol} />
          {view.state === "error" ? "Analysis unavailable" : toneLabel}
        </div>
        <h2>{view.state === "error" ? "The analysis could not finish." : <Text value={result?.summary_line || "Gathering the report and repository evidence…"} />}</h2>
        {running && <div className="progress" aria-hidden="true" />}
        {running && <p className="note">Claim checks will appear here as the analysis progresses.</p>}
        {(error || result?.error) && <p className="error-text" role="alert">{error || result?.error}</p>}
        {error && /reach the analysis backend/i.test(error) && <p className="note">Start it with <code>scripts/dev.sh backend</code>, or turn on Mock mode in Settings to see an example.</p>}
        {props.notice && <p className="note" role="status">{props.notice}</p>}
      </section>

      {result && !running && <dl className="facts">
        <div className="card fact"><dt>Code version</dt><dd>{snap?.sha ? snap.ref_label || snap.sha.slice(0, 7) : "Unknown"}</dd><dd className="note">{RELIABILITY[snap?.reliability ?? "unknown"]}</dd></div>
        <div className="card fact"><dt>Execution</dt><dd>{view.execution}</dd></div>
        {labelled && <div className="card fact wide suggested-label"><dt>Suggested label</dt>
          {label
            ? <><dd className={`chip ${LABELS[label.label][0]}`}>{LABELS[label.label][1]}</dd>{!!label.reasons.length && <dd className="note"><Text value={label.reasons.join("; ")} /></dd>}</>
            : <><dd>None</dd><dd className="note">The evidence doesn't settle it; no label is guessed from wording alone.</dd></>}
        </div>}
      </dl>}

      {view.state === "partial" && <section className="card attention" aria-label="Incomplete analysis">
        <h3>Analysis incomplete</h3><p>Some checks did not finish. Treat the findings below as a partial assessment.</p>
      </section>}
      {view.blockers.length > 0 && <section className="card section blockers" aria-label="What blocked verification">
        <h3>What blocked verification</h3>
        {view.blockers.map((b, i) => <div className="blocker" key={i}><p><Text value={b.reason} /></p><p className="next-step"><strong>Next step:</strong> <Text value={b.next_step} /></p></div>)}
      </section>}

      {view.claims.length > 0 && <section className="section claims-section" aria-label="Claim assessments">
        <div className="section-heading"><h3>Claim assessments <span className="count">{view.claims.length}</span></h3><span className="note">Main claim first</span></div>
        {!running && <div className="filters" role="group" aria-label="Filter claims by outcome">
          {filters.map(([value, label, count]) => <button key={value} className={`filter ${value}`} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label} <span>{count}</span></button>)}
        </div>}
        <div className="claims">
          {claims.map((c) => <ClaimRow key={`${result?.run_id}:${c.id}`} claim={c} a={view.assessments.get(c.id)} evidence={evidence}
            initiallyOpen={c.is_primary || filter !== "all"} running={running} onVote={(v) => props.onVote(c.id, v)} />)}
        </div>
        {!claims.length && <p className="empty">No claims with this outcome.</p>}
      </section>}
      {result && !view.claims.length && <section className="card section">
        <h3>{running ? "Checks so far" : "No assessable claims"}</h3>
        <p className="note">{running ? "Gathering evidence before individual claims are assessed." : "This run did not produce individual claim assessments. Review the analysis details and limitations below."}</p>
        {checks.length > 0 && <div className="ev-list">{checks.slice(0, 6).map((e) => <Evidence key={e.id} item={e} relation="context" />)}</div>}
      </section>}

      {!!result?.missing_info.length && <section className="card section missing" aria-label="Information that would help">
        <h3>Information that would help</h3><ul>{result.missing_info.map((m, i) => <li key={i}><Text value={m} /></li>)}</ul>
        {result.reply_draft && <><button className="btn" onClick={async () => setReplyMsg(await reply(result.reply_draft!))}>{Icon.reply}Insert suggested reply</button>
          <span className="note">Adds a draft to the comment box for your review. Nothing is posted.</span>{replyMsg && <p className="note" role="status">{replyMsg}</p>}</>}
      </section>}

      {result && <section className="card section methodology"><details>
        <summary>Analysis details &amp; limitations</summary><div className="inner">
          <h4>Scope</h4>
          {result.not_checked.length ? <ul>{result.not_checked.map((n, i) => <li key={i}><Text value={n} /></li>)}</ul> : <p>Scope is defined by the evidence attached to each claim.</p>}
          <h4>Evidence collected</h4><p>{result.evidence.length} records collected. Cited records appear under each claim. Source matches alone do not establish runtime behavior.</p>
          <h4>Checks</h4><ol className="stages">{result.stages.map((s) => <li key={s.stage} className={`stage-${s.status}`}>
            <div><span>{s.stage.replace(/_/g, " ")}</span><span className="stage-status">{s.status}</span></div>
            {s.reason && <p>{s.reason}</p>}
          </li>)}</ol>
          {!!result.notes.length && <><h4>Notes</h4><ul>{result.notes.map((n, i) => <li key={i}><Text value={n} /></li>)}</ul></>}
          <dl className="provenance"><div><dt>Model</dt><dd>{result.models.reasoning ?? "Not recorded"}</dd></div>
            <div><dt>Pipeline</dt><dd>v{result.pipeline_version}</dd></div><div><dt>Run</dt><dd>{result.run_id}</dd></div>
            <div><dt>Commit</dt><dd>{snap?.sha || "Unknown"}</dd></div></dl>
        </div>
      </details></section>}
      {result && <p className="privacy">Assesses technical claims. Nothing is posted to GitHub automatically.</p>}
    </div>
  </div>;
}
