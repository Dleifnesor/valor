// *builds the whole house* OwO web/src/App.tsx is the ROOT of the web UI! ^w^
// It checks who's signed in and decides what to show: the sign-in steps, a pop-out console window, or the main shell
// laid out like Proxmox VE (header, resource tree, the selected object's panel, task list). Which page shows comes
// from the URL's hash (#/ranges/web, #/jobs/<id>, ...) *wags tail*
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
// API helpers, shared types, hooks, UI pieces, the tree, task log, menu host, and every page UwU
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
import { ConsoleWindow, VmView } from "./pages/Vm";
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

// The app's root: who is signed in decides what shows - the sign-in steps, a console window of its own (opened
// from the tree), or the main shell laid out like Proxmox VE. Pages are picked from the URL's hash (#/ranges/web,
// #/jobs/<id>, ...), see useRoute in hooks.ts.

// App: the root component >w<
export default function App() {
  // Who's signed in (null = nobody), whether we've asked yet, and the route's first parts OwO
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);
  const [p0, p1, p2, p3] = useRoute();

  // refreshMe(): ask the server who we are; store the CSRF token for later requests ^w^
  const refreshMe = useCallback(() => {
    get<Me>("/api/auth/me")
      .then((session) => {
        setCsrf(session.csrf);
        setMe(session);
      })
      .catch(() => setMe(null))
      .finally(() => setChecked(true));
  }, []);

  // On start: an expired session anywhere sends us back to sign-in; then ask who we are UwU
  useEffect(() => {
    setUnauthenticatedHandler(() => setMe(null));
    refreshMe();
  }, [refreshMe]);

  // Still asking: spinner. Not fully signed in (or a password change is due): the sign-in steps >w<
  if (!checked) return <Loading />;
  if (!me || me.stage !== "full" || me.user.must_change_password) {
    return (
      <SignIn
        me={me}
        onSignedIn={(session) => {
          setCsrf(session.csrf);
          setMe(session);
        }}
      />
    );
  }
  if (p0 === "console" && p1 && p2) {
    // console/<range>/<host>[/serial]: a console window (double-click on a VM in the tree), without the shell
    // Only operators and admins may open consoles OwO
    const canOperate = me.user.role === "admin" || me.user.role === "operator";
    return <PageGuard><ConsoleWindow range={p1} host={p2} kind={p3 === "serial" ? "serial" : "vnc"} canOperate={canOperate} /></PageGuard>;
  }
  // Everyone else: the main shell, wrapped in a guard so a crashing page can't blank the whole app ^w^
  return <PageGuard><Shell me={me} setMe={setMe} /></PageGuard>;
}

// NARROW: phone-sized screens (the tree slides in instead of staying open) UwU
const NARROW = "(max-width: 860px)";

