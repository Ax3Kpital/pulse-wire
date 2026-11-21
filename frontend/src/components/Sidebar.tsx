import * as Collapsible from "@radix-ui/react-collapsible";
import { useEffect, useState } from "react";
import type { CategoryNode, SourceInfo } from "../types/api";
import { SourceHealth } from "./SourceHealth";

interface Props {
  categories: CategoryNode[];
  sources: SourceInfo[];
  active: string | null;
  onSelect: (slug: string | null) => void;
}

export function Sidebar({ categories, sources, active, onSelect }: Props) {
  return (
    <aside className="sidebar" aria-label="Categories and sources">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">N</span>
        <span className="brand-name">Pulse Wire</span>
      </div>

      <nav aria-label="Categories">
        <ul className="nav-list">
          <li>
            <button
              type="button"
              className={`nav-item ${active === null ? "is-active" : ""}`}
              aria-current={active === null ? "page" : undefined}
              onClick={() => onSelect(null)}
            >
              <span>All stories</span>
            </button>
          </li>
          {categories.map((cat) => (
            <CategoryItem key={cat.slug} node={cat} active={active} onSelect={onSelect} />
          ))}
        </ul>
      </nav>

      <SourceHealth sources={sources} />
    </aside>
  );
}

function CategoryItem({
  node,
  active,
  onSelect,
}: {
  node: CategoryNode;
  active: string | null;
  onSelect: (slug: string | null) => void;
}) {
  const childActive = node.children.some((c) => c.slug === active);
  const [open, setOpen] = useState(childActive);
  useEffect(() => {
    if (childActive) setOpen(true);
  }, [childActive]);

  const button = (
    <button
      type="button"
      className={`nav-item ${active === node.slug ? "is-active" : ""}`}
      aria-current={active === node.slug ? "page" : undefined}
      onClick={() => onSelect(node.slug)}
    >
      <span>{node.name}</span>
      <span className="count" aria-label={`${node.count} stories`}>
        {node.count}
      </span>
    </button>
  );

  if (!node.children.length) return <li>{button}</li>;

  return (
    <li>
      <Collapsible.Root open={open} onOpenChange={setOpen}>
        <div className="nav-row">
          {button}
          <Collapsible.Trigger className="disclosure" aria-label={`${open ? "Hide" : "Show"} ${node.name} filters`}>
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" className={open ? "rot" : ""}>
              <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </Collapsible.Trigger>
        </div>
        <Collapsible.Content>
          <ul className="nav-sub">
            {node.children.map((child) => (
              <li key={child.slug}>
                <button
                  type="button"
                  className={`nav-item sub ${active === child.slug ? "is-active" : ""}`}
                  aria-current={active === child.slug ? "page" : undefined}
                  onClick={() => onSelect(child.slug)}
                >
                  <span>{child.name}</span>
                  <span className="count">{child.count}</span>
                </button>
              </li>
            ))}
          </ul>
        </Collapsible.Content>
      </Collapsible.Root>
    </li>
  );
}
