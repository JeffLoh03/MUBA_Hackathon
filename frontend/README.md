# Verity Desk Frontend

React/Vinext prototype for evidence-led news verification. The current version uses mock data and simulates the complete DeepSeek, Kimi, MiniMax, and rule-engine workflow.

## Project Structure

- `app/components/` - reusable interface sections and workflows
- `app/data/` - mock verification results
- `app/hooks/` - progress and verification state
- `app/types/` - shared TypeScript contracts
- `app/login/` - mock sign-in route
- `public/` - static and social-preview assets
- `tests/` - server-render checks

## Local Development

```bash
npm install
npm run dev
```

Open `http://localhost:3000`. Enter any complete `http://` or `https://` URL to run the simulated verification.

## Verification

```bash
npm run build
npm test
```