// The layout of Proxmox VE's web UI: a header, the resource tree on the left, the selected object's panel on the
// right and the task list along the bottom.
// Shell: the main layout >w<
function Shell({ me, setMe }: { me: Me; setMe: (session: Me | null) => void }) {
  // The route, the range list (every 10 s), tree/task-list visibility (some remembered), the open task, and roles OwO
  const route = useRoute();
  const ranges = useApi<{ ranges: RangeRow[]; cluster_error: string | null }>("/api/ranges", 10000);
  const [treeOpen, setTreeOpen] = useState(false);                                // phones: the slide-in tree
  const [treeHidden, setTreeHidden] = useStored("valor-nav-collapsed", false);     // wide screens
  const [logOpen, setLogOpen] = useStored("valor-tasklog", true);
  const [logTab, setLogTab] = useState<"tasks" | "notes">("tasks");
  const [task, setTask] = useState<string | null>(null);
  const isAdmin = me.user.role === "admin";
  const canOperate = isAdmin || me.user.role === "operator";

  // Every navigation closes the slide-in tree and the task viewer ^w^
  useEffect(() => { setTreeOpen(false); setTask(null); }, [route.join("/")]);
  // Reload the range list when a page says ranges changed (e.g. after a delete) UwU
  useEffect(() => {
    window.addEventListener("valor-ranges", ranges.reload);
    return () => window.removeEventListener("valor-ranges", ranges.reload);
  }, [ranges.reload]);
  // Open the task viewer when something started a job >w<
  useEffect(() => {
    const onTask = (e: Event) => setTask((e as CustomEvent<string>).detail);
    window.addEventListener("valor-task", onTask);
    return () => window.removeEventListener("valor-task", onTask);
  }, []);
  // Escape closes the slide-in tree OwO
  useEffect(() => {
    if (!treeOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setTreeOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [treeOpen]);
  // toggleTree(): slide in/out on phones, show/hide on wide screens ^w^
  const toggleTree = () => (window.matchMedia(NARROW).matches ? setTreeOpen(!treeOpen) : setTreeHidden(!treeHidden));

  // signOut(): end the session (ignoring API errors) and go back to sign-in UwU
  const signOut = async () => {
    try {
      await post("/api/auth/logout");
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    }
    setMe(null);
    go("dashboard");
  };

  // Pick the page from the route; `selected` = what the tree highlights >w<
  const [p0, p1, p2, p3, p4, p5] = route;
  let selected: Selection | null = "root";
  let view: ReactNode;
  if (p0 === "ranges" && p1 && p1 !== "new" && (p2 === "vm" || p2 === "console") && p3) {
    // ranges/<range>/vm/<host>[/console[/serial]]; ranges/<range>/console/<host>/<vnc|serial> is the older form
    // A VM page (with an optional console; an older URL form still works) OwO
    selected = `vm:${p1}/${p3}`;
    const consoleKind = p2 === "console" ? (p4 === "serial" ? "serial" : "vnc")
      : p4 === "console" ? (p5 === "serial" ? "serial" : "vnc") : null;
    view = <VmView key={`${p1}/${p3}`} range={p1} host={p3} console={consoleKind} canOperate={canOperate} />;
  } else if (p0 === "ranges" && p1 && p1 !== "new") {
    // A range page (a section, or a chat by id) ^w^
    selected = `range:${p1}`;
    view = <RangeView key={p1} name={p1} section={p2} chatId={p2 === "chat" && p3 ? Number(p3) : undefined} canOperate={canOperate} />;
  } else if (p0 === "ranges" && p1 === "new") {
    // The range editor for a new range UwU
    selected = null;
    view = <View title="Create range"><RangeEditor canEdit={canOperate} /></View>;
  } else if (p0 === "builder") {
    // The chat builder (operators can start new chats; anyone can open a saved one) >w<
    selected = null;
    view = (
      <View title="Chat builder">
        {canOperate || p1 ? <Builder key={p1 ?? "new"} isAdmin={isAdmin} chatId={p1 ? Number(p1) : null} />
          : <div className="alert warn">The chat builder needs the operator role.</div>}
      </View>
    );
  } else if (p0 === "account") {
    // My account OwO
    selected = null;
    view = <View title={`User '${me.user.username}'`}><Account me={me} onChanged={setMe} /></View>;
  } else {
    // Everything else is under Datacenter ^w^
    view = <Datacenter me={me} route={route} ranges={ranges.data?.ranges ?? null} />;
  }

  // The shell UwU
  return (
    <div className={`shell${logOpen ? "" : " log-closed"}${treeHidden ? " tree-hidden" : ""}`}>
      {/* Header: tree toggle, logo and version, search, create buttons (operators), user menu */}
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
      {/* The workspace: the tree (and its backdrop on phones) and the content */}
      <div className="workspace">
        {treeOpen && <div className="nav-backdrop" onClick={() => setTreeOpen(false)} aria-hidden="true" />}
        <aside id="resource-tree" className={`tree-pane${treeOpen ? " open" : ""}`}>
          {/* Each part has its own guard, so a crash in one doesn't take down the other */}
          <PageGuard page={route.join("/")}>
            <ResourceTree ranges={ranges.data?.ranges ?? null} instance={me.instance || "VALOR"} selected={selected} canOperate={canOperate} />
          </PageGuard>
          {/* Proxmox couldn't be asked: the tree shows a warning */}
          {ranges.data?.cluster_error && <div className="alert warn small tree-alert">Live VM status unavailable: {ranges.data.cluster_error}</div>}
        </aside>
        <main className="content"><PageGuard page={route.join("/")}>{view}</PageGuard></main>
      </div>
      {/* The task list, the task viewer dialog, and the right-click menu host */}
      <TaskLog open={logOpen} onOpen={setLogOpen} tab={logTab} onTab={setLogTab} />
      {task && <Modal title="Task viewer" wide onClose={() => setTask(null)}><JobDetail id={task} /></Modal>}
      <ContextMenuHost page={route.join("/")} />
    </div>
  );
}

// Datacenter: VALOR's own panel, with its section menu (admins get more sections) >w<
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
      ...SETTINGS_TABS.map((tab) => ({ ...tab, href: `settings/${tab.id}` })),
    );
  }
  // Pick the section from the route OwO
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
    active = SETTINGS_TABS.some((tab) => tab.id === p1) ? p1 : "notifications";
    page = <Settings tab={active as SettingsTab} />;
  } else page = <Dashboard ranges={ranges} canOperate={canOperate} />;
  // The panel with the menu and the chosen section ^w^
  return <View title={`Datacenter (${me.instance || "VALOR"})`} menu={menu} active={active}>{page}</View>;
}

