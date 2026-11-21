import * as Collapsible from "@radix-ui/react-collapsible";
import { useState } from "react";
import {
  IMPACT_LABEL,
  describeImpact,
  describeMatch,
  describeTone,
  formatAbsolute,
  formatRelative,
  hostOf,
} from "../lib/format";
import type { ImpactLevel, ImpactReason, Story, Tone } from "../types/api";

interface Props {
  story: Story;
  now: Date;
  onCategory: (slug: string) => void;
}

export function StoryCard({ story, now, onCategory }: Props) {
  const [open, setOpen] = useState(false);
  const others = story.others;
  const otherSources = new Set(others.map((o) => o.source.key));
  otherSources.delete(story.primary_source.key);

  return (
    <article
      className={`card lvl-${story.impact_level}`}
      aria-labelledby={`story-${story.id}`}
    >
      <div className="card-meta">
        <ImpactBadge
          score={story.impact_score}
          level={story.impact_level}
          tone={story.tone}
          sentiment={story.sentiment}
          reasons={story.impact_reasons}
          scope="Story"
        />
        <span className="source-badge">{story.primary_source.name}</span>
        {story.primary_source.is_demo && <span className="tag-demo">demo</span>}
        {story.primary_source.paywalled && <PaywallTag />}
        <span className="sep" aria-hidden="true">·</span>
        <time dateTime={story.published_at} title={formatAbsolute(story.published_at)}>
          {formatRelative(story.published_at, now)}
        </time>
        {story.source_count > 1 && (
          <>
            <span className="sep" aria-hidden="true">·</span>
            <span className="multi">{story.source_count} sources</span>
          </>
        )}
      </div>

      <h2 className="card-title" id={`story-${story.id}`}>
        <a href={story.url} target="_blank" rel="noopener noreferrer">
          {story.title}
        </a>
      </h2>

      {story.excerpt && <p className="excerpt">{story.excerpt}</p>}

      <div className="card-foot">
        <ul className="chips" aria-label="Categories">
          {story.categories.map((c) => (
            <li key={c.slug}>
              <button type="button" className="chip" onClick={() => onCategory(c.slug)} title={`Score ${c.score}`}>
                {c.name}
              </button>
            </li>
          ))}
        </ul>
        <a className="open-original" href={story.url} target="_blank" rel="noopener noreferrer">
          Open original
          <span className="sr-only"> on {hostOf(story.url)} (opens in a new tab)</span>
          <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
            <path d="M6 3h7v7M13 3L5.5 10.5M11 13H3V5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </a>
      </div>

      {others.length > 0 && (
        <Collapsible.Root open={open} onOpenChange={setOpen} className="others">
          <Collapsible.Trigger className="others-toggle">
            <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" className={open ? "rot" : ""}>
              <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {open ? "Hide" : "Show"} {others.length} more {others.length === 1 ? "report" : "reports"}
            {otherSources.size > 0 && <span className="muted"> — {[...new Set(others.map((o) => o.source.name))].join(", ")}</span>}
          </Collapsible.Trigger>
          <Collapsible.Content>
            <ul className="report-list">
              {others.map((o) => (
                <li key={o.item_id} className="report">
                  <div className="report-meta">
                    {o.impact_level && (
                      <ImpactBadge
                        score={o.impact_score ?? 1}
                        level={o.impact_level}
                        tone={o.tone ?? "neutral"}
                        sentiment={o.sentiment ?? 0}
                        reasons={o.impact_reasons ?? []}
                        scope="Report"
                        compact
                      />
                    )}
                    <span className="report-source">{o.source.name}</span>
                    {o.source.paywalled && <PaywallTag />}
                    <span className="sep" aria-hidden="true">·</span>
                    <time dateTime={o.published_at} title={formatAbsolute(o.published_at)}>
                      {formatRelative(o.published_at, now)}
                    </time>
                  </div>
                  <a href={o.url} target="_blank" rel="noopener noreferrer" className="report-title">
                    {o.title}
                  </a>
                  <p className="why" title="Why this report was grouped here">
                    {describeMatch(o.method, o.match_reason)}
                  </p>
                </li>
              ))}
            </ul>
          </Collapsible.Content>
        </Collapsible.Root>
      )}
    </article>
  );
}

function PaywallTag() {
  return (
    <span className="tag-paywall" title="Subscription required to read the full article">
      paywall
    </span>
  );
}

interface BadgeProps {
  score: number;
  level: ImpactLevel;
  tone: Tone;
  sentiment: number;
  reasons: ImpactReason[];
  scope: "Story" | "Report";
  compact?: boolean;
}

const TONE_ICON: Record<Tone, string> = { negative: "▼", neutral: "●", positive: "▲" };

/** Coloured 1–10 impact score; the tooltip explains every point of it. */
export function ImpactBadge({ score, level, tone, sentiment, reasons, scope, compact = false }: BadgeProps) {
  const label = IMPACT_LABEL[level];
  const lines = describeImpact(reasons);
  const title = [`${scope} impact ${score}/10 — ${label}`, describeTone(tone, sentiment), ...lines].join("\n");
  return (
    <span className={`impact-badge impact-${level}${compact ? " is-compact" : ""}`} title={title}>
      <span className="impact-score">{score}</span>
      {!compact && (
        <>
          <span className="impact-label">{label}</span>
          <span className={`tone tone-${tone}`} aria-hidden="true">
            {TONE_ICON[tone]}
          </span>
        </>
      )}
      <span className="sr-only">
        {" "}
        out of 10, {label.toLowerCase()} impact, {describeTone(tone, sentiment).toLowerCase()}. Why: {lines.join("; ")}
      </span>
    </span>
  );
}
