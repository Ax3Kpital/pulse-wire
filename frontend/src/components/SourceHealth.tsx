import * as Collapsible from "@radix-ui/react-collapsible";
import { useState } from "react";
import { formatAbsolute, formatRelative } from "../lib/format";
import { useNow } from "../lib/hooks";
import type { SourceInfo } from "../types/api";

interface Props {
  sources: SourceInfo[];
}

const STATUS_LABEL: Record<SourceInfo["status"], string> = {
  ok: "Healthy",
  error: "Error",
  disabled: "Inactive",
  never: "Not fetched yet",
  running: "Fetching",
};

export function SourceHealth({ sources }: Props) {
  const now = useNow();
  const [showInactive, setShowInactive] = useState(false);
  const active = sources.filter((s) => s.enabled);
  const inactive = sources.filter((s) => !s.enabled);
  const errors = active.filter((s) => s.status === "error").length;
  const [open, setOpen] = useState(true);

  return (
    <section className="health" aria-label="Source health">
      <Collapsible.Root open={open} onOpenChange={setOpen}>
        <Collapsible.Trigger className="health-head">
          <span>Sources</span>
          <span className={`pill ${errors ? "pill-warn" : "pill-ok"}`}>
            {errors ? `${errors} with errors` : `${active.length} healthy`}
          </span>
        </Collapsible.Trigger>
        <Collapsible.Content>
          <ul className="source-list">
            {active.map((s) => (
              <SourceRow key={s.key} s={s} now={now} />
            ))}
          </ul>
          {inactive.length > 0 && (
            <>
              <button type="button" className="link-btn" onClick={() => setShowInactive((v) => !v)}>
                {showInactive ? "Hide" : "Show"} {inactive.length} inactive
              </button>
              {showInactive && (
                <ul className="source-list inactive">
                  {inactive.map((s) => (
                    <li key={s.key} className="source-row">
                      <span className="dot dot-off" aria-hidden="true" />
                      <span className="source-text">
                        <span className="source-name">{s.name}</span>
                        <span className="source-meta">{s.disabled_reason}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </Collapsible.Content>
      </Collapsible.Root>
    </section>
  );
}

function SourceRow({ s, now }: { s: SourceInfo; now: Date }) {
  const [open, setOpen] = useState(false);
  const latency = s.recent.find((h) => h.ok)?.latency_ms;

  return (
    <li>
      <Collapsible.Root open={open} onOpenChange={setOpen}>
        <Collapsible.Trigger className="source-row" aria-label={`${s.name}: ${STATUS_LABEL[s.status]}. Show details`}>
          <span className={`dot dot-${s.status}`} aria-hidden="true" />
          <span className="source-text">
            <span className="source-name">
              {s.name}
              {s.is_demo && <span className="tag-demo">demo</span>}
            </span>
            {s.status === "error" ? (
              <span className="source-meta error-text">{s.last_error}</span>
            ) : (
              <span className="source-meta">
                {s.last_success_at ? formatRelative(s.last_success_at, now) : STATUS_LABEL[s.status]}
              </span>
            )}
          </span>
        </Collapsible.Trigger>
        <Collapsible.Content>
          <dl className="source-details">
            <dt>Status</dt>
            <dd>{STATUS_LABEL[s.status]}</dd>
            <dt>Publisher</dt>
            <dd>{s.publisher}</dd>
            <dt>Last success</dt>
            <dd>{formatAbsolute(s.last_success_at)}</dd>
            {latency != null && (
              <>
                <dt>Latency</dt>
                <dd>{latency} ms</dd>
              </>
            )}
            <dt>Storage</dt>
            <dd>
              {s.policy.replace("_", " ")}
              {s.retention_days ? ` · ${s.retention_days} d` : ""}
            </dd>
            {s.last_error && (
              <>
                <dt>Last error</dt>
                <dd className="error-text">
                  {s.last_error} ({formatAbsolute(s.last_error_at)})
                </dd>
              </>
            )}
          </dl>
        </Collapsible.Content>
      </Collapsible.Root>
    </li>
  );
}
