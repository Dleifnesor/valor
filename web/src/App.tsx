import { ReactNode, Suspense, lazy, useCallback, useEffect, useState } from "react";
import { ApiError, get, post, setCsrf, setUnauthenticatedHandler } from "./api";
import { Me } from "./types";
import { go, useRoute } from "./hooks";
import { Icon, Loading, Logo } from "./components/ui";
import { Notifications } from "./components/Notifications";
import { SignIn } from "./pages/SignIn";
import { Dashboard } from "./pages/Dashboard";
import { Ranges } from "./pages/Ranges";
import { RangeDetail } from "./pages/RangeDetail";
import { RangeEditor } from "./pages/RangeEditor";
import { Jobs, JobDetail } from "./pages/Jobs";
import { Users } from "./pages/Users";
import { Audit } from "./pages/Audit";
import { Settings } from "./pages/Settings";
import { Account } from "./pages/Account";
// The console page carries noVNC and xterm.js: loaded only when a console is opened.
const ConsolePage = lazy(() => import("./pages/Console").then((m) => ({ default: m.ConsolePage })));

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
  return <Shell me={me} setMe={setMe} />;
}

const NAV: { path: string; label: string; icon: Parameters<typeof Icon>[0]["name"]; role?: "admin" }[] = [
  { path: "dashboard", label: "Dashboard", icon: "dashboard" },
  { path: "ranges", label: "Ranges", icon: "ranges" },
  { path: "jobs", label: "Jobs", icon: "jobs" },
  { path: "users", label: "Users", icon: "users", role: "admin" },
  { path: "audit", label: "Audit log", icon: "audit", role: "admin" },
  { path: "settings", label: "Settings", icon: "settings", role: "admin" },
];

function Shell({ me, setMe }: { me: Me; setMe: (m: Me | null) => void }) {
  const route = useRoute();
  const [navOpen, setNavOpen] = useState(false);
  const isAdmin = me.user.role === "admin";
  const can = (role: "operator" | "admin") =>
    role === "admin" ? isAdmin : me.user.role === "admin" || me.user.role === "operator";

  useEffect(() => setNavOpen(false), [route.join("/")]);

  const signOut = async () => {
    try {
      await post("/api/auth/logout");
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    }
    setMe(null);
    go("dashboard");
  };

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

  let page: ReactNode;
  let title = "";
  const [p0, p1, p2, p3, p4] = route;
  if (p0 === "ranges" && p1 && p2 === "console" && p3) {
    title = `${p3} console`;
    page = (
      <Suspense fallback={<Loading label="Loading the console…" />}>
        <ConsolePage range={p1} host={p3} kind={p4 === "serial" ? "serial" : "vnc"} />
      </Suspense>
    );
  } else if (p0 === "ranges" && p1 === "new") {
    title = "New range";
    page = <RangeEditor canEdit={can("operator")} />;
  } else if (p0 === "ranges" && p1 && p2 === "edit") {
    title = `Edit ${p1}`;
    page = <RangeEditor name={p1} canEdit={can("operator")} />;
  } else if (p0 === "ranges" && p1) {
    title = `Range ${p1}`;
    page = <RangeDetail name={p1} canOperate={can("operator")} />;
  } else if (p0 === "ranges") {
    title = "Ranges";
    page = <Ranges canOperate={can("operator")} />;
  } else if (p0 === "jobs" && p1) {
    title = `Job ${p1}`;
    page = <JobDetail id={p1} />;
  } else if (p0 === "jobs") {
    title = "Jobs";
    page = <Jobs />;
  } else if (p0 === "users" && isAdmin) {
    title = "Users";
    page = <Users me={me} />;
  } else if (p0 === "audit" && isAdmin) {
    title = "Audit log";
    page = <Audit />;
  } else if (p0 === "settings" && isAdmin) {
    title = "Settings";
    page = <Settings />;
  } else if (p0 === "account") {
    title = "Your account";
    page = <Account me={me} onChanged={setMe} />;
  } else {
    title = "Dashboard";
    page = <Dashboard />;
  }

  return (
    <div className="shell">
      <nav className={`nav${navOpen ? " open" : ""}`} aria-label="Main">
        <div className="brand">
          <Logo />
          <div>
            <b>VALOR</b>
            <small>{me.instance.replace(/^VALOR\s*/, "") || "ranges on Proxmox VE"}</small>
          </div>
        </div>
        {NAV.filter((n) => !n.role || isAdmin).map((n, i) => (
          <div key={n.path}>
            {i === 3 && <div className="section">Administration</div>}
            <a href={`#/${n.path}`} className={p0 === n.path || (!p0 && n.path === "dashboard") ? "active" : ""}>
              <Icon name={n.icon} /> {n.label}
            </a>
          </div>
        ))}
        <div className="spacer" />
        <a href="#/account" className={p0 === "account" ? "active" : ""}>
          <Icon name="account" /> {me.user.display_name || me.user.username}
        </a>
        <div className="foot">
          VALOR {me.version} · {me.user.role}
        </div>
      </nav>
      <div className="main">
        <header className="topbar">
          <button className="btn ghost menu-btn" onClick={() => setNavOpen(!navOpen)} aria-label="Menu">
            <Icon name="menu" />
          </button>
          <div className="title">
            <h1>{title}</h1>
          </div>
          <button className="btn ghost" onClick={toggleTheme} title="Light / dark" aria-label="Toggle theme">
            <Icon name="theme" />
          </button>
          <Notifications />
          <button className="btn ghost" onClick={signOut} title="Sign out" aria-label="Sign out">
            <Icon name="logout" />
          </button>
        </header>
        <main className="content">{page}</main>
      </div>
    </div>
  );
}
