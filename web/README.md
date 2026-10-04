# VALOR web UI

React + TypeScript + Vite, with React Flow (`@xyflow/react`) for the topology map. The built files in `dist/` are
committed, so installing VALOR never needs Node.js on the cluster.

```bash
cd web
npm ci                 # exact versions from package-lock.json
npm run build          # type-check, then build into dist/
npm run dev            # http://localhost:5173, proxies /api to `valor-web serve --port 8080`
```

For local development against a test database:

```bash
VALOR_CONFIG=dev.toml valor-web serve --port 8080   # dev.toml: [web] secure_cookies = false, db = "...", ...
```

After changing anything under `src/`, rebuild and commit `dist/` together with the source. CI rebuilds and
fails if `dist/` is out of date.

| Path | What |
|---|---|
| `src/App.tsx` | sign-in gate, app shell, hash routing |
| `src/api.ts` | API client (JSON, CSRF header, 401 handling) |
| `src/components/Topology.tsx` | the view-only map with the change overlay |
| `src/pages/` | Dashboard, Ranges, RangeDetail (map, hosts, tests, verification, plan approval), RangeEditor, Jobs, Users, Audit, Settings, Account, SignIn |
