import { Link, useRouterState } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  Bell,
  ChartNoAxesCombined,
  CheckCheck,
  CircleHelp,
  ClipboardList,
  Command,
  LoaderCircle,
  LogOut,
  Menu,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  isAuthenticated,
  logout,
  getCurrentUser,
  getUserInitials,
  type CurrentUser,
} from "@/services/auth";
import { getCaseNames, getApprovals, type CaseName } from "@/services/service";
import { usePendingCount, setPendingCount } from "@/lib/approvals-store";

const links = [
  { label: "Case Board", to: "/cases", icon: ClipboardList },
  { label: "Timeline", to: "/timeline", icon: Activity },
  { label: "Trace Viewer", to: "/trace", icon: Command },
  { label: "Approvals", to: "/approvals", icon: ShieldCheck },
  { label: "KPIs", to: "/kpis", icon: ChartNoAxesCombined },
  { label: "Scenario Controller", to: "/scenario-controller", icon: SlidersHorizontal },
] as const;

// The shell is mounted once at the root and stays mounted across navigation.
// These module-level caches let the shell render instantly after the first
// load and seed the UI while a background refresh runs, so data stays fresh on
// every tab switch without ever flashing the "Loading console..." placeholder.
let shellHydrated = false;
let cachedCaseNames: CaseName[] | null = null;
export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div className="brand-mark">
        <span aria-hidden="true" />
      </div>
      <div>
        <div className="brand-name">OpsResolve</div>
        <div
          className={`brand-subtitle ${compact ? "text-primary" : "text-sidebar-foreground/65"}`}
        >
          Operations Console
        </div>
      </div>
    </div>
  );
}
export function OpsShell({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const pending = usePendingCount();
  // Start ready if the shell has already been set up once this session, so
  // remounts on navigation render immediately instead of flashing a loader.
  const [ready, setReady] = useState(shellHydrated);
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState("");
  const [caseNames, setCaseNames] = useState<CaseName[]>(cachedCaseNames ?? []);
  const [namesLoading, setNamesLoading] = useState(cachedCaseNames === null);
  const [user, setUser] = useState<CurrentUser | null>(() => getCurrentUser());
  // One-time setup: auth guard. Runs once on mount.
  useEffect(() => {
    if (!isAuthenticated()) {
      location.replace("/login");
      return;
    }
    setUser(getCurrentUser());
    if (!shellHydrated) {
      shellHydrated = true;
      setReady(true);
    }
  }, []);
  // Silent background refresh on every navigation. The UI keeps showing the
  // cached data while these run, so there is no flash — the lists just update
  // in place once the fresh data arrives. This keeps the top-bar search and the
  // Approvals badge current on every tab switch.
  useEffect(() => {
    if (!isAuthenticated()) return;
    // Only show the "Searching..." state on the very first load, when we have
    // no cached names yet; subsequent refreshes update silently.
    if (cachedCaseNames === null) setNamesLoading(true);
    getCaseNames()
      .then((names) => {
        cachedCaseNames = names;
        setCaseNames(names);
      })
      .catch(() => {
        if (cachedCaseNames === null) setCaseNames([]);
      })
      .finally(() => setNamesLoading(false));
    getApprovals("PENDING")
      .then((list) => setPendingCount(list.length))
      .catch(() => {
        // Keep the last known count on a transient failure.
      });
  }, [pathname]);
  // Top-bar search: match id/customer/issue/asset, cap at 5 suggestions.
  const matches = search.trim()
    ? caseNames
        .filter((c) =>
          `${c.id} ${c.customer} ${c.issue} ${c.asset}`
            .toLowerCase()
            .includes(search.toLowerCase()),
        )
        .slice(0, 5)
    : [];
  return (
    <div className="app-frame">
      <aside className={`sidebar ${menu ? "sidebar-open" : ""}`}>
        <div className="px-6 pt-7 pb-10">
          <Brand />
        </div>
        <div className="sidebar-caption">WORKSPACE</div>
        <nav className="space-y-1 px-3">
          {links.map(({ label, to, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              onClick={() => setMenu(false)}
              className={`nav-link ${pathname === to || (to === "/cases" && pathname.startsWith("/cases")) ? "nav-active" : ""}`}
            >
              <Icon size={18} />
              <span>{label}</span>
              {label === "Approvals" && !!pending && (
                <span className="ml-auto nav-count">{pending}</span>
              )}
            </Link>
          ))}
        </nav>
        <div className="mt-auto px-4 pb-5">
          <div className="sidebar-divider" />
          <div className="sidebar-user">
            <span className="avatar">{getUserInitials(user?.name ?? "")}</span>
            <div className="min-w-0">
              <div className="text-xs font-semibold truncate">{user?.name ?? "—"}</div>
              <div className="text-[11px] text-sidebar-foreground/60 truncate">Service Manager</div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              title="Sign out"
              className="ml-auto text-sidebar-foreground"
              onClick={() => {
                logout();
                location.href = "/login";
              }}
            >
              <LogOut size={16} />
            </Button>
          </div>
        </div>
      </aside>
      {menu && <div className="mobile-scrim" onClick={() => setMenu(false)} />}
      <div className="app-main">
        <header className="topbar">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            title="Open menu"
            onClick={() => setMenu(true)}
          >
            <Menu />
          </Button>
          <div className="topbar-search">
            <Search size={17} className="text-muted-foreground" />
            <input
              aria-label="Search cases, customers, assets"
              placeholder="Search cases, customers, assets..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <span className="search-shortcut">⌘ K</span>
            {search && (
              <Button
                variant="ghost"
                size="icon"
                title="Clear search"
                onClick={() => setSearch("")}
              >
                <X size={14} />
              </Button>
            )}
            {search && (
              <div className="search-results">
                {namesLoading ? (
                  <div className="p-4 text-sm text-muted-foreground flex items-center gap-2">
                    <LoaderCircle className="animate-spin text-primary" size={16} /> Searching...
                  </div>
                ) : matches.length ? (
                  matches.map((c) => (
                    <Link
                      key={c.id}
                      to="/cases/$caseId"
                      params={{ caseId: c.id }}
                      onClick={() => setSearch("")}
                      className="search-result"
                    >
                      <span className="font-semibold text-primary">{c.id}</span>
                      <span>
                        {c.customer} · {c.issue}
                      </span>
                    </Link>
                  ))
                ) : (
                  <div className="p-4 text-sm text-muted-foreground">No matching cases</div>
                )}
              </div>
            )}
          </div>
          <div className="topbar-right">
            <Button
              variant="ghost"
              size="icon"
              title="Notifications"
              onClick={() => (location.href = "/approvals")}
              className="relative"
            >
              <Bell size={18} />
              {(pending ?? 0) > 0 && <span className="notification-dot" />}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              title="Help"
              onClick={() => (location.href = "/scenario-controller")}
            >
              <CircleHelp size={18} />
            </Button>
          </div>
        </header>
        <main className="content">
          {ready ? children : <div className="p-8 text-muted-foreground">Loading console...</div>}
        </main>
      </div>
    </div>
  );
}
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
export function Status({ state }: { state: string }) {
  return (
    <span className={`status status-${state.toLowerCase().replaceAll(" ", "-")}`}>{state}</span>
  );
}
export function Empty({ title, description }: { title: string; description?: string }) {
  return (
    <div className="empty">
      <CheckCheck size={28} />
      <h3>{title}</h3>
      {description && <p>{description}</p>}
    </div>
  );
}
export function Loader() {
  return (
    <div className="flex items-center justify-center w-full min-h-[60vh]">
      <LoaderCircle className="animate-spin text-primary" size={32} />
    </div>
  );
}
// Generic "download as JSON" button — serialises `data` to a Blob and triggers a client-side download.
export function DownloadButton({
  data,
  name,
  children,
}: {
  data: unknown;
  name: string;
  children: ReactNode;
}) {
  return (
    <Button
      variant="outline"
      onClick={() => {
        const link = document.createElement("a");
        link.href = URL.createObjectURL(
          new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
        );
        link.download = name;
        link.click();
        URL.revokeObjectURL(link.href);
      }}
    >
      {children}
    </Button>
  );
}