/** Search in the header: ranges and VMs by name, VMID or description. */
// Search: the header search: ranges and VMs by name, id or description; arrow keys, Enter and Escape work UwU
function Search({ ranges }: { ranges: RangeRow[] }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [pick, setPick] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  // Close the results on a click elsewhere >w<
  useEffect(() => {
    if (!open) return;
    const onOutsideClick = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onOutsideClick);
    return () => document.removeEventListener("mousedown", onOutsideClick);
  }, [open]);
  // Everything searchable (ranges with a spec, and their VMs), and the first 12 matches OwO
  const needle = query.trim().toLowerCase();
  const items = ranges.filter((range) => range.has_spec).flatMap((range) => [
    { type: "Range", name: range.name, about: range.description ?? "", href: `ranges/${range.name}` },
    ...(range.machines ?? []).map((machine) => ({ type: "VM", name: `${machine.vmid} (${machine.host})`, about: `range ${range.name} · ${machine.status}`,
      href: `ranges/${range.name}/vm/${machine.host}` })),
  ]);
  const hits = needle ? items.filter((item) => `${item.name} ${item.about}`.toLowerCase().includes(needle)).slice(0, 12) : [];
  // choose(): go to a result and reset the box ^w^
  const choose = (href: string) => { go(href); setQuery(""); setOpen(false); };
  return (
    <div className="search" ref={ref}>
      {/* The search icon and box */}
      <Icon name="search" size={15} />
      <input name="search" type="search" placeholder="Search" value={query} aria-label="Search ranges and VMs" autoComplete="off"
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setPick(0); }} onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setPick(Math.min(pick + 1, hits.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setPick(Math.max(pick - 1, 0)); }
          else if (e.key === "Enter" && hits[pick]) choose(hits[pick].href);
          else if (e.key === "Escape") setOpen(false);
        }} />
      {/* The results (mousedown so the click lands before the input loses focus) */}
      {open && needle && (
        <div className="popover search-hits" role="listbox">
          {!hits.length ? <div className="empty">No match.</div> : (
            <table>
              <thead><tr><th>Type</th><th>Name</th><th>Description</th></tr></thead>
              <tbody>
                {/* One row per match: type, name, description */}
                {hits.map((hit, i) => (
                  <tr key={hit.href} className={`clickable${i === pick ? " selected" : ""}`} role="option" aria-selected={i === pick}
                    onMouseDown={(e) => { e.preventDefault(); choose(hit.href); }}>
                    <td>{hit.type}</td><td className="nowrap"><b>{hit.name}</b></td><td className="fill"><span className="clip">{hit.about}</span></td>
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

// UserMenu: my account, light/dark theme, sign out UwU
function UserMenu({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  // toggleTheme(): flip the theme (starting from the system's when none was picked) and remember it >w<
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
      {/* The menu button with my name OwO */}
      <DropButton icon="account" label={me.user.display_name || me.user.username} items={[
        { label: "My account", icon: "account", onClick: () => go("account") },
        { label: "Light / dark theme", icon: "theme", onClick: toggleTheme },
        { label: "Sign out", icon: "logout", onClick: onSignOut },
      ]} />
    </div>
  );
}
