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
| `src/App.tsx` | sign-in gate, the Proxmox-style shell (header, resource tree, panel, task list), hash routing |
| `src/components/ui.tsx`, `Tree.tsx`, `TaskLog.tsx` | building blocks (`View` panel with section menu), the resource tree, the task list |
| `src/components/ContextMenu.tsx` | right-click menus (`showMenu`, `openDialog`, the `ContextMenuHost` in the shell) |
| `src/api.ts`, `src/hooks.ts` | API client (JSON, CSRF header, 401 handling); `useRoute`, polling `useApi` |
| `src/components/Topology.tsx` | the map with the plan/draft overlay and the details panel |
| `src/components/RangeEditing.tsx`, `SegmentEditing.tsx` | editing a range on the map (VMs, services, segments, traffic rules), the draft bar, the range chat |
| `src/components/ChatFeed.tsx` | chat feed: streamed steps, approval cards, question cards |
| `src/pages/` | Dashboard, RangeDetail, Vm, RangeEditor, Builder, Chats, Blueprints, Library, Jobs, Console, Users, Audit, Settings, Account, SignIn |

More: [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#the-web-ui-web) and [docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md#the-web-ui).
