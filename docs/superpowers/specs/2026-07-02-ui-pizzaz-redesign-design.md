# UI "Pizzaz" Redesign — Design Spec

**Date:** 2026-07-02
**Status:** Proposed (user was AFK during brainstorming; recommended options chosen — review welcome)
**Branch:** `design-pizzaz`

## Problem

Deal Hunter's UI is competent but generic: cards, a table, a topbar, almost no
motion. The user wants it to look *designed* — standout visual identity,
animation, and transitions — instead of "a basic full-stack application."

## Direction chosen

**Distinctive, on-brand.** Stay inside the PRODUCT.md brand (friendly, warm,
trustworthy, persimmon accent, light surfaces) but push it until it's
memorable. Explicitly rejected: dark dev-tool theme, coupon-site loudness,
generic SaaS hero-metric template (all PRODUCT.md anti-references).

**Tech constraint:** stays Jinja + HTMX. Modern CSS (View Transitions API,
`@keyframes`, `@starting-style`, scroll/entrance choreography) plus small
vanilla JS. No frontend framework, no build step. Progressive enhancement +
`prefers-reduced-motion` honored throughout.

## What changes

### 1. Visual signature
- **Expressive price/verdict typography**: a display treatment for the money
  moments (big fluid price type, tabular numerals, tighter optical spacing).
  Consider a second font only if it earns its weight; otherwise push Hanken
  Grotesk's weights/sizes harder.
- **Verdict hero** on item detail becomes the centerpiece: verdict-colored
  ambient treatment (soft tint wash/gradient, not a flat box), animated badge,
  clear one-tap Buy CTA.
- **Charming empty states**: small inline SVG illustrations + friendly copy
  instead of dashed-border boxes.
- **Brand moments**: logo/wordmark treatment in topbar, subtle background
  texture/gradient so pages don't feel like plain #fafafa.

### 2. Motion system (all gated by `prefers-reduced-motion`)
- **Data moments**:
  - Price values tick/settle when they update (HTMX swap → brief highlight +
    count-settle animation).
  - Sparkline draws itself in (`stroke-dashoffset` animation).
  - Verdict reveal: badge + headline animate in when offers load.
  - **Deal celebration**: when verdict is `below_target`/`all_time_low`, a
    restrained one-time flourish (e.g., soft glow pulse on the Buy CTA) —
    confident, not confetti-cannon.
- **Swap & page transitions**:
  - Cross-document View Transitions for page navigation.
  - HTMX swaps animate: new rows slide/fade in with stagger, deleted rows
    collapse out, `htmx-settling` styled intentionally.
  - Skeleton shimmer placeholders replace "Loading offers…" text.
- **Micro-interactions**:
  - Buttons: hover lift + press, focus rings already good.
  - Inputs: label/focus polish.
  - Screenshot upload becomes a real dropzone (drag-over state, preview thumb,
    progress feel).
- **Entrances**: staggered card/list reveals on first paint; auth pages get a
  composed entrance.

### 3. Surfaces touched
`base.html` (stylesheet + topbar), `dashboard.html`, `_items.html`,
`item_detail.html`, `_offers.html`, `_alerts.html`, `_identify_result.html`,
`login.html`, `register.html`. Ops dashboard: inherits tokens but is not a
focus.

## Non-goals
- No framework/build-step; no dark mode (separate effort); no backend changes;
  no new pages or features; ops dashboard redesign.

## Accessibility
WCAG 2.1 AA maintained: contrast ≥4.5:1 body text, verdicts never color-only,
`prefers-reduced-motion` collapses all animation, full keyboard operability
preserved (HTMX swaps keep focus behavior sane).

## Testing / verification
- Existing pytest suite must stay green (templates keep same ids/attrs HTMX
  and tests rely on).
- Visual verification in a real browser (screenshots at mobile + desktop
  widths), including HTMX flows: add item, delete item, offers auto-refresh,
  identify upload.

## Approaches considered
1. **Polish + motion only** — lowest risk, least transformative. Rejected: user
   explicitly wants "standout," not incremental.
2. **Distinctive, on-brand** ✅ — chosen; honors the existing brand brief while
   delivering the "wow."
3. **Showpiece/portfolio-grade** — rejected: bends the calm/trustworthy brief
   and risks coupon-site energy.
