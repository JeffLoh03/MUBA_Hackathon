# Verity Desk Frontend

React, TypeScript and Vite frontend based directly on the team's `MUBA_Hackathon` code.

The original mock timer and sample report have been replaced with the real Python stream at `/api/verify/stream`. Keep the FastAPI backend on port `8000` while developing:

The composer accepts text claims, article URLs, and JPG/PNG/WEBP image attachments. Image requests use `/api/verify/image/stream` and remain in memory only.

```powershell
npm install
npm run dev
```

Validation:

```powershell
npm run lint
npm run build
```
