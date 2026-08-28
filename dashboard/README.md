# Quant Operations Dashboard UI v0.1

Frontend-only development foundation for future V9.x and AWS monitoring integrations. It is intentionally isolated from the Python collector and trading runtime.

## Safety boundary

- All displayed values are static mock fixtures.
- No collector, account, order, API key, AWS resource, or trading backend is connected.
- Live trading and new entries are read-only, disabled states with no override controls.
- Missing trading data is shown as `NOT AVAILABLE`, never as zero.
- Unprovable V9 metrics are shown as `NOT VERIFIABLE`.

## Local development

```sh
npm install
npm run dev
```

## Verification

```sh
npm run typecheck
npm run lint
npm test
npm run build
```

The current UI includes Overview, Collector Health, Safety Center, and Logs / Events. Trading, Performance, Research Lab, and AWS / Infrastructure are navigation placeholders for later evidence-gated integrations.
