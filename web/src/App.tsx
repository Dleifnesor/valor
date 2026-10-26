import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get, post, setCsrf, setUnauthenticatedHandler } from "./api";
import { Me, RangeRow } from "./types";
import { go, useApi, useRoute, useStored } from "./hooks";
import { DropButton, Icon, Loading, Logo, MenuEntry, Modal, PageGuard, View } from "./components/ui";
import { ResourceTree, Selection } from "./components/Tree";
import { TaskLog } from "./components/TaskLog";
import { ContextMenuHost } from "./components/ContextMenu";
import { SignIn } from "./pages/SignIn";
import { Dashboard } from "./pages/Dashboard";
import { RangeView } from "./pages/RangeDetail";
import { VmView } from "./pages/Vm";
import { RangeEditor } from "./pages/RangeEditor";
import { Jobs, JobDetail } from "./pages/Jobs";
import { Users } from "./pages/Users";
import { Audit } from "./pages/Audit";
import { SETTINGS_TABS, Settings, SettingsTab } from "./pages/Settings";
import { Account } from "./pages/Account";
import { Library } from "./pages/Library";
import { Blueprints } from "./pages/Blueprints";
import { Builder } from "./pages/Builder";
import { Chats } from "./pages/Chats";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);

  const refreshMe = useCallback(() => {
    get<Me>("/api/auth/me")
      .then((m) => {
        setCsrf(m.csrf);
        setMe(m);
      })
      .catch(() => setMe(null))
      .finally(() => setChecked(true));
  }, []);

  useEffect(() => {
    setUnauthenticatedHandler(() => setMe(null));
    refreshMe();
  }, [refreshMe]);

  if (!checked) return <Loading />;
  if (!me || me.stage !== "full" || me.user.must_change_password) {
    return (
      <SignIn
        me={me}
        onSignedIn={(m) => {
          setCsrf(m.csrf);
          setMe(m);
        }}
      />
    );
  }
  return <PageGuard><Shell me={me} setMe={setMe} /></PageGuard>;
}

const NARROW = "(max-width: 860px)";

