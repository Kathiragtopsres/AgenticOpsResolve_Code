import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import {
  Home,
  ClipboardList,
  CalendarDays,
  Sparkles,
  UserRound,
  ArrowLeft,
  WifiOff,
  Hexagon,
  Bell,
  Loader2,
} from "lucide-react";
import { getAuthUser, clearAuthUser, type AuthUser } from "@/lib/auth";
import { getMe, type MeResponse } from "@/service/service";

type FieldState = {
  hydrated: boolean;
  session: boolean;
  user: AuthUser | null;
  me: MeResponse | null;
  meLoading: boolean;
  login: (user: AuthUser) => void;
  logout: () => void;
  offline: boolean;
  setOffline: (v: boolean) => void;
  pending: number;
  queue: () => void;
  toast: string;
  notify: (message: string) => void;
};
const Context = createContext<FieldState | null>(null);
function stored<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const value = localStorage.getItem(key);
    return value ? (JSON.parse(value) as T) : fallback;
  } catch {
    return fallback;
  }
}
export function FieldProvider({ children }: { children: ReactNode }) {
  const [hydrated, setHydrated] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const session = user !== null;
  const [me, setMe] = useState<MeResponse | null>(null);
  const [meLoading, setMeLoading] = useState(false);
  const [simOffline, setSimOffline] = useState(false);
  const [browserOnline, setBrowserOnline] = useState(true);
  const [pending, setPending] = useState(0);
  const [toast, setToast] = useState("");
  const offline = simOffline || !browserOnline;
  useEffect(() => {
    const pd = stored<unknown>("opsresolve.field.pending", 0);
    setPending(typeof pd === "number" ? pd : 0);
    setUser(getAuthUser());
    setHydrated(true);
  }, []);
  useEffect(() => {
    const update = () => setBrowserOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  useEffect(() => {
    if (hydrated) localStorage.setItem("opsresolve.field.pending", JSON.stringify(pending));
  }, [pending, hydrated]);
  useEffect(() => {
    if (!offline && pending) {
      setPending(0);
      setToast("All changes synced");
    }
  }, [offline, pending]);
  useEffect(() => {
    let active = true;
    if (user?.technicianId) {
      setMeLoading(true);
      setMe(null);
      getMe(user.technicianId)
        .then((data) => {
          if (active) setMe(data);
        })
        .catch(() => {
          if (active) setMe(null);
        })
        .finally(() => {
          if (active) setMeLoading(false);
        });
    } else {
      setMe(null);
      setMeLoading(false);
    }
    return () => {
      active = false;
    };
  }, [user?.technicianId]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 3200);
    return () => clearTimeout(t);
  }, [toast]);
  const queue = () => {
    if (offline) setPending((n) => n + 1);
  };
  return (
    <Context.Provider
      value={{
        hydrated,
        session,
        user,
        me,
        meLoading,
        login: (u: AuthUser) => setUser(u),
        logout: () => {
          void clearAuthUser();
          setUser(null);
          setMe(null);
          setMeLoading(false);
        },
        offline,
        setOffline: setSimOffline,
        pending,
        queue,
        toast,
        notify: setToast,
      }}
    >
      {hydrated ? children : null}
    </Context.Provider>
  );
}
export function useField() {
  const value = useContext(Context);
  if (!value) throw new Error("Missing FieldProvider");
  return value;
}
export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "outline" | "ghost" | "icon";
}) {
  return <button {...props} className={`btn btn-${variant} ${className}`} />;
}
export function Brand() {
  return (
    <span className="brand">
      <span className="brand-mark">
        <Hexagon size={18} strokeWidth={3} />
      </span>
      <span>
        OpsResolve <em>Field</em>
      </span>
    </span>
  );
}
export function Loader({ size = 18, label }: { size?: number; label?: string }) {
  return (
    <span className="loader" role="status" aria-live="polite">
      <Loader2 size={size} className="loader-spin" />
      {label ? <span>{label}</span> : null}
    </span>
  );
}
const tabs = [
  { to: "/home", label: "Home", icon: Home },
  { to: "/jobs", label: "Jobs", icon: ClipboardList },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/assistant", label: "Assistant", icon: Sparkles },
  { to: "/profile", label: "Profile", icon: UserRound },
] as const;
export function Shell({
  children,
  title,
  back,
  tab = true,
  action,
}: {
  children: ReactNode;
  title?: string;
  back?: string;
  tab?: boolean;
  action?: ReactNode;
}) {
  const { hydrated, session, offline, pending, toast } = useField();
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    if (hydrated && !session && location.pathname !== "/login")
      navigate({ to: "/login", replace: true });
  }, [session, location.pathname, navigate]);
  if (!session) return null;
  return (
    <div className="app-frame"> 
      <header className="app-header">
        <div className="header-left">
          {back ? (
            <Button
              variant="icon"
              aria-label="Back"
              onClick={() => (history.length > 1 ? history.back() : navigate({ to: back }))}
            >
              <ArrowLeft size={21} />
            </Button>
          ) : null}
          <Brand />
        </div>
        <div className="header-right">{action || <Bell size={20} />}</div>
      </header>
      {offline && (
        <div className="offline-banner">
          <WifiOff size={16} /> You're offline — changes will sync when you're back online.
          {pending > 0 && <b> {pending} pending</b>}
        </div>
      )}
      <main className={`screen ${tab ? "with-tabs" : ""}`}>
        {title && (
          <div className="page-heading">
            <h1>{title}</h1>
          </div>
        )}
        {children}
      </main>
      {tab && (
        <nav className="bottom-tabs" aria-label="Main navigation">
          {tabs.map(({ to, label, icon: Icon }) => (
            <Link key={to} to={to} className={`tab ${location.pathname === to ? "active" : ""}`}>
              <Icon size={22} strokeWidth={location.pathname === to ? 2.4 : 1.9} />
              <span>{label}</span>
            </Link>
          ))}
        </nav>
      )}
      {toast && (
        <div role="status" className="toast">
          {toast}
        </div>
      )}
    </div>
  );
}
