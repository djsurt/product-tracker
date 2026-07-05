---
target: app UI — clutter/simplification review
total_score: 29
p0_count: 0
p1_count: 2
timestamp: 2026-07-04T20-11-07Z
slug: api-templates-app-ui-clutter-review
---
## Design Health Score (clutter/simplification lens)

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 4 | Live dot, skeletons, htmx indicators — excellent |
| 2 | Match System / Real World | 2 | Internal jargon leaks: "Search query" field, "Query:" on detail, raw source codes (mock/scraper/rapidapi) shown to shoppers |
| 3 | User Control and Freedom | 3 | Good; delete confirms, pause/resume present |
| 4 | Consistency and Standards | 3 | Three competing primary (orange) CTAs in one card breaks the "one primary action" rule |
| 5 | Error Prevention | 3 | Confirms on destructive actions |
| 6 | Recognition Rather Than Recall | 3 | Mostly good |
| 7 | Flexibility and Efficiency | 3 | Three add methods is flexible but all shown at once |
| 8 | Aesthetic and Minimalist Design | 2 | The wishlist opens with a 3-form wall + a developer MCP card on the shopper's primary page |
| 9 | Error Recovery | 3 | Friendly failed-source messaging |
| 10 | Help and Documentation | 3 | Inline hints throughout |
| **Total** | | **29/40** | **Good — solid foundation, clutter concentrated in 2 places** |

## Anti-Patterns Verdict

Deterministic detector: **clean** (0 findings across all templates). No gradient text, side-stripe borders, eyebrow kickers, or identical card grids. The design system (`base.html`) is genuinely well-built — OKLCH tokens, paired-text verdict badges, accessible focus states, reduced-motion handling.

The problems are **structural clutter and audience mismatch**, not visual slop.

## What's Working

1. **The verdict hero on item detail** — big price, plain-language headline ("Below your target — good time to buy"), one clear Buy button. This is the product's thesis delivered perfectly.
2. **Empty states teach** — every empty view has a warm illustration + a next action, not "nothing here."
3. **Accessible verdicts** — badges pair color with a text label; honors `prefers-reduced-motion`.

## Priority Issues

### [P1] The "Track a new product" card is three parallel forms stacked with three primary CTAs
The wishlist opens with manual (Title + Search query + Target), URL paste, and screenshot upload — all expanded, ~7 fields, 3 orange buttons competing. Fails cognitive-load "single focus / minimal choices / chunking." Contradicts PRODUCT.md ("low patience for clutter", "answer the one question first").
**Fix:** One primary input (a single field that takes a URL *or* a search term), one primary button. Demote the other two methods behind a "More ways to add" disclosure or tabs. Only one orange CTA visible at rest.
**Command:** `/impeccable distill` (structure) → `/impeccable layout`

### [P1] Developer surfaces sit on the shopper's primary page and nav
The wishlist page ends with a "Connect Claude (MCP)" card (raw CLI command + token form + token table); the top nav shows "Ops". PRODUCT.md: developer is a *secondary* audience, "the design serves the shopper."
**Fix:** Move MCP tokens to a Settings/Integrations page; remove "Ops" from the shopper nav (gate to admin, or move under an account menu).
**Command:** `/impeccable shape` (IA) → `/impeccable distill`

### [P2] Internal jargon leaks to shoppers
"Search query" required field, "Query: sony xm4s" on item detail, and raw source codes ("mock", "scraper", "rapidapi") in the Browse hint and offers table.
**Fix:** Hide/auto-manage the query (already JS-mirrored — drop the visible field into an "Advanced" toggle). Map source codes to friendly store names; suppress mock/scraper in the shopper view.
**Command:** `/impeccable clarify`

### [P2] "Below target" is restated 3× on item detail
Verdict shows a "below target" badge + headline "Below your target — good time to buy" + "$200 under your $300 target" — plus a separate Price-alerts card with a "below target" rule. Redundant and conceptually overlapping (does setting a target also set an alert?).
**Fix:** Keep the headline + delta; drop the duplicate badge in the verdict's right column. Clarify the target ↔ alerts relationship (or auto-create the below-target alert from the target).
**Command:** `/impeccable distill` → `/impeccable clarify`

### [P3] Offers table has low-signal repeated columns
Stock ("in stock") and Checked ("just now") repeat identically on every row.
**Fix:** Show a stock badge only when NOT in stock; move "checked N ago" to a single table caption ("Prices checked just now").
**Command:** `/impeccable distill`

## Persona Red Flags

**Jordan (First-Timer):** Lands on the wishlist, sees three forms and a "Search query" field — unsure which to use or what a "query" is. Sees a CLI command ("claude mcp add --transport http…") on a shopping site — confusing.
**Casey (Mobile):** The 3-form add card pushes the actual wishlist far below the fold; three stacked forms on a phone is a long scroll before any content.

## Minor Observations
- Three orange buttons in one card dilutes "orange = the action to take."
- Item-detail target-price form floats between the "Query:" line and the offers card with no grouping.
- Browse hint exposing "mock, scraper" undercuts trust ("is this real data?").
