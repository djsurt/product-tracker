# Product

## Register

product

## Users

The primary user is a budget-conscious online shopper who has a few specific
products in mind (headphones, a console, an appliance) and doesn't want to
babysit prices across a dozen sites. They add items to a wishlist, set a target
price, and want to be told — clearly and without effort — when a real deal
appears, then sent straight to it.

Their context: checking in occasionally (often on a phone), low patience for
clutter, and a single recurring question on every screen — *"is this a good
price right now, and should I act?"* This is also a personal learning project,
so the developer is a second audience, but the design serves the shopper.

## Product Purpose

Deal Hunter tracks the prices of a user's wishlisted products across multiple
sources, surfaces the best current offer, judges whether it's actually a good
deal (lowest-in-30-days, below-target, etc.), and notifies + redirects the user
when it's time to buy. Success is a user trusting the verdict enough to click
through and buy without second-guessing the price.

## Brand Personality

Friendly, reassuring, confident. Three words: **approachable, trustworthy,
upbeat.** It should feel like a helpful friend who watches prices for you and
gives you a clear thumbs-up — not a trading terminal. Warm and light rather
than dark and dense. The emotional goal is *relief and confidence at the moment
of decision* ("yes, buy it now") and *calm reassurance* the rest of the time
("we're watching, nothing to do yet").

## Anti-references

- **The engineer dashboard / dev-tool dark theme.** Dense, cold, monospace-y,
  Grafana/terminal energy. This is what the current UI looks like and what we're
  moving away from.
- **Coupon-site clutter.** Loud red "SALE!" banners, blinking discounts,
  aggressive upsells, ad-stuffed comparison tables (RetailMeNot, Slickdeals at
  their worst). Friendly is not the same as loud.
- **Generic SaaS hero-metric template.** Big gradient number, three supporting
  stats. This is a shopping tool, not a B2B analytics product.

## Design Principles

1. **Answer the one question first.** Every screen should make "is this a good
   price, should I buy?" obvious at a glance — verdict and best offer lead, the
   rest supports.
2. **Warmth without noise.** Friendly and inviting through color, roundness, and
   tone — but the price data stays the calm, legible center of attention.
3. **Trust through clarity.** Show the evidence (price history, the source, when
   it was checked) so the verdict feels earned, not asserted.
4. **Effortless on a phone.** Mobile is a first-class context; touch targets,
   single-column flow, and skimmable verdicts come first, not as an afterthought.
5. **Confidence at the decision.** When it's time to act, the call-to-action is
   unmistakable and the path to buy is one tap.

## Accessibility & Inclusion

Target WCAG 2.1 AA. Body text ≥4.5:1 contrast, large text ≥3:1 — especially
important since a warm/light palette tempts low-contrast muted grays. Price
verdicts must never rely on color alone (pair green/amber/red badges with text
labels), to stay legible for color-blind users. Honor
`prefers-reduced-motion` for any price-update or chart animations. Full keyboard
operability for the wishlist add/edit/delete flows.