// The layout of Proxmox VE's web UI: a header, the resource tree on the left, the selected object's panel on the
// right and the task list along the bottom.
function Shell({ me, setMe }: { me: Me; setMe: (m: Me | null) => void }) {
  const route = useRoute();
  const ranges = useApi<{ ranges: RangeRow[]; cluster_error: string | null }>("/api/ranges", 10000);
  const [treeOpen, setTreeOpen] = useState(false);                                // phones: the slide-in tree
  const [treeHidden, setTreeHidden] = useStored("valor-nav-collapsed", false);     // wide screens
  const [logOpen, setLogOpen] = useStored("valor-tasklog", true);
  const [logTab, setLogTab] = useState<"tasks" | "notes">("tasks");
  const [task, setTask] = useState<string | null>(null);
  const isAdmin = me.user.role === "admin";
  const canOperate = isAdmin || me.user.role === "operator";

  useEffect(() => { setTreeOpen(false); setTask(null); }, [route.join("/")]);
  useEffect(() => {
    const on = (e: Event) => setTask((e as CustomEvent<string>).detail);
    window.addEventListener("valor-task", on);
    return () => window.removeEventListener("valor-task", on);
  }, []);
  useEffect(() => {
    if (!treeOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setTreeOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [treeOpen]);
  const toggleTree = () => (window.matchMedia(NARROW).matches ? setTreeOpen(!treeOpen) : setTreeHidden(!treeHidden));

  const signOut = async () => {
    try {
      await post("/api/auth/logout");
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    }
    setMe(null);
    go("dashboard");
  };

  const [p0, p1, p2, p3, p4, p5] = route;
  let selected: Selection | null = "root";
  let view: ReactNode;
  if (p0 === "ranges" && p1 && p1 !== "new" && (p2 === "vm" || p2 === "console") && p3) {
    // ranges/<range>/vm/<host>[/console[/serial]]; ranges/<range>/console/<host>/<vnc|serial> is the older form
    selected = `vm:${p1}/${p3}`;
    const consoleKind = p2 === "console" ? (p4 === "serial" ? "serial" : "vnc")
      : p4 === "console" ? (p5 === "serial" ? "serial" : "vnc") : null;
    view = <VmView key={`${p1}/${p3}`} range={p1} host={p3} console={consoleKind} canOperate={canOperate} />;
  } else if (p0 === "ranges" && p1 && p1 !== "new") {
    selected = `range:${p1}`;
    view = <RangeView key={p1} name={p1} section={p2} chatId={p2 === "chat" && p3 ? Number(p3) : undefined} canOperate={canOperate} />;
  } else if (p0 === "ranges" && p1 === "new") {
    selected = null;
    view = <View title="Create range"><RangeEditor canEdit={canOperate} /></View>;
  } else if (p0 === "builder") {
    selected = null;
    view = (
      <View title="Chat builder">
        {canOperate || p1 ? <Builder key={p1 ?? "new"} isAdmin={isAdmin} chatId={p1 ? Number(p1) : null} />
          : <div className="alert warn">The chat builder needs the operator role.</div>}
      </View>
    );
  } else if (p0 === "account") {
    selected = null;
    view = <View title={`User '${me.user.username}'`}><Account me={me} onChanged={setMe} /></View>;
  } else {
    view = <Datacenter me={me} route={route} ranges={ranges.data?.ranges ?? null} />;
  }

  return (
    <div className={`shell${logOpen ? "" : " log-closed"}${treeHidden ? " tree-hidden" : ""}`}>
      <header className="topbar">
        <button className="btn ghost menu-btn" onClick={toggleTree} aria-controls="resource-tree" aria-label="Resources"
          title="Show or hide the resource tree">
          <Icon name="menu" />
        </button>
        <a className="brand" href="#/dashboard">
          <Logo size={24} />
          <b>VALOR</b>
          <span className="version">Lab Ranges {me.version}</span>
        </a>
        <Search ranges={ranges.data?.ranges ?? []} />
        <span className="grow" />
        {canOperate && (
          <>
            <button className="btn primary" onClick={() => go("ranges/new")}><Icon name="plus" size={15} /> Create range</button>
            <button className="btn primary hide-narrow" onClick={() => go("builder")}><Icon name="chat" size={15} /> Chat builder</button>
          </>
        )}
        <UserMenu me={me} onSignOut={signOut} />
      </header>
      <div className="workspace">
        {treeOpen && <div className="nav-backdrop" onClick={() => setTreeOpen(false)} aria-hidden="true" />}
        <aside id="resource-tree" className={`tree-pane${treeOpen ? " open" : ""}`}>
          <PageGuard page={route.join("/")}>
            <ResourceTree ranges={ranges.data?.ranges ?? null} instance={me.instance || "VALOR"} selected={selected} canOperate={canOperate} />
          </PageGuard>
          {ranges.data?.cluster_error && <div className="alert warn small tree-alert">Live VM status unavailable: {ranges.data.cluster_error}</div>}
        </aside>
        <main className="content"><PageGuard page={route.join("/")}>{view}</PageGuard></main>
      </div>
      <TaskLog open={logOpen} onOpen={setLogOpen} tab={logTab} onTab={setLogTab} />
      {task && <Modal title="Task viewer" wide onClose={() => setTask(null)}><JobDetail id={task} /></Modal>}
      <ContextMenuHost page={route.join("/")} />
    </div>
  );
}

function Datacenter({ me, route, ranges }: { me: Me; route: string[]; ranges: RangeRow[] | null }) {
  const isAdmin = me.user.role === "admin";
  const canOperate = isAdmin || me.user.role === "operator";
  const menu: MenuEntry[] = [
    { id: "summary", label: "Summary", icon: "info", href: "dashboard" },
    { id: "chats", label: "Chats", icon: "chats", href: "chats" },
    { id: "blueprints", label: "Blueprints", icon: "copy", href: "blueprints" },
    { id: "library", label: "ISO library", icon: "disc", href: "library" },
    { id: "jobs", label: "Jobs", icon: "jobs", href: "jobs" },
  ];
  if (isAdmin) {
    menu.push(
      { id: "users", label: "Users", icon: "users", href: "users" },
      { id: "audit", label: "Audit log", icon: "audit", href: "audit" },
      ...SETTINGS_TABS.map((t) => ({ ...t, href: `settings/${t.id}` })),
    );
  }
  const [p0, p1] = route;
  let active = "summary";
  let page: ReactNode;
  if (p0 === "chats") { active = "chats"; page = <Chats isAdmin={isAdmin} />; }
  else if (p0 === "blueprints") { active = "blueprints"; page = <Blueprints canOperate={canOperate} />; }
  else if (p0 === "library") { active = "library"; page = <Library canAdd={canOperate} canDelete={isAdmin} />; }
  else if (p0 === "jobs") { active = "jobs"; page = p1 ? <JobDetail id={p1} /> : <Jobs />; }
  else if (p0 === "users" && isAdmin) { active = "users"; page = <Users me={me} />; }
  else if (p0 === "audit" && isAdmin) { active = "audit"; page = <Audit />; }
  else if (p0 === "settings" && isAdmin) {
    active = SETTINGS_TABS.some((t) => t.id === p1) ? p1 : "notifications";
    page = <Settings tab={active as SettingsTab} />;
  } else page = <Dashboard ranges={ranges} canOperate={canOperate} />;
  return <View title={`Datacenter (${me.instance || "VALOR"})`} menu={menu} active={active}>{page}</View>;
}

/** Search in the header: ranges and VMs by name, VMID or description. */
function Search({ ranges }: { ranges: RangeRow[] }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [pick, setPick] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, [open]);
  const needle = q.trim().toLowerCase();
  const items = ranges.filter((r) => r.has_spec).flatMap((r) => [
    { type: "Range", name: r.name, about: r.description ?? "", href: `ranges/${r.name}` },
    ...(r.machines ?? []).map((m) => ({ type: "VM", name: `${m.vmid} (${m.host})`, about: `range ${r.name} · ${m.status}`,
      href: `ranges/${r.name}/vm/${m.host}` })),
  ]);
  const hits = needle ? items.filter((i) => `${i.name} ${i.about}`.toLowerCase().includes(needle)).slice(0, 12) : [];
  const choose = (href: string) => { go(href); setQ(""); setOpen(false); };
  return (
    <div className="search" ref={ref}>
      <Icon name="search" size={15} />
      <input name="search" type="search" placeholder="Search" value={q} aria-label="Search ranges and VMs" autoComplete="off"
        onChange={(e) => { setQ(e.target.value); setOpen(true); setPick(0); }} onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setPick(Math.min(pick + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setPick(Math.max(pick - 1, 0)); }
          else if (e.key === "Enter" && hits[pick]) choose(hits[pick].href);
          else if (e.key === "Escape") setOpen(false);
        }} />
      {open && needle && (
        <div className="popover search-hits" role="listbox">
          {!hits.length ? <div className="empty">No match.</div> : (
            <table>
              <thead><tr><th>Type</th><th>Name</th><th>Description</th></tr></thead>
              <tbody>
                {hits.map((h, i) => (
                  <tr key={h.href} className={`clickable${i === pick ? " selected" : ""}`} role="option" aria-selected={i === pick}
                    onMouseDown={(e) => { e.preventDefault(); choose(h.href); }}>
                    <td>{h.type}</td><td className="nowrap"><b>{h.name}</b></td><td className="fill"><span className="clip">{h.about}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

function UserMenu({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const toggleTheme = () => {
    const root = document.documentElement;
    const dark = root.dataset.theme === "dark" ||
      (!root.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    root.dataset.theme = dark ? "light" : "dark";
    try {
      localStorage.setItem("valor-theme", root.dataset.theme);
    } catch {
      /* not persisted */
    }
  };
  return (
    <div className="user-menu">
      <DropButton icon="account" label={me.user.display_name || me.user.username} items={[
        { label: "My account", icon: "account", onClick: () => go("account") },
        { label: "Light / dark theme", icon: "theme", onClick: toggleTheme },
        { label: "Sign out", icon: "logout", onClick: onSignOut },
      ]} />
    </div>
  );
}
