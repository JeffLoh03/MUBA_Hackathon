# Verity Desk Frontend

Local React, Vite, and TypeScript prototype for evidence-led news verification. It uses mock data to simulate the DeepSeek, Kimi, MiniMax, and rule-engine workflow.

## Structure

- `src/components/` - reusable interface and workflow components
- `src/pages/` - route-level pages
- `src/hooks/` - verification progress and state
- `src/data/` - mock verification results
- `src/types/` - shared TypeScript contracts
- `public/` - static assets

## Development

```bash
npm install
npm run dev
```

Open `http://localhost:5173`.

## Validation

```bash
npm run lint
npm run build
```
