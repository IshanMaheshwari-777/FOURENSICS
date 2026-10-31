import { useEffect, useId, useState } from "react";
import type { Claim, ClaimAssessment, EvidenceItem, PanelResult, Relation } from "../types";
import { insertReply } from "./reply";
import { present, safeHref, sourceLocation, type Filter } from "./presentation";

type Kind = "ok" | "bad" | "warn" | "neu";
const TONE: Record<string, [Kind, string, string]> = {
  supported: ["ok", "✓", "Main claim supported"], contradicted: ["bad", "✕", "Main claim contradicted"],
  mixed: ["warn", "!", "Mixed findings"], neutral: ["neu", "?", "Evidence is inconclusive"],
  not_defect: ["neu", "i", "Not a bug report"], pending: ["neu", "…", "Checking claims"],
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
const TIERS = {
  E1: ["Execution evidence", "An observation from an execution check; read its scope and result."],
  E2: ["Measured source fact", "A deterministic source check. A matching file or symbol alone does not prove behavior."],
  E3: ["Source context", "Source or documentation interpreted for this claim; not an execution result."],
  E4: ["Search context", "A weak retrieval match; not enough to settle a claim on its own."],
};
const RELATIONS: Record<Relation, string> = { supports: "Supports", contradicts: "Contradicts", context: "Context" };
const VISIBLE_EVIDENCE = 3;

// Only inline code is formatted. All report/source text stays React-escaped.
function Text({ value }: { value: string }) {
  return <>{value.split(/(`[^`\n]+`)/g).map((part, i) => part.startsWith("`") && part.endsWith("`")
    ? <code key={i}>{part.slice(1, -1)}</code> : part)}</>;
}
function Dot({ kind, symbol }: { kind: Kind; symbol: string }) {
  return <span className={`dot ${kind}`} aria-hidden="true">{symbol}</span>;
}
function Evidence({ item, relation }: { item: EvidenceItem; relation: Relation }) {
  const href = safeHref(item.permalink);
  const location = sourceLocation(item.path, item.start, item.end);
  return <article className="ev">
    <div className="ev-meta">
      <span className={`relation ${relation}`}>{RELATIONS[relation]}</span>
      <span title={TIERS[item.tier][1]}>{TIERS[item.tier][0]}</span>
      <span className="ev-id">{item.id}</span>
    </div>
    <div><Text value={item.result} /></div>
    {(item.path || href) && <div className="source">
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

  return <article className={`claim${open ? " open" : ""}${claim.is_primary ? " primary" : ""}`}>
    <button className="claim-row" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls={open ? detailId : undefined}>
      <span className="claim-main">
        <span className="claim-top"><span className="eyebrow">{claim.is_primary ? "Main claim" : `Claim ${claim.id.replace(/^C/, "")}`}</span>
          <span className={`badge ${kind}`}><Dot kind={kind} symbol={symbol} />{label}</span></span>
        <span className="claim-text"><Text value={claim.text} /></span>
      </span>
      <span className="caret" aria-hidden="true">⌄</span>
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

export function Panel(props: {
  result: PanelResult | null; error: string | null; notice?: string | null; dark: boolean; startCollapsed: boolean;
  onRefresh: () => void; onClose: () => void; onVote: (claimId: string, vote: "yes" | "no") => void;
}) {
  const { result, error, dark } = props;
  const [collapsed, setCollapsed] = useState(props.startCollapsed);
  const [replyMsg, setReplyMsg] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => { setFilter("all"); setReplyMsg(null); }, [result?.run_id]);
  const view = present(result, error);
  const running = view.state === "running";
  const [toneKind, toneSymbol, toneLabel] = TONE[running ? "pending" : result?.summary_tone ?? "neutral"];
  const stateLabel = { running: "In progress", partial: "Incomplete", complete: "Finished", error: "Failed" }[view.state];
  const evidence = new Map((result?.evidence ?? []).map((e) => [e.id, e]));
  const snap = result?.snapshot;
  const targetHref = safeHref(result?.target?.url);
  const targetLabel = result?.target ? `${result.target.owner}/${result.target.repo} · ${result.target.kind === "pr" ? "PR" : "Issue"} #${result.target.number}` : null;
  const claims = view.claims.filter((c) => filter === "all" || view.outcome(c) === filter);
  const checks = (result?.evidence ?? []).filter((e) => (e.tier === "E1" || e.tier === "E2") && e.claim_ids.length === 0);
  const filters: [Filter, string, number][] = [["all", "All", view.claims.length], ["supported", "Supported", view.counts.supported],
    ["contradicted", "Contradicted", view.counts.contradicted], ["inconclusive", "Unresolved", view.counts.inconclusive]];

  return <div className={`root${dark ? " dark" : ""}${collapsed ? " collapsed" : ""}`} role="complementary" aria-label="FOURENSICS assessment">
    <div className="head">
      <span className="brand-mark" aria-hidden="true">F</span><span className="title">FOURENSICS</span>
      <span className="spacer" />
      <span className={`run-state ${view.state}`} role="status">{running && <span className="spinner" />}{stateLabel}</span>
      {!collapsed && <button className="iconbtn" title="Run analysis again" aria-label="Run analysis again" onClick={props.onRefresh}>↻</button>}
      <button className="iconbtn" title={collapsed ? "Expand assessment" : "Collapse assessment"} aria-label={collapsed ? "Expand assessment" : "Collapse assessment"} aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}>{collapsed ? "▴" : "▾"}</button>
      <button className="iconbtn" title="Hide on this page" aria-label="Hide assessment on this page" onClick={props.onClose}>✕</button>
    </div>
    {!collapsed && <div className="body">
      {result?.mock && <div className="mockbar" role="note">Example data · This is not an analysis of this page.</div>}
      <section className="summary" aria-label="Main finding">
        {targetLabel && <div className="target">{targetHref ? <a href={targetHref} target="_blank" rel="noopener noreferrer">{targetLabel} ↗</a> : targetLabel}</div>}
        <div className={`finding-label ${view.state === "error" ? "bad" : toneKind}`}>
          <Dot kind={view.state === "error" ? "bad" : toneKind} symbol={view.state === "error" ? "!" : toneSymbol} />
          {view.state === "error" ? "Analysis unavailable" : toneLabel}
        </div>
        <h2>{view.state === "error" ? "The analysis could not finish." : <Text value={result?.summary_line || "Gathering the report and repository evidence…"} />}</h2>
        {running && <p className="note">Claim checks will appear here as the analysis progresses.</p>}
        {(error || result?.error) && <p className="error-text" role="alert">{error || result?.error}</p>}
        {props.notice && <p className="note" role="status">{props.notice}</p>}
        {result && !running && <dl className="scope-grid">
          <div><dt>Code version</dt><dd>{snap?.sha ? snap.ref_label || snap.sha.slice(0, 7) : "Unknown"}</dd><dd className="note">{RELIABILITY[snap?.reliability ?? "unknown"]}</dd></div>
          <div><dt>Execution</dt><dd>{view.execution}</dd></div>
        </dl>}
      </section>
      {view.state === "partial" && <section className="attention" aria-label="Incomplete analysis">
        <h3>Analysis incomplete</h3><p>Some checks did not finish. Treat the findings below as a partial assessment.</p>
      </section>}
      {view.blockers.length > 0 && <section className="section blockers" aria-label="What blocked verification">
        <h3>What blocked verification</h3>
        {view.blockers.map((b, i) => <div className="blocker" key={i}><p><Text value={b.reason} /></p><p className="next-step"><strong>Next step:</strong> <Text value={b.next_step} /></p></div>)}
      </section>}
      {view.claims.length > 0 && <section className="section claims-section" aria-label="Claim assessments">
        <div className="section-heading"><h3>Claim assessments <span className="count">{view.claims.length}</span></h3><span className="note">Main claim first</span></div>
        {!running && <div className="filters" role="group" aria-label="Filter claims by outcome">
          {filters.map(([value, label, count]) => <button key={value} className={`filter ${value}`} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label} <span>{count}</span></button>)}
        </div>}
        {claims.map((c) => <ClaimRow key={`${result?.run_id}:${c.id}`} claim={c} a={view.assessments.get(c.id)} evidence={evidence}
          initiallyOpen={c.is_primary || filter !== "all"} running={running} onVote={(v) => props.onVote(c.id, v)} />)}
        {!claims.length && <p className="empty">No claims with this outcome.</p>}
      </section>}
      {result && !view.claims.length && <section className="section">
        <h3>{running ? "Checks so far" : "No assessable claims"}</h3>
        <p className="note">{running ? "Gathering evidence before individual claims are assessed." : "This run did not produce individual claim assessments. Review the analysis details and limitations below."}</p>
        {checks.length > 0 && <div className="ev-list">{checks.slice(0, 6).map((e) => <Evidence key={e.id} item={e} relation="context" />)}</div>}
      </section>}
      {!!result?.missing_info.length && <section className="section" aria-label="Information that would help">
        <h3>Information that would help</h3><ul>{result.missing_info.map((m, i) => <li key={i}><Text value={m} /></li>)}</ul>
        {result.reply_draft && <><button className="btn" onClick={async () => setReplyMsg(await insertReply(result.reply_draft!))}>Insert suggested reply</button>
          <span className="note">Adds a draft to the comment box for your review.</span>{replyMsg && <p className="note" role="status">{replyMsg}</p>}</>}
      </section>}
      {result && <section className="section methodology"><details>
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
      </details><p className="note privacy">Assesses technical claims. Nothing is posted to GitHub automatically.</p></section>}
    </div>}
  </div>;
}
