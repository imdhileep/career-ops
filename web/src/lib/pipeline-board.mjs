// Kanban grouping for the Pipeline board view. Pure, so `node --test` covers it.
// statusToken is the tabs' normalizer (strips dates, bold, aliases), so a
// card always sits in the column matching the tab that counts it.
import { statusToken } from "./pipeline-sankey.mjs";

export const OTHER_COLUMN = "Other";

/**
 * Group tracker rows into one column per canonical state, in the given order.
 * A status that does not resolve to a canonical state goes to "Other", which
 * only appears when it has rows. Row order inside a column is preserved, so
 * the caller's sort (score by default) carries over to the board.
 * @template {{status: string}} T
 * @param {T[]} rows
 * @param {readonly string[]} states canonical labels, e.g. CANONICAL_STATES
 * @returns {{status: string, rows: T[]}[]}
 */
export function boardColumns(rows, states) {
  const byToken = new Map(states.map((s) => [s.toUpperCase(), s]));
  /** @type {Map<string, T[]>} */
  const groups = new Map(states.map((s) => [s, []]));
  /** @type {T[]} */
  const other = [];
  for (const row of rows) {
    const label = byToken.get(statusToken(row.status));
    if (label) groups.get(label).push(row);
    else other.push(row);
  }
  const columns = states.map((s) => ({ status: s, rows: groups.get(s) }));
  if (other.length > 0) columns.push({ status: OTHER_COLUMN, rows: other });
  return columns;
}

/**
 * The status a drop onto `target` should save, or null when nothing changes
 * (same column, or the synthetic "Other" column, which is not a real state).
 * @param {string} current
 * @param {string} target
 * @param {readonly string[]} states
 * @returns {string | null}
 */
export function dropStatus(current, target, states) {
  if (target === OTHER_COLUMN || !states.includes(target)) return null;
  return statusToken(current) === target.toUpperCase() ? null : target;
}

/** Board vs table, from `?view=`. Anything else is the table, the default. */
export function pipelineLayout(raw) {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v === "board" ? "board" : "table";
}
