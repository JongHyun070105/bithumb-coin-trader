# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-28
- Primary product surfaces: Quant operations dashboard for research infrastructure, collector observability, safety posture, and event review.
- Evidence reviewed: repository README and docs at commit `6085218`; user-provided UI v0.1 brief. No existing frontend, design system, brand assets, or dashboard implementation was present.

## Brand
- Personality: precise, calm, technical, operational, and evidence-first.
- Trust signals: explicit data provenance labels, visible safety locks, restrained status color, timestamps, and clear unavailable/unknown states.
- Avoid: brokerage-app spectacle, fake profitability, neon gradients, glassmorphism, decorative motion, oversized cards, and controls that imply live authority.

## Product goals
- Goals: make system posture scannable in seconds; expose evidence quality alongside values; create a stable frontend shell for later V9.x/AWS APIs.
- Non-goals: backend integration, alpha research, portfolio truth, live controls, exchange credentials, and trading activation.
- Success signals: all core screens render from typed mock data; HEALTHY/DEGRADED/FAIL/UNKNOWN/LOCKED are unambiguous; unavailable data is never rendered as zero.

## Personas and jobs
- Primary personas: quant researcher, trading-system operator, and infrastructure auditor.
- User jobs: confirm safety posture, detect collector degradation, understand which claims are measured, inspect recent events, and navigate future operational surfaces.
- Key contexts of use: desktop monitoring during research and responsive read-only checks on mobile.

## Information architecture
- Primary navigation: Overview, Collector Health, Safety Center, Logs / Events; future placeholders for Trading, Performance, Research Lab, and AWS / Infrastructure.
- Core routes/screens: implemented as client-side view state in v0.1 with shareable semantics deferred until routing becomes necessary.
- Content hierarchy: environment banner and global posture first; exceptions and locks second; detailed evidence and timelines third.

## Design principles
- Evidence travels with the metric: every operational value may carry MEASURED, ESTIMATED, NOT VERIFIABLE, or NOT AVAILABLE.
- Absence is not zero: unknown PnL, drops, or reconciliation values remain explicitly unavailable.
- Safety has visual priority: locked/failed controls are prominent and read-only, with no activation toggle.
- Dense, not cramped: compact rows and cards use consistent alignment, whitespace, and typography rather than decorative separation.
- Tradeoffs: desktop information density is prioritized while mobile collapses tables into stacked, labeled records.

## Visual language
- Color: near-black graphite surfaces; cool neutral text; green healthy, amber degraded/warn, red fail/error, blue informational, violet research, and muted gray unknown/unavailable.
- Typography: system sans for interface text; system monospace for metrics, identifiers, and timestamps.
- Spacing/layout rhythm: 4px base rhythm; compact 8/12/16px spacing; maximum content width remains fluid for operations displays.
- Shape/radius/elevation: 8–12px radii, hairline borders, minimal shadows, no floating glass cards.
- Motion: only short state/focus transitions; reduced-motion preference disables nonessential transitions.
- Imagery/iconography: Lucide line icons only; no stock imagery or financial illustration.

## Components
- Existing components to reuse: none.
- New/changed components: app shell, navigation rail, status badge, evidence label, metric card, collector stream table, safety checklist, event filters, event timeline, placeholder panel, mobile header.
- Variants and states: HEALTHY, DEGRADED, FAIL, UNKNOWN, LOCKED; INFO, WARN, ERROR, CRITICAL; active/inactive navigation and filters.
- Token/component ownership: CSS custom properties in `dashboard/src/index.css`; typed UI contracts in `dashboard/src/data/mockData.ts`.

## Accessibility
- Target standard: WCAG 2.2 AA for contrast, keyboard access, landmarks, and readable status text.
- Keyboard/focus behavior: all navigation/filter controls are native buttons with visible focus rings and current-state semantics.
- Contrast/readability: status is conveyed by text and icons in addition to color; compact type never drops below 12px for metadata.
- Screen-reader semantics: semantic headings, nav/main/aside landmarks, tables with headers, lists for event timelines, and status labels.
- Reduced motion and sensory considerations: respect `prefers-reduced-motion`; no flashing, pulsing, or ambient animation.

## Responsive behavior
- Supported breakpoints/devices: desktop-first at 1280px+, compact desktop/tablet from 760px, mobile down to 360px.
- Layout adaptations: desktop rail becomes a compact horizontal header; metric grids collapse; stream tables become card-like rows; secondary metadata wraps rather than truncates critical state.
- Touch/hover differences: minimum 40px navigation targets on mobile; hover is supplemental and never the only indication.

## Interaction states
- Loading: future skeleton rows must preserve layout and retain the DEVELOPMENT banner.
- Empty: explicit “No events match this filter” or “Not available in UI v0.1” copy.
- Error: red FAIL/ERROR state with evidence context, never silent fallback to zero.
- Success: calm HEALTHY state without celebratory motion.
- Disabled: read-only safety controls are rendered as LOCKED status rows, not disabled toggles.
- Offline/slow network, if applicable: future API shell must preserve last-updated time and change evidence to stale/unknown.

## Content voice
- Tone: concise, factual, audit-friendly, and non-promotional.
- Terminology: use collector, stream, evidence, research, audit, locked, unavailable, and not verifiable consistently.
- Microcopy rules: uppercase short operational states; use sentence case for explanations; never imply returns or readiness without evidence.

## Implementation constraints
- Framework/styling system: React, TypeScript, Vite, plain CSS design tokens, Lucide icons. No Python dependency changes.
- Design-token constraints: components consume semantic CSS variables; raw hex values stay in the token layer.
- Performance constraints: no charting library in v0.1 without real timeseries needs; keep bundle and runtime simple.
- Compatibility constraints: current evergreen desktop/mobile browsers; frontend isolated under `dashboard/`.
- Test/screenshot expectations: typecheck, ESLint, production build, focused component tests, Playwright desktop/mobile smoke, and screenshot artifacts under `dashboard/output/playwright/` (ignored from Git).

## Open questions
- [ ] Choose API transport and authentication after V9.1/AWS monitor contracts exist; backend owner; blocks live data integration only.
- [ ] Decide whether ECharts is warranted once real timeseries and research plots are specified; frontend owner; no impact on v0.1.
- [ ] Define portfolio/PnL authorization and redaction policy before exposing any account data; safety owner; blocks portfolio integration.
