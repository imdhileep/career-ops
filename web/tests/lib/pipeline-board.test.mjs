import { test } from "node:test";
import assert from "node:assert/strict";
import { boardColumns, dropStatus, pipelineLayout, OTHER_COLUMN } from "../../src/lib/pipeline-board.mjs";

const STATES = ["Evaluated", "Applied", "Interview", "Offer", "Rejected", "SKIP"];

test("boardColumns puts each row under its canonical state, keeping order", () => {
  const rows = [
    { n: "1", status: "Applied" },
    { n: "2", status: "evaluated" },
    { n: "3", status: "**Interview**" },
    { n: "4", status: "applied" },
  ];
  const cols = boardColumns(rows, STATES);
  assert.deepEqual(cols.map((c) => c.status), STATES);
  assert.deepEqual(cols.find((c) => c.status === "Applied").rows.map((r) => r.n), ["1", "4"]);
  assert.deepEqual(cols.find((c) => c.status === "Evaluated").rows.map((r) => r.n), ["2"]);
  assert.deepEqual(cols.find((c) => c.status === "Interview").rows.map((r) => r.n), ["3"]);
});

test("boardColumns adds an Other column only for unknown statuses", () => {
  assert.equal(boardColumns([{ status: "Applied" }], STATES).length, STATES.length);
  const cols = boardColumns([{ status: "weird" }, { status: "" }], STATES);
  assert.equal(cols.at(-1).status, OTHER_COLUMN);
  assert.equal(cols.at(-1).rows.length, 2);
});

test("boardColumns resolves aliases (no aplicar -> SKIP)", () => {
  const cols = boardColumns([{ status: "no aplicar" }], STATES);
  assert.equal(cols.find((c) => c.status === "SKIP").rows.length, 1);
});

test("dropStatus saves only real moves", () => {
  assert.equal(dropStatus("Evaluated", "Applied", STATES), "Applied");
  assert.equal(dropStatus("applied", "Applied", STATES), null);
  assert.equal(dropStatus("Applied", OTHER_COLUMN, STATES), null);
  assert.equal(dropStatus("Applied", "NotAState", STATES), null);
});

test("pipelineLayout defaults to table", () => {
  assert.equal(pipelineLayout("board"), "board");
  assert.equal(pipelineLayout(["board"]), "board");
  assert.equal(pipelineLayout(null), "table");
  assert.equal(pipelineLayout("kanban"), "table");
});
