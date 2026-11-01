"use client";

import { useState, type DragEvent, type MouseEvent } from "react";
import Link from "next/link";
import type { Application } from "@/lib/career-ops";
import { CANONICAL_STATES, scoreTone, statusDot } from "@/lib/format";
import { applicationKey } from "@/lib/pipeline-status.mjs";
import { boardColumns, dropStatus, OTHER_COLUMN } from "@/lib/pipeline-board.mjs";
import { companyPresentation } from "@/lib/company-presentation.mjs";
import { CompanyLogo } from "@/components/company-logo";
import { cn } from "@/lib/cn";

const TONE: Record<string, string> = {
  good: "text-emerald-500",
  warn: "text-amber-500",
  muted: "text-muted",
  bad: "text-red-500",
};

/**
 * Kanban view of the tracker. Drag a card to another column — or use the
 * card's status menu (keyboard / touch) — to change its status. Every move
 * goes through the same /api/status writer as the table, via `onMove`.
 */
export function PipelineBoard({
  rows,
  detailQuery,
  onNavigate,
  onMove,
}: {
  rows: Application[];
  detailQuery: string;
  onNavigate: (event: MouseEvent<HTMLAnchorElement>) => void;
  onMove: (row: Application, status: string) => Promise<void>;
}) {
  const states = CANONICAL_STATES as readonly string[];
  const columns = boardColumns(rows, states);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overColumn, setOverColumn] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const byKey = new Map(rows.map((r) => [applicationKey(r), r]));

  async function move(row: Application, target: string) {
    const next = dropStatus(row.status, target, states);
    if (!next) return;
    const key = applicationKey(row);
    setBusyKey(key);
    try {
      await onMove(row, next);
    } finally {
      setBusyKey((k) => (k === key ? null : k));
    }
  }

  function onDrop(event: DragEvent<HTMLElement>, target: string) {
    event.preventDefault();
    setOverColumn(null);
    const key = event.dataTransfer.getData("text/plain") || dragKey;
    setDragKey(null);
    const row = key ? byKey.get(key) : undefined;
    if (row) void move(row, target);
  }

  return (
    <div className="mt-4 flex gap-3 overflow-x-auto pb-3" aria-label="Pipeline board">
      {columns.map((col) => {
        const droppable = col.status !== OTHER_COLUMN;
        return (
          <section
            key={col.status}
            aria-label={`${col.status}, ${col.rows.length} application${col.rows.length === 1 ? "" : "s"}`}
            onDragOver={droppable ? (e) => { e.preventDefault(); setOverColumn(col.status); } : undefined}
            onDragLeave={droppable ? () => setOverColumn((c) => (c === col.status ? null : c)) : undefined}
            onDrop={droppable ? (e) => onDrop(e, col.status) : undefined}
            className={cn(
              "flex shrink-0 flex-col rounded-2xl border border-border bg-surface/40 transition-[width]",
              // Empty columns stay droppable but narrow, so more stages fit on screen.
              col.rows.length > 0 || overColumn === col.status ? "w-60" : "w-40",
              overColumn === col.status && "border-brand/60 bg-brand-soft",
            )}
          >
            <header className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted">
              <span className={cn("size-2 rounded-full", statusDot(col.status))} aria-hidden="true" />
              {col.status}
              <span className="ml-auto tabular-nums text-faint">{col.rows.length}</span>
            </header>
            <ol className="flex min-h-24 flex-col gap-2 p-2">
              {col.rows.map((r) => {
                const key = applicationKey(r);
                const href = `/pipeline/${encodeURIComponent(r.n)}${detailQuery ? `?${detailQuery}` : ""}`;
                const company = companyPresentation(r);
                return (
                  <li
                    key={key}
                    draggable={busyKey !== key}
                    onDragStart={(e) => {
                      e.dataTransfer.setData("text/plain", key);
                      e.dataTransfer.effectAllowed = "move";
                      setDragKey(key);
                    }}
                    onDragEnd={() => { setDragKey(null); setOverColumn(null); }}
                    aria-busy={busyKey === key}
                    className={cn(
                      "cursor-grab rounded-xl border border-border bg-surface p-2.5 text-sm shadow-sm active:cursor-grabbing",
                      dragKey === key && "opacity-50",
                      busyKey === key && "animate-pulse",
                    )}
                  >
                    <Link href={href} onClick={onNavigate} className="flex items-center gap-2 font-medium hover:text-brand-text">
                      <CompanyLogo name={company.label} size={20} />
                      <span className="truncate">{company.label}</span>
                      <span className={cn("ml-auto tabular-nums text-xs", TONE[scoreTone(r.score)])}>{r.score}</span>
                    </Link>
                    <p className="mt-1 line-clamp-2 text-xs text-muted">{r.role}</p>
                    <div className="mt-2 flex items-center gap-2 text-[11px] text-faint">
                      <span className="tabular-nums">#{r.n}</span>
                      {r.date && <span className="tabular-nums">{r.date}</span>}
                      <select
                        aria-label={`Move ${company.label} ${r.role} to status`}
                        value=""
                        disabled={busyKey === key}
                        onChange={(e) => { if (e.target.value) void move(r, e.target.value); }}
                        className="ml-auto rounded border border-border bg-surface px-1 py-0.5 text-[11px] text-muted"
                      >
                        <option value="">Move…</option>
                        {states.filter((s) => dropStatus(r.status, s, states)).map((s) => (
                          <option key={s} value={s}>{s}</option>
                        ))}
                      </select>
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>
        );
      })}
    </div>
  );
}
