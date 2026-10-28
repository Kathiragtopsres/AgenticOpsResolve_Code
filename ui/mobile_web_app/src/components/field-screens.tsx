import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent } from "react";
import { Link, useNavigate, useLocation } from "@tanstack/react-router";
import {
  ArrowRight,
  ClipboardList,
  MapPin,
  Clock3,
  ChevronRight,
  ChevronDown,
  CalendarDays,
  Star,
  ShieldCheck,
  AlertTriangle,
  Sparkles,
  Navigation,
  Phone,
  MessageSquare,
  CarFront,
  Building2,
  Search,
  ScanLine,
  Package,
  ShoppingCart,
  Check,
  Camera,
  Mic,
  Send,
  Paperclip,
  Trash2,
  PenLine,
  Wifi,
  LogOut,
  Pencil,
  X,
  Fingerprint,
  Delete,
  CloudOff,
  BadgeCheck,
  Wrench,
  Plus,
  Minus,
  CircleCheck,
  CalendarPlus,
  CalendarClock,
} from "lucide-react";
import { Button, Brand, Shell, Loader, useField } from "./field-ui";
import { BarcodeScanner } from "./barcode-scanner";
import { signIn as signInCognito } from "@/lib/auth";
import {
  getJobs,
  getJobDetail,
  postJobStatus,
  getDepotStock,
  postPartUsed,
  postPartRequest,
  getPartByBarcode,
  uploadFile,
  postJobComplete,
  postAssistantMessage,
  getAvailability,
  addAvailability,
  deleteAvailability,
  AvailabilityError,
  getSlotCounts,
  type SlotCountDay,
  type JobListItem,
  type JobsView,
  type JobsResponse,
  type JobDetailResponse,
  type DepotStockResponse,
  type AvailabilitySlot,
} from "@/service/service";
import { Calendar } from "@/components/ui/calendar";
import { type Job } from "@/lib/field-data";
import chiller from "@/assets/chiller.jpg";

const detail = (id: string) => `/jobs/${id}`;
const dateString = new Intl.DateTimeFormat("en-US", {
  weekday: "long",
  month: "long",
  day: "numeric",
}).format(new Date());
const greetingForUtcHour = (hour: number): string => {
  if (hour >= 5 && hour < 12) return "Good morning";
  if (hour >= 12 && hour < 17) return "Good afternoon";
  if (hour >= 17 && hour < 21) return "Good evening";
  return "Good night";
};
const show = (v: unknown): string => (v === undefined ? "NA" : v === null ? " " : String(v));


const utcTime = (t?: string | null): string => {
  if (t === undefined) return "NA";
  if (t === null || t === "") return "";
  const tIndex = t.indexOf("T");
  if (tIndex >= 0 && t.length >= tIndex + 6) return t.slice(tIndex + 1, tIndex + 6);
  return t.slice(0, 5);
};

// Format an API timestamp as "MM DD YYYY" by slicing the date portion WITHOUT
// any Date parsing or timezone conversion (e.g. "2026-10-13T..." -> "10 13 2026").

const utcDate = (t?: string | null): string => {
  if (t === undefined) return "NA";
  if (t === null || t === "") return "";
  // Expect the date part to start as "YYYY-MM-DD".
  const datePart = t.slice(0, 10);
  const [y, m, d] = datePart.split("-");
  if (y && m && d) return `${m} ${d} ${y}`;
  return String(t);
};

// Time range label: "HH:MM – HH:MM" .
const timeLabel = (start?: string | null, end?: string | null): string => {
  if (start === undefined && end === undefined) return "NA";
  return `${utcTime(start)} – ${utcTime(end)}`;
};

// Completed-date label: "MM DD YYYY".
const dateLabel = (t?: string | null): string => utcDate(t);

// Map the API priority (P1/P2/P3) to the UI pill labels so existing styles apply.
const mapPriority = (p?: string | null): Job["priority"] => {
  if (p === "P1") return "Critical";
  if (p === "P2") return "Medium";
  if (p === "P3") return "Low";
  
  return "Low";
};


const mapStatus = (s?: string | null): Job["status"] => {
  if (s === "EN_ROUTE" || s === "ON_SITE" || s === "COMPLETED") return s;
  return "NOT_STARTED";
};


const toJob = (j: JobListItem): Job => ({
  id: show(j.jobId),
  customer: show(j.customerName),
  site: show(j.siteName),
  address: show(j.address),
  phone: "NA",
  asset: show(j.assetLabel),
  serial: "NA",
  time: timeLabel(j.slotStart, j.slotEnd),
  distance: j.distanceKm === undefined ? "NA" : j.distanceKm === null ? "" : `${j.distanceKm} km`,
  priority: mapPriority(j.priority),
  status: mapStatus(j.jobStatus),
  brief: "NA",
  telemetry: "NA",
  history: "NA",
  part: "NA",
  checklist: [],
  ...(typeof j.rating === "number" ? { rating: j.rating } : {}),
  date: dateLabel(j.completedAt),
});
// Build initials from a name when the API doesn't supply them.
const initialsFrom = (name: string | null | undefined): string =>
  typeof name === "string" && name.trim()
    ? name
        .trim()
        .split(/\s+/)
        .map((p) => p[0])
        .join("")
        .slice(0, 2)
        .toUpperCase()
    : "";
function Priority({ value }: { value: Job["priority"] }) {
  return <span className={`pill priority-${value.toLowerCase()}`}>{value}</span>;
}
function JobCard({ job }: { job: Job }) {
  return (
    <Link to={detail(job.id)} className="job-card">
      <div className="job-card-top">
        <span className="job-time">
          <Clock3 size={15} />
          {job.time}
        </span>
        <Priority value={job.priority} />
      </div>
      <div className="job-card-main">
        <img src={chiller} alt="Industrial equipment" width={816} height={816} loading="lazy" />
        <div className="min-w-0">
          <h3>{job.customer}</h3>
          <p>{job.asset}</p>
          <span className="job-location">
            <MapPin size={14} />
            {job.site} · {job.distance}
          </span>
        </div>
        <ChevronRight size={20} className="chevron" />
      </div>
      <div className="job-card-foot">
        <span>{job.id}</span>
        <span>
          {job.status === "COMPLETED"
            ? "Completed"
            : job.status === "ON_SITE"
              ? "On site"
              : job.status === "EN_ROUTE"
                ? "En route"
                : "View job"}{" "}
          <ArrowRight size={14} />
        </span>
      </div>
    </Link>
  );
}
// Login page — username/password sign-in via Cognito, redirects to Home on success.
export function LoginScreen() {
  const { session, login, hydrated } = useField();
  const navigate = useNavigate();
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [bio, setBio] = useState(false);
  const [mode, setMode] = useState<"password" | "pin">("password");
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);

  const signIn = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const authUser = await signInCognito(user.trim(), pass);
      login(authUser);
      navigate({ to: "/home" });
    } catch (err) {
      setError(
        err instanceof Error && err.message ? err.message : "Incorrect username or password.",
      );
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (hydrated && session) navigate({ to: "/home", replace: true });
  }, [hydrated, session, navigate]);
  const unlock = () => {
    setError("PIN sign-in is unavailable. Please use your username and password.");
    setPin("");
  };
  const digit = (n: string) => {
    setError("");
    setPin((p) => (p.length < 4 ? p + n : p));
  };
  return (
    <div className="app-frame login-screen">
      <div className="login-top">
        <Brand />
        <p>Technician App</p>
      </div>
      <div className="login-body">
        <div className="login-symbol">
          <ShieldCheck size={32} />
        </div>
        <p className="eyebrow">WELCOME BACK</p>
        <h1>Ready for the field?</h1>
        {mode === "password" ? (
          <>
            <p className="muted">Sign in with your technician account.</p>
            {/* Sign-in form — username, password (show/hide toggle) and submit. */}
            <form className="login-form" onSubmit={signIn}>
              <label>
                Username
                <input
                  autoComplete="username"
                  value={user}
                  onChange={(e) => {
                    setUser(e.target.value);
                    setError("");
                  }}
                  placeholder="e.g. john_doe"
                />
              </label>
              <label>
                Password
                <span className="pw-field">
                  <input
                    type={show ? "text" : "password"}
                    autoComplete="current-password"
                    value={pass}
                    onChange={(e) => {
                      setPass(e.target.value);
                      setError("");
                    }}
                    placeholder="Enter password"
                  />
                  <button type="button" onClick={() => setShow((v) => !v)}>
                    {show ? "Hide" : "Show"}
                  </button>
                </span>
              </label>
              <p className="pin-message" role="alert">
                {error || " "}
              </p>
              <Button type="submit" disabled={!user.trim() || !pass || busy}>
                {busy ? "Signing in…" : "Sign In"} <ArrowRight size={18} />
              </Button>
            </form>
          </>
        ) : (
          <>
            <p className="muted">Enter your 4-digit PIN to unlock your day.</p>
            <div
              className={`pin-dots ${error ? "pin-error" : ""}`}
              aria-label={`${pin.length} of 4 PIN digits entered`}
            >
              {[0, 1, 2, 3].map((i) => (
                <span key={i} className={pin.length > i ? "filled" : ""} />
              ))}
            </div>
            <p className="pin-message" role="alert">
              {error || " "}
            </p>
            <div className="keypad">
              {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((n) => (
                <Button variant="ghost" key={n} onClick={() => digit(n)}>
                  {n}
                </Button>
              ))}
              <Button variant="ghost" aria-label="Use fingerprint" onClick={() => setBio(true)}>
                <Fingerprint size={27} />
              </Button>
              <Button variant="ghost" onClick={() => digit("0")}>
                0
              </Button>
              <Button
                variant="ghost"
                aria-label="Delete digit"
                onClick={() => setPin((p) => p.slice(0, -1))}
              >
                <Delete size={25} />
              </Button>
            </div>
          </>
        )}
        <span className="offline-pill m-4">
          <CloudOff size={16} /> Offline mode available
        </span>
      </div>
      
      
    </div>
  );
}
// Home page — greeting + today's stats and job list.
export function HomeScreen() {
  const { user, me, meLoading } = useField();
  const technicianId = user?.technicianId;
  const [jobsResp, setJobsResp] = useState<JobsResponse | null>(null);
  const [jobsLoading, setJobsLoading] = useState(true);
  useEffect(() => {
    let active = true;
    if (technicianId) {
      setJobsLoading(true);
      setJobsResp(null);
      getJobs(technicianId, "today")
        .then((data) => {
          if (active) setJobsResp(data);
        })
        .catch(() => {
          if (active) setJobsResp(null);
        })
        .finally(() => {
          if (active) setJobsLoading(false);
        });
    } else {
      setJobsResp(null);
      setJobsLoading(false);
    }
    return () => {
      active = false;
    };
  }, [technicianId]);
  const loading = meLoading || jobsLoading;
  const meName = me ? me.name : undefined;
  const meInitials = me
    ? me.initials != null && me.initials !== ""
      ? me.initials
      : initialsFrom(me.name)
    : undefined;
  const apiJobs = jobsResp && Array.isArray(jobsResp.jobs) ? jobsResp.jobs : [];
  const today = apiJobs.map(toJob);
  const counts = jobsResp ? jobsResp.counts : undefined;
  const nextStart = jobsResp ? jobsResp.nextStart : undefined;
  const nextLabel = utcTime(nextStart);
  return (
    <Shell>
      {loading ? (
        <div className="page-loader">
          <Loader size={34} label="Loading…" />
        </div>
      ) : (
        <>
          {/* Greeting header — date, name and avatar link to profile. */}
          <div className="home-intro">
            <div>
              <p className="eyebrow">{dateString.toUpperCase()}</p>
              <h1>
                {greetingForUtcHour(new Date().getUTCHours())},
                <br />
                <strong>{show(meName)}</strong>
              </h1>
              <p>Your field schedule is ready.</p>
            </div>
            <Link to="/profile" className="avatar-link" aria-label="Open profile">
              {show(meInitials)}
            </Link>
          </div>
          <div className="stats-strip">
            <div>
              <span>Today</span>
              <strong>{show(counts ? counts.today : undefined)}</strong>
              <small>Jobs</small>
            </div>
            <div>
              <span>Next</span>
              <strong>{nextLabel}</strong>
              <small>Start time</small>
            </div>
            <div>
              <span>Completed</span>
              <strong>{show(counts ? counts.completed : undefined)}</strong>
              <small>Jobs</small>
            </div>
          </div>
          {/* Today's jobs section heading with "View all" link to the Jobs page. */}
          <div className="section-heading">
            <div>
              <p className="eyebrow">YOUR SCHEDULE</p>
              <h2>Today's jobs</h2>
            </div>
            <Link to="/jobs">
              View all <ArrowRight size={16} />
            </Link>
          </div>
          {/* Job cards for today; shows an empty-state message when there are none. */}
          <div className="job-list">
            {today.length ? (
              today.map((job) => <JobCard key={job.id} job={job} />)
            ) : (
              <p className="empty">No jobs scheduled for today — enjoy the break.</p>
            )}
          </div>
        </>
      )}
    </Shell>
  );
}
export const viewForFilter = (filter: string): JobsView =>
  filter === "Upcoming" ? "upcoming" : filter === "Completed" ? "completed" : "today";

// My Jobs page — searchable job list with Today/Upcoming/Completed filters.
export function JobsScreen() {
  const { user } = useField();
  const technicianId = user?.technicianId;
  const [filter, setFilter] = useState("Today");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<JobListItem[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  useEffect(() => {
    let active = true;
    if (technicianId) {
      setJobsLoading(true);
      setItems([]);
      getJobs(technicianId, viewForFilter(filter))
        .then((data) => {
          if (active) setItems(Array.isArray(data.jobs) ? data.jobs : []);
        })
        .catch(() => {
          if (active) setItems([]);
        })
        .finally(() => {
          if (active) setJobsLoading(false);
        });
    } else {
      setItems([]);
      setJobsLoading(false);
    }
    return () => {
      active = false;
    };
  }, [technicianId, filter]);
  const mapped = items.map(toJob);
  const list = mapped.filter((j) =>
    `${j.customer} ${j.id} ${j.asset}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <Shell title="My Jobs">
      <div className="jobs-screen">
        {/* Header — search box and Today/Upcoming/Completed filter toggle. */}
        <div className="jobs-head">
          <div className="subtitle">Your work, all in one place.</div>
          <div className="search-box">
            <Search size={18} />
            <input
              aria-label="Search jobs"
              placeholder="Search jobs or customers"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="segmented">
            {["Today", "Upcoming", "Completed"].map((f) => (
              <Button
                variant="ghost"
                key={f}
                className={filter === f ? "selected" : ""}
                onClick={() => setFilter(f)}
              >
                {f}
              </Button>
            ))}
          </div>
        </div>
        {jobsLoading ? (
          <div className="page-loader">
            <Loader size={34} label="Loading…" />
          </div>
        ) : (
          <div className="jobs-body">
            <div className="list-caption">
              {list.length} {list.length === 1 ? "job" : "jobs"}
            </div>
            <div className="job-list">
              {list.length ? (
                list.map((j) => <JobCard key={j.id} job={j} />)
              ) : (
                <p className="empty">No jobs found.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </Shell>
  );
}
// Job Detail page — full job info and the status flow (Start Navigation → I've Arrived → Complete).
export function DetailScreen({ id }: { id: string }) {
  const { user, notify } = useField();
  const technicianId = user?.technicianId;
  const navigate = useNavigate();
  const [detail, setDetail] = useState<JobDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [citation, setCitation] = useState(false);
  const [statusOverride, setStatusOverride] = useState<Job["status"] | null>(null);
  const [statusBusy, setStatusBusy] = useState(false);
  useEffect(() => {
    let active = true;
    if (technicianId) {
      setDetailLoading(true);
      setDetail(null);
      setStatusOverride(null);
      getJobDetail(technicianId, id)
        .then((data) => {
          if (active) setDetail(data);
        })
        .catch(() => {
          if (active) setDetail(null);
        })
        .finally(() => {
          if (active) setDetailLoading(false);
        });
    } else {
      setDetail(null);
      setDetailLoading(false);
    }
    return () => {
      active = false;
    };
  }, [technicianId, id]);
  const status = statusOverride ?? mapStatus(detail ? detail.jobStatus : undefined);
  const priority = mapPriority(detail ? detail.priority : undefined);
  const assetLabel = detail ? (detail.asset ? detail.asset.displayName : undefined) : undefined;
  const diagnosisBody =
    detail === null || detail.diagnosisHistory === undefined
      ? "NA"
      : detail.diagnosisHistory === null
        ? ""
        : detail.diagnosisHistory.map((d) => show(d?.summary)).join("\n");
  const requiredPartsBody =
    detail === null || detail.requiredParts === undefined
      ? "NA"
      : detail.requiredParts === null
        ? ""
        : detail.requiredParts.map((p) => show(p?.name)).join(", ");
  const manualTitle = detail ? (detail.manualRef ? detail.manualRef.title : undefined) : undefined;
  const manualExcerpt = detail
    ? detail.manualRef
      ? detail.manualRef.excerpt
      : undefined
    : undefined;
  const safety = detail ? detail.safetyNote : undefined;
  const contactPhone = detail ? (detail.contact ? detail.contact.phone : undefined) : undefined;
  const contactPhoneHref = contactPhone == null ? "" : String(contactPhone);
  const hasPhone = contactPhoneHref !== "";
  const primary = async () => {
    if (!technicianId) return;
    if (status === "NOT_STARTED") {
      setStatusBusy(true);
      try {
        await postJobStatus(technicianId, id, "EN_ROUTE");
        setStatusOverride("EN_ROUTE");
      } catch {
        notify("Could not update status. Please try again.");
      } finally {
        setStatusBusy(false);
      }
    } else if (status === "EN_ROUTE") {
      setStatusBusy(true);
      try {
        await postJobStatus(technicianId, id, "ON_SITE");
        setStatusOverride("ON_SITE");
      } catch {
        notify("Could not update status. Please try again.");
      } finally {
        setStatusBusy(false);
      }
    } else {
      navigate({ to: `/jobs/${id}/complete` });
    }
  };
  return (
    <Shell back="/jobs" tab={false}>
      {detailLoading ? (
        <div className="page-loader">
          <Loader size={34} label="Loading…" />
        </div>
      ) : (
        <>
          <div className="scroll-content-detail">
            <div className="detail-head">
              <span className="eyebrow">SERVICE REQUEST</span>
              <Priority value={priority} />
              <h1>{show(detail ? (detail.customer ? detail.customer.name : undefined) : undefined)}</h1>
              <p>{show(detail ? detail.jobId : undefined)}</p>
            </div>
            <div className="detail-content">
              {/* Summary card — equipment, serial, site address and scheduled visit time. */}
              <section className="surface summary-card">
                <div className="summary-photo">
                  <img src={chiller} alt={show(assetLabel)} width={816} height={816} />
                </div>
                <div>
                  <span className="eyebrow">EQUIPMENT</span>
                  <h3>{show(assetLabel)}</h3>
                  <p>Serial no. {show(detail ? (detail.asset ? detail.asset.serial : undefined) : undefined)}</p>
                </div>
                <div className="summary-divider" />
                <div className="info-line">
                  <MapPin size={18} />
                  <span>
                    {show(detail ? (detail.site ? detail.site.name : undefined) : undefined)}
                    <small>{show(detail ? (detail.site ? detail.site.address : undefined) : undefined)}</small>
                  </span>
                </div>
                <div className="info-line">
                  <Clock3 size={18} />
                  <span>
                    {timeLabel(detail ? detail.slotStart : undefined, detail ? detail.slotEnd : undefined)}
                    <small>Scheduled visit</small>
                  </span>
                </div>
              </section>
              {safety != null && safety !== "" && (
                <section className="safety">
                  <AlertTriangle size={20} />
                  <div>
                    <b>Safety first</b>
                    <p>{safety}</p>
                  </div>
                </section>
              )}
              {/* AI Pre-Job Brief — summary text with a tappable manual citation. */}
              <section className="brief">
                <div className="brief-title">
                  <span>
                    <Sparkles size={19} />
                  </span>
                  <div>
                    <small>FIELD INTELLIGENCE</small>
                    <h2>AI Pre-Job Brief</h2>
                  </div>
                </div>
                <p>{show(detail ? (detail.brief ? detail.brief.text : undefined) : undefined)}</p>
                <Button variant="ghost" className="citation" onClick={() => setCitation(true)}>
                  {show(manualTitle)} <ArrowRight size={14} />
                </Button>
              </section>
              {/* Job details — collapsible sections for symptoms, diagnosis and required parts. */}
              <div className="section-heading">
                <h2>Job details</h2>
              </div>
              {(
                [
                  ["Symptoms & Telemetry", show(detail ? detail.symptoms : undefined)],
                  ["Diagnosis History", diagnosisBody],
                  ["Required Parts", requiredPartsBody],
                ] as [string, string][]
              ).map(([title, body]) => (
                <details key={title} className="accordion">
                  <summary>
                    {title}
                    <ChevronDown size={18} />
                  </summary>
                  <p>{body}</p>
                </details>
              ))}
            </div>
          </div>
          {status !== "COMPLETED" && (
            <div className="sticky-actions">
              {status === "EN_ROUTE" && (
                <div className={`secondary-links ${hasPhone ? "cols-2" : "cols-1"}`}>                  
                  {hasPhone && (
                    <a href={`tel:${contactPhoneHref}`}>
                      <Phone size={17} /> Call
                    </a>
                  )}
                  {hasPhone && (
                    <a href={`sms:${contactPhoneHref}`}>
                      <MessageSquare size={17} /> Message
                    </a>
                  )}
                </div>
              )}
              {status === "ON_SITE" && (
                <div className="secondary-links">
                  <Link to="/jobs/$jobId/parts" params={{ jobId: id }}>
                    <Package size={17} /> View Parts
                  </Link>
                  <Link to="/assistant">
                    <Sparkles size={17} /> Ask Assistant
                  </Link>
                </div>
              )}
              <Button onClick={primary} disabled={statusBusy}>
                {statusBusy
                  ? "Updating…"
                  : status === "NOT_STARTED"
                    ? "Start Navigation"
                    : status === "EN_ROUTE"
                      ? "I've Arrived"
                      : "Complete Job"}{" "}
                <ArrowRight size={19} />
              </Button>
            </div>
          )}
          {status === "COMPLETED" && (
            <div className="completed-banner">
              <CircleCheck size={18} /> This job is completed
            </div>
          )}
          {citation && (
            <div className="modal-backdrop">
              <div className="modal">
                <Button
                  variant="icon"
                  className="close"
                  aria-label="Close"
                  onClick={() => setCitation(false)}
                >
                  <X size={20} />
                </Button>
                <h2>{show(manualTitle)}</h2>
                <p>{show(manualExcerpt)}</p>
                <Button onClick={() => setCitation(false)}>Done</Button>
              </div>
            </div>
          )}
        </>
      )}
    </Shell>
  );
}

type Chat = {
  sender: "TECHNICIAN" | "ASSISTANT";
  text: string;
  citation?: string;
  citationExcerpt?: string;
  photo?: string | undefined;
};
const seedChat: Chat[] = [];
// Assistant page — chat with the knowledge assistant (text + optional photo, with citations).
export function AssistantScreen() {
  const { user, notify } = useField();
  const technicianId = user?.technicianId;
  const [messages, setMessages] = useState<Chat[]>(seedChat);
  const [text, setText] = useState("");
  const [photo, setPhoto] = useState<string>();
  const [imageKey, setImageKey] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [cite, setCite] = useState<string>();
  const [citeExcerpt, setCiteExcerpt] = useState<string>();
  const end = useRef<HTMLDivElement>(null);
  const lastMsg = useRef<HTMLDivElement>(null);
  useEffect(() => {
    
    if (typing) {
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    } else {
      lastMsg.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [messages, typing]);
  const send = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim() && !photo) return;
    if (!technicianId) return;
    const query = text.trim();
    const sentImageKey = imageKey;
    // Build the history (prior turns) for the assistant request.
    const history = messages.map((m) => ({ sender: m.sender, text: m.text }));
    setMessages((m) => [...m, { sender: "TECHNICIAN", text: query, photo }]);
    setText("");
    setPhoto(undefined);
    setImageKey(null);
    setTyping(true);
    try {
      const res = await postAssistantMessage(technicianId, {
        jobId: null,
        text: query,
        imageKey: sentImageKey,
        history,
      });
      const firstCitation =
        res.citations && res.citations.length ? res.citations[0] : undefined;
      setMessages((m) => [
        ...m,
        {
          sender: "ASSISTANT",
          text: show(res.answer),
          ...(firstCitation && firstCitation.title != null
            ? { citation: show(firstCitation.title) }
            : {}),
          ...(firstCitation && firstCitation.excerpt != null
            ? { citationExcerpt: show(firstCitation.excerpt) }
            : {}),
        },
      ]);
    } catch {
      notify("Assistant is unavailable. Please try again.");
    } finally {
      setTyping(false);
    }
  };
  return (
    <Shell>
      <div className="assistant-screen">
      <div className="assistant-heading">
        <span>
          <Sparkles size={23} />
        </span>
        <div>
          <h1>Knowledge Assistant</h1>
          <p>Ask about any procedure, part or manual</p>
        </div>
      </div>
      {/* Chat thread — message bubbles, citation chips and the typing indicator. */}
      <div className="chat-thread">
        {messages.map((m, i) => (
          <div
            key={i}
            ref={i === messages.length - 1 ? lastMsg : undefined}
            className={`chat-item ${m.sender === "TECHNICIAN" ? "mine" : ""}`}
          >
            {m.sender === "ASSISTANT" && (
              <span className="chat-avatar">
                <Sparkles size={16} />
              </span>
            )}
            <div className="chat-bubble">
              {m.photo && <img src={m.photo} alt="Attached fault photo" />}
              <p>{m.text}</p>
              {m.citation && (
                <Button
                  variant="ghost"
                  className="citation"
                  onClick={() => {
                    setCite(m.citation);
                    setCiteExcerpt(m.citationExcerpt);
                  }}
                >
                  <Paperclip size={13} />
                  {m.citation}
                </Button>
              )}
            </div>
          </div>
        ))}
        {typing && (
          <div className="chat-item">
            <span className="chat-avatar">
              <Sparkles size={16} />
            </span>
            <div className="typing" aria-label="Assistant is typing">
              <span />
              <span />
              <span />
            </div>
          </div>
        )}
        <div ref={end} />
      </div>
      {/* Chat user input */}
      <form className="chat-compose" onSubmit={send}>
        {photo && (
          <div className="attached-photo">
            <img src={photo} alt="Photo ready to send" />
            <Button
              variant="icon"
              type="button"
              aria-label="Remove photo"
              onClick={() => {
                setPhoto(undefined);
                setImageKey(null);
              }}
            >
              <X size={16} />
            </Button>
          </div>
        )}
        <label className="compose-icon" title="Attach photo">
          <Camera size={21} />
          <input
            hidden
            type="file"
            accept="image/*"
            capture="environment"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              setPhoto(URL.createObjectURL(file));
              setImageKey(null);
              if (technicianId) {
                try {
                  const key = await uploadFile(technicianId, "", "PHOTO", file);
                  setImageKey(key);
                } catch {
                  notify("Photo upload failed. Please try again.");
                }
              }
            }}
          />
        </label>
        <input
          aria-label="Message"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask a question..."
        />
        <Button
          type="button"
          variant="icon"
          aria-label="Voice input"
          onClick={() => setText("How do I diagnose this fault?")}
        >
          <Mic size={20} />
        </Button>
        <Button
          type="submit"
          variant="icon"
          className="send-button"
          aria-label="Send message"
          disabled={!text.trim() && !photo}
        >
          <Send size={19} />
        </Button>
      </form>
      </div>
      {/* Citation detail popup */}
      {cite && (
        <div className="modal-backdrop">
          <div className="modal">
            <h2>{cite}</h2>
            <p>{citeExcerpt === undefined ? "NA" : citeExcerpt}</p>
            <Button
              onClick={() => {
                setCite(undefined);
                setCiteExcerpt(undefined);
              }}
            >
              Done
            </Button>
          </div>
        </div>
      )}
    </Shell>
  );
}
export // Map an API stockLevel to the pill class/label the Parts screen uses.
const stockPillClass = (level?: string | null): string =>
  level === "AVAILABLE" ? "available" : level === "LOW" ? "priority-medium" : "priority-critical";
const stockPillLabel = (level?: string | null): string =>
  level === undefined
    ? "NA"
    : level === null || level === ""
      ? ""
      : level === "AVAILABLE"
        ? "Available"
        : level === "LOW"
          ? "Low stock"
          : "Unavailable";

// Parts & Inventory page — required parts, truck stock, and request/mark-used actions.
export function PartsScreen({ id }: { id: string }) {
  const { user, notify } = useField();
  const technicianId = user?.technicianId;
  const [stock, setStock] = useState<DepotStockResponse | null>(null);
  const [stockLoading, setStockLoading] = useState(true);
  const [request, setRequest] = useState(false);
  const [requestPart, setRequestPart] = useState("");
  const [qty, setQty] = useState(1);
  const [scan, setScan] = useState(false);
  const [lookingUp, setLookingUp] = useState(false);
  const [selected, setSelected] = useState<string>();
  
  const [selectedRequired, setSelectedRequired] = useState<string>();
  const [markBusy, setMarkBusy] = useState(false);
  const [requestBusy, setRequestBusy] = useState(false);
  useEffect(() => {
    let active = true;
    if (technicianId) {
      setStockLoading(true);
      setStock(null);
      getDepotStock(technicianId, id)
        .then((data) => {
          if (active) setStock(data);
        })
        .catch(() => {
          if (active) setStock(null);
        })
        .finally(() => {
          if (active) setStockLoading(false);
        });
    } else {
      setStock(null);
      setStockLoading(false);
    }
    return () => {
      active = false;
    };
  }, [technicianId, id]);
  // Reload depot stock after a mutation so quantities stay accurate.
  const reloadStock = () => {
    if (!technicianId) return;
    getDepotStock(technicianId, id)
      .then((data) => setStock(data))
      .catch(() => {});
  };
  // Shared handler for a scanned or manually entered barcode value.
  const lookupBarcode = async (barcode: string) => {
    const value = barcode.trim();
    if (!technicianId || !value) return;
    setScan(false);
    setLookingUp(true);
    try {
      const part = await getPartByBarcode(technicianId, value);
      notify(`Matched ${show(part.name)}`);
    } catch {
      notify("No matching part found.");
    } finally {
      setLookingUp(false);
    }
  };
  const requiredParts =
    stock && Array.isArray(stock.requiredParts) ? stock.requiredParts : [];
  // With a single required part, auto-select it; with multiple the technician
  // must pick one before marking it used.
  const activeRequiredId =
    requiredParts.length === 1 ? (requiredParts[0]?.partId ?? undefined) : selectedRequired;
  const required = requiredParts.find((p) => p.partId === activeRequiredId);
  // qtyRemaining drives whether the selected required part can still be used.
  const remaining = required?.qtyRemaining;
  const items = stock && Array.isArray(stock.items) ? stock.items : [];
  const selectedItem = items.find((p) => p.partId === selected);
  return (
    <Shell
      back={detail(id)}
      tab={false}
      action={
        <Button
          variant="icon"
          aria-label="Scan barcode"
          disabled={!technicianId}
          onClick={() => setScan(true)}
        >
          <ScanLine size={22} />
        </Button>
      }
    >
      {stockLoading ? (
        <div className="page-loader">
          <Loader size={34} label="Loading…" />
        </div>
      ) : (
        <>
          <div className="page-heading">
            <h1>Parts & Inventory</h1>
            <p>View and manage parts for this job</p>
          </div>
          <div className="parts-body">
            {/* Parts required for this job */}
            <div className="required-section">
              <div className="required-top">
                <span>
                  <CircleCheck size={18} /> Required for this job
                </span>
                <span className="pill priority-low">
                  {requiredParts.length} {requiredParts.length === 1 ? "part" : "parts"}
                </span>
              </div>
              {requiredParts.length === 0 ? (
                <div className="required-card">
                  <p className="muted">No parts are required for this job.</p>
                </div>
              ) : (
                requiredParts.map((rp, i) => {
                  const rpId = rp.partId ?? undefined;
                  const isSelected = rpId != null && rpId === activeRequiredId;
                  const rpRemaining = rp.qtyRemaining;
                  const depleted = rpRemaining === 0;
                  const selectable = requiredParts.length > 1;
                  return (
                    <button
                      type="button"
                      key={rpId ?? i}
                      className={`required-card ${selectable ? "selectable" : ""} ${
                        isSelected ? "selected" : ""
                      }`}
                      aria-pressed={selectable ? isSelected : undefined}
                      onClick={selectable ? () => setSelectedRequired(rpId) : undefined}
                    >
                      <div className="required-detail">
                        <span className="part-illustration">
                          <Package size={33} />
                        </span>
                        <div>
                          <h3>{show(rp.name)}</h3>
                          <p>{show(rp.partId)}</p>
                          <small>
                            Required: {show(rp.qtyRequired)} · Remaining:{" "}
                            {show(rp.qtyRemaining)}
                          </small>
                        </div>
                        <div className="required-qty">
                          On truck: {show(rp.available)}
                          <span className={`pill ${stockPillClass(rp.stockLevel)}`}>
                            {stockPillLabel(rp.stockLevel)}
                          </span>
                          {depleted && <span className="pill priority-low">Fully used</span>}
                        </div>
                      </div>
                    </button>
                  );
                })
              )}
            </div>
            {/* Parts carried on the truck */}
            <div className="section-heading">
              <h2>Truck Stock</h2>
              <span>{items.length} items</span>
            </div>
            <div className="stock-list">
              {items.map((p, i) => (
                <Button
                  variant="ghost"
                  key={p.partId ?? i}
                  className="stock-row"
                  onClick={() => setSelected(p.partId ?? undefined)}
                >
                  <span className="stock-icon">
                    <Package size={27} />
                  </span>
                  <span className="stock-info">
                    <b>{show(p.name)}</b>
                    <span>{show(p.partId)}</span>
                    <small>{show(p.description)}</small>
                  </span>
                  <span className="qty-badge">Qty: {show(p.available)}</span>
                  <ChevronRight size={19} />
                </Button>
              ))}
            </div>
          </div>
          {/* Bottom actions — request a part or mark the required part as used */}
          <div className="sticky-actions">
            <Button
              onClick={() => {
                setRequestPart(required ? show(required.partId) : "");
                setRequest(true);
              }}
            >
              <ShoppingCart size={18} /> Request Additional Part
            </Button>
            <Button
              variant="outline"
              disabled={!required || required.partId == null || remaining === 0 || markBusy}
              onClick={async () => {
                if (!technicianId || !required || required.partId == null) return;
                setMarkBusy(true);
                try {
                  await postPartUsed(technicianId, id, String(required.partId), 1);
                  notify(`${show(required.name)} marked as used`);
                  reloadStock();
                } catch {
                  notify("Could not mark part as used. Please try again.");
                } finally {
                  setMarkBusy(false);
                }
              }}
            >
              <Package size={18} />{" "}
              {markBusy
                ? "Marking…"
                : requiredParts.length > 1 && !required
                  ? "Select a part"
                  : remaining === 0
                    ? "Fully used"
                    : "Mark Part Used"}
            </Button>
          </div>
          {scan && (
            <BarcodeScanner onResult={(value) => void lookupBarcode(value)} onClose={() => setScan(false)} />
          )}
          {lookingUp && (
            <div className="modal-backdrop">
              <div className="modal">
                <Loader size={34} label="Looking up part…" />
                <p>Searching truck stock for a match.</p>
              </div>
            </div>
          )}
          {/* Part detail popup for a selected truck-stock item */}
          {selected && (
            <div className="modal-backdrop">
              <div className="modal">
                <Button
                  variant="icon"
                  className="close"
                  aria-label="Close"
                  onClick={() => setSelected(undefined)}
                >
                  <X size={20} />
                </Button>
                <Package size={32} className="teal" />
                <h2>{show(selectedItem ? selectedItem.name : undefined)}</h2>
                <p>
                  {show(selected)} · {show(selectedItem ? selectedItem.description : undefined)}
                </p>
                <p>On truck: {show(selectedItem ? selectedItem.available : undefined)}</p>
                <Button
                  onClick={() => {
                    setRequestPart(selectedItem ? show(selectedItem.partId) : "");
                    setSelected(undefined);
                    setRequest(true);
                  }}
                >
                  Request this part
                </Button>
              </div>
            </div>
          )}
          {/* Request a part popup */}
          {request && (
            <div className="modal-backdrop">
              <form
                className="modal"
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (!technicianId || !requestPart) return;
                  setRequestBusy(true);
                  try {
                    await postPartRequest(technicianId, id, requestPart, qty);
                    setRequest(false);
                    notify("Request sent — Ops team notified.");
                  } catch {
                    notify("Could not send request. Please try again.");
                  } finally {
                    setRequestBusy(false);
                  }
                }}
              >
                <Button
                  variant="icon"
                  type="button"
                  className="close"
                  aria-label="Close"
                  onClick={() => setRequest(false)}
                >
                  <X size={20} />
                </Button>
                <h2>Request a part</h2>
                <label>
                  Part
                  <select value={requestPart} onChange={(e) => setRequestPart(e.target.value)}>
                    {items.map((p, i) => (
                      <option key={p.partId ?? i} value={p.partId ?? ""}>
                        {show(p.name)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Quantity
                  <div className="stepper">
                    <Button
                      variant="icon"
                      type="button"
                      onClick={() => setQty(Math.max(1, qty - 1))}
                    >
                      <Minus size={18} />
                    </Button>
                    <b>{qty}</b>
                    <Button variant="icon" type="button" onClick={() => setQty(qty + 1)}>
                      <Plus size={18} />
                    </Button>
                  </div>
                </label>
                <Button type="submit" disabled={requestBusy}>
                  {requestBusy ? "Submitting…" : "Submit Request"}
                </Button>
              </form>
            </div>
          )}
        </>
      )}
    </Shell>
  );
}
// Job Completion page — checklist, photos and customer signature before submitting the job as done.
export function CompletionScreen({ id }: { id: string }) {
  const { user, offline, queue, notify } = useField();
  const technicianId = user?.technicianId;
  const navigate = useNavigate();
  const [detailData, setDetailData] = useState<JobDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [checked, setChecked] = useState<string[]>([]);
  const [photos, setPhotos] = useState<string[]>([]);
  const [photoKeys, setPhotoKeys] = useState<string[]>([]);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [signed, setSigned] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [completeBusy, setCompleteBusy] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  useEffect(() => {
    let active = true;
    if (technicianId) {
      setDetailLoading(true);
      setDetailData(null);
      getJobDetail(technicianId, id)
        .then((data) => {
          if (active) setDetailData(data);
        })
        .catch(() => {
          if (active) setDetailData(null);
        })
        .finally(() => {
          if (active) setDetailLoading(false);
        });
    } else {
      setDetailData(null);
      setDetailLoading(false);
    }
    return () => {
      active = false;
    };
  }, [technicianId, id]);
  const checklistItems: string[] =
    detailData && Array.isArray(detailData.checklist)
      ? detailData.checklist.map((c) => show(c?.item))
      : [];
  const point = (e: PointerEvent<HTMLCanvasElement>) => {
    const c = canvas.current;
    if (!c) return null;
    const r = c.getBoundingClientRect();
    return {
      x: ((e.clientX - r.left) * c.width) / r.width,
      y: ((e.clientY - r.top) * c.height) / r.height,
    };
  };
  const start = (e: PointerEvent<HTMLCanvasElement>) => {
    const c = canvas.current;
    const p = point(e);
    if (!c || !p) return;
    c.setPointerCapture(e.pointerId);
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.strokeStyle = "#1F3864";
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    drawing.current = true;
  };
  const move = (e: PointerEvent<HTMLCanvasElement>) => {
    if (!drawing.current) return;
    const p = point(e);
    const ctx = canvas.current?.getContext("2d");
    if (!p || !ctx) return;
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    setSigned(true);
  };
  return (
    <Shell back={detail(id)} tab={false}>
      {detailLoading ? (
        <div className="page-loader">
          <Loader size={34} label="Loading…" />
        </div>
      ) : (
        <>
      <div className="page-heading">
        <h1>Job Completion</h1>
        <p>{show(detailData ? detailData.jobId : undefined)}</p>
      </div>
      <div className="completion-body">
        {/* Verification checklist — confirm the work done on site */}
        <section className="completion-section">
          <div className="section-heading">
            <h2>Verification checklist</h2>
            <span>
              {checked.length}/{checklistItems.length}
            </span>
          </div>
          <p>Confirm the work completed on site.</p>
          <div className="checklist">
            {checklistItems.map((item) => (
              <label key={item} className="check-row">
                <input
                  type="checkbox"
                  checked={checked.includes(item)}
                  onChange={(e) => {
                    setChecked((v) =>
                      e.target.checked ? [...v, item] : v.filter((x) => x !== item),
                    );
                    if (offline) queue();
                  }}
                />
                <span>{item}</span>
              </label>
            ))}
          </div>
        </section>
        {/* Optional photo evidence of the completed work */}
        <section className="completion-section">
          <div className="section-heading">
            <h2>Photo evidence</h2>
            <span>Optional</span>
          </div>
          <div className="photo-grid">
            {photos.map((p, i) => (
              <div className="photo-tile" key={i}>
                <img src={p} alt={`Evidence ${i + 1}`} />
                <Button
                  variant="icon"
                  aria-label="Remove photo"
                  onClick={() => {
                    setPhotos((v) => v.filter((_, n) => n !== i));
                    setPhotoKeys((v) => v.filter((_, n) => n !== i));
                  }}
                >
                  <X size={15} />
                </Button>
              </div>
            ))}
            <label className="add-photo">
              <Camera size={23} />
              <span>Add Photo</span>
              <input
                hidden
                type="file"
                accept="image/*"
                capture="environment"
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  if (!f || !technicianId) return;
                  const preview = URL.createObjectURL(f);
                  setPhotos((v) => [...v, preview]);
                  if (offline) queue();
                  setPhotoBusy(true);
                  try {
                    const key = await uploadFile(technicianId, id, "PHOTO", f);
                    setPhotoKeys((v) => [...v, key]);
                  } catch {
                    notify("Photo upload failed. Please try again.");
                    setPhotos((v) => v.filter((p) => p !== preview));
                  } finally {
                    setPhotoBusy(false);
                  }
                }}
              />
            </label>
          </div>
        </section>
        {/* Customer sign-off captured on a canvas */}
        <section className="completion-section">
          <div className="section-heading">
            <h2>Customer signature</h2>
            <Button
              variant="ghost"
              onClick={() => {
                canvas.current?.getContext("2d")?.clearRect(0, 0, 600, 170);
                setSigned(false);
              }}
            >
              Clear
            </Button>
          </div>
          <div className="signature-pad">
            {!signed && (
              <div>
                <PenLine size={20} /> Sign here
              </div>
            )}
            <canvas
              ref={canvas}
              width={600}
              height={170}
              aria-label="Customer signature area"
              onPointerDown={start}
              onPointerMove={move}
              onPointerUp={() => (drawing.current = false)}
            />
          </div>
        </section>
      </div>
      {/* Bottom action — enabled once the checklist and signature are complete */}
      <div className="sticky-actions">
        <p className="helper">
          {checked.length !== checklistItems.length || !signed
            ? "Complete all checklist items and capture a signature to finish"
            : ""}
        </p>
        <Button
          disabled={checked.length !== checklistItems.length || !signed || photoBusy}
          onClick={() => setConfirm(true)}
        >
          Complete Job <Check size={19} />
        </Button>
      </div>
      {/* Final confirm popup — uploads signature then submits the completion */}
      {confirm && (
        <div className="modal-backdrop">
          <div className="modal">
            <CircleCheck size={34} className="teal" />
            <h2>Complete this job?</h2>
            <p>
              {checked.length} checks confirmed, {photos.length} photos and a customer signature
              will be submitted.
            </p>
            <div className="modal-actions">
              <Button variant="outline" disabled={completeBusy} onClick={() => setConfirm(false)}>
                Cancel
              </Button>
              <Button
                disabled={completeBusy}
                onClick={async () => {
                  if (!technicianId) return;
                  setCompleteBusy(true);
                  try {
                    // Upload the signature canvas as a SIGNATURE and collect its key.
                    let signatureKey: string | null = null;
                    const c = canvas.current;
                    if (c) {
                      const blob = await new Promise<Blob | null>((resolve) =>
                        c.toBlob((b) => resolve(b), "image/png"),
                      );
                      if (blob) {
                        const sigFile = new File([blob], "signature.png", { type: "image/png" });
                        signatureKey = await uploadFile(technicianId, id, "SIGNATURE", sigFile);
                      }
                    }
                    await postJobComplete(technicianId, id, {
                      checklist: checklistItems.map((item) => ({
                        item,
                        done: checked.includes(item),
                      })),
                      photoKeys,
                      signatureKey,
                      debrief: "",
                    });
                    notify(
                      offline ? "Job completed — Pending sync" : "Job completed — great work!",
                    );
                    navigate({ to: "/home" });
                  } catch {
                    notify("Could not complete the job. Please try again.");
                  } finally {
                    setCompleteBusy(false);
                  }
                }}
              >
                {completeBusy ? "Submitting…" : "Confirm"}
              </Button>
            </div>
          </div>
        </div>
      )}
        </>
      )}
    </Shell>
  );
}
// Profile page — technician details, stats, and tabs for jobs/certs/skills/settings.
export function ProfileScreen() {
  const { user, me, meLoading, logout, offline, setOffline, pending, notify } = useField();
  const technicianId = user?.technicianId;
  const navigate = useNavigate();
  const [tab, setTab] = useState("Recent Jobs");
  const [edit, setEdit] = useState(false);
  const [name, setName] = useState("");
  const [recentItems, setRecentItems] = useState<JobListItem[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  useEffect(() => {
    if (me && me.name != null) setName(me.name);
  }, [me]);
  useEffect(() => {
    let active = true;
    if (technicianId) {
      setRecentLoading(true);
      setRecentItems([]);
      getJobs(technicianId, "completed")
        .then((data) => {
          if (active) setRecentItems(Array.isArray(data.jobs) ? data.jobs : []);
        })
        .catch(() => {
          if (active) setRecentItems([]);
        })
        .finally(() => {
          if (active) setRecentLoading(false);
        });
    } else {
      setRecentItems([]);
      setRecentLoading(false);
    }
    return () => {
      active = false;
    };
  }, [technicianId]);
  const recentJobs = recentItems.map(toJob);
  const meInitials = me
    ? me.initials != null && me.initials !== ""
      ? me.initials
      : initialsFrom(me.name)
    : undefined;
  const certs = me ? me.certifications : undefined;
  const skills = me ? me.skills : undefined;
  const ffr = me ? me.stats?.firstTimeFixRate : undefined;
  return (
    <Shell>
      {meLoading ? (
        <div className="page-loader">
          <Loader size={34} label="Loading…" />
        </div>
      ) : (
        <>
          {/* Profile header — avatar, name/title, edit button and certifications */}
          <div className="profile-top">
            <div className="profile-identity">
              <span className="profile-avatar">
                {show(meInitials)}
                <span />
              </span>
              <div className="flex flex-col justify-center">
                <h1>{me ? show(name) : show(undefined)}</h1>
                <p>{show(me ? me.title : undefined)}</p>
              </div>
              <Button variant="outline" onClick={() => setEdit(true)}>
                <Pencil size={15} /> Edit
              </Button>
            </div>
            <div className="certifications">
              {certs === undefined ? (
                <span>NA</span>
              ) : certs === null ? (
                <span></span>
              ) : certs.length ? (
                certs.map((c, i) => <span key={i}>{show(c?.model)}</span>)
              ) : (
                <span>NA</span>
              )}
            </div>
          </div>
          {/* Key performance stats — jobs this week, first-time-fix rate, rating */}
          <div className="profile-stats">
            <div>
              <ClipboardList size={20} />
              <span>Jobs this week</span>
              <strong>{show(me ? me.stats?.jobsThisWeek : undefined)}</strong>
            </div>
            <div>
              <ShieldCheck size={20} />
              <span>First-Time-Fix</span>
              <strong>
                {ffr === undefined ? "NA" : ffr === null ? "" : `${Math.round(ffr * 100)}%`}
              </strong>
            </div>
            <div>
              <Star size={20} />
              <span>Rating</span>
              <strong>{show(me ? me.stats?.rating : undefined)}</strong>
            </div>
          </div>
          {/* Tab switcher — selects which panel shows below */}
          <div className="profile-tabs">
            {["Recent Jobs", "Certifications", "Skills", "About"].map((t) => (
              <Button
                variant="ghost"
                key={t}
                className={tab === t ? "active" : ""}
                onClick={() => setTab(t)}
              >
                {t}
              </Button>
            ))}
          </div>
          {/* Recent Jobs panel — list of completed jobs */}
          {tab === "Recent Jobs" ? (
            recentLoading ? (
              <div className="page-loader">
                <Loader size={34} label="Loading…" />
              </div>
            ) : (
              <div className="history-list">
                {recentJobs.length ? (
                  recentJobs.map((j) => (
                    <Link to={detail(j.id)} key={j.id} className="history-row">
                      <img
                        src={chiller}
                        alt="Industrial equipment"
                        width={816}
                        height={816}
                        loading="lazy"
                      />
                      <div>
                        <b>{j.id}</b>
                        <strong>{j.customer}</strong>
                        <span>{j.asset}</span>
                        <small>
                          <CalendarDays size={13} />
                          {j.date} <MapPin size={13} />
                          {j.address === "NA"
                            ? "NA"
                            : j.address === ""
                              ? ""
                              : j.address.split(",").slice(-2).join(",").trim()}
                        </small>
                      </div>
                      <div className="history-end">
                        <span className="pill available">
                          <Check size={12} />
                          Completed
                        </span>
                        {
                          j.rating && 
                          <span>
                            <Star size={13} fill="currentColor" />{" "}
                            {j.rating === undefined ? "" : j.rating}
                          </span>
                        }
                      </div>
                      <ChevronRight size={17} />
                    </Link>
                  ))
                ) : (
                  <p className="empty">No jobs found.</p>
                )}
              </div>
            )
          ) : 
          // Certifications panel
          tab === "Certifications" ? (
            <div className="profile-panel">
              <h2>Certifications</h2>
              {certs === undefined ? (
                <p>
                  <BadgeCheck size={19} />
                  NA
                </p>
              ) : certs === null ? (
                <p>
                  <BadgeCheck size={19} />
                </p>
              ) : certs.length ? (
                certs.map((c, i) => (
                  <p key={i}>
                    <BadgeCheck size={19} />
                    {show(c?.model)}
                  </p>
                ))
              ) : (
                <p>
                  <BadgeCheck size={19} />
                  NA
                </p>
              )}
            </div>
          ) : 
          /* Skills panel */ 
          tab === "Skills" ? (
            <div className="profile-panel">
              <h2>Skills</h2>
              {skills === undefined ? (
                <p>
                  <Wrench size={19} />
                  NA
                </p>
              ) : skills === null ? (
                <p>
                  <Wrench size={19} />
                </p>
              ) : skills.length ? (
                skills.map((s, i) => (
                  <p key={i}>
                    <Wrench size={19} />
                    {show(s?.skill)}
                  </p>
                ))
              ) : (
                <p>
                  <Wrench size={19} />
                  NA
                </p>
              )}
            </div>
          ) : (
            /* Settings/About panel — offline toggle and sign out */
            <div className="profile-panel">
              <h2>Settings</h2>
              <label className="toggle-row">
                <span>
                  <Wifi size={19} />
                  Simulate offline mode
                </span>
                <input
                  type="checkbox"
                  checked={offline}
                  onChange={(e) => setOffline(e.target.checked)}
                />
              </label>
              {pending > 0 && <p className="pending-note">{pending} changes pending sync</p>}
              <Button
                variant="outline"
                className="signout"
                onClick={() => {
                  logout();
                  navigate({ to: "/login" });
                }}
              >
                <LogOut size={18} /> Sign out
              </Button>
            </div>
          )}
          {/* Edit profile popup */}
          {edit && (
            <div className="modal-backdrop">
              <form
                className="modal"
                onSubmit={(e) => {
                  e.preventDefault();
                  setEdit(false);
                  notify("Profile updated");
                }}
              >
                <h2>Edit profile</h2>
                <label>
                  Name
                  <input value={name} onChange={(e) => setName(e.target.value)} required />
                </label>
                <div className="modal-actions">
                  <Button variant="outline" type="button" onClick={() => setEdit(false)}>
                    Cancel
                  </Button>
                  <Button type="submit">Save</Button>
                </div>
              </form>
            </div>
          )}
        </>
      )}
    </Shell>
  );
}

// Local "YYYY-MM-DD" key for a Date.
const dayKey = (d: Date): string => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
};

// Local midnight for a date (strips the time component).
const startOfDay = (d: Date): Date => new Date(d.getFullYear(), d.getMonth(), d.getDate());
// First day of the month for a date.
const startOfMonth = (d: Date): Date => new Date(d.getFullYear(), d.getMonth(), 1);
// Friendly long label for the selected date header.
const longDateLabel = (d: Date): string =>
  d.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });

// Fixed, selectable availability time slots. The 13:00–14:00 gap is an
// intentional lunch break and is not offered as a slot.
const AVAILABILITY_SLOTS: { key: string; start: string; end: string; label: string }[] = [
  { key: "09:00-11:00", start: "09:00", end: "11:00", label: "09:00 – 11:00" },
  { key: "11:00-13:00", start: "11:00", end: "13:00", label: "11:00 – 13:00" },
  { key: "14:00-16:00", start: "14:00", end: "16:00", label: "14:00 – 16:00" },
  { key: "16:00-18:00", start: "16:00", end: "18:00", label: "16:00 – 18:00" },
];

// Add-availability dialog — pick one or more time slots to open up for the selected day.
function AddAvailabilityDialog({
  dateKey,
  busy = false,
  failedKeys = [],
  takenKeys = {},
  onCancel,
  onSave,
}: {
  dateKey: string;
  busy?: boolean;
  failedKeys?: string[];
  // Slot keys already present for this day, mapped to how they're taken.
  takenKeys?: Record<string, "booked" | "available">;
  onCancel: () => void;
  onSave: (
    slots: { key: string; start: string; end: string; note: string | null }[],
  ) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const toggle = (key: string) => {
    if (takenKeys[key]) return; // can't select a slot that already exists
    setError("");
    setSelected((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (selected.length === 0) {
      setError("Select at least one time slot.");
      return;
    }
    const trimmedNote = note.trim() || null;
    const slots = AVAILABILITY_SLOTS.filter(
      (s) => selected.includes(s.key) && !takenKeys[s.key],
    ).map((s) => ({
      key: s.key,
      start: s.start,
      end: s.end,
      note: trimmedNote,
    }));
    onSave(slots);
  };
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Add availability">
      <form className="modal" onSubmit={submit}>
        <h2>Add availability</h2>
        <p className="subtitle">{dateKey}</p>
        <div className="slot-choice-grid">
          {AVAILABILITY_SLOTS.map((s) => {
            const taken = takenKeys[s.key];
            const active = !taken && selected.includes(s.key);
            const failed = !taken && failedKeys.includes(s.key);
            const hint = taken === "booked" ? "Booked" : taken ? "Added" : null;
            return (
              <Button
                key={s.key}
                type="button"
                variant={active ? "primary" : "outline"}
                aria-pressed={active}
                disabled={busy || Boolean(taken)}
                title={taken ? `Already ${hint?.toLowerCase()} for this day` : undefined}
                className={
                  failed ? "slot-choice-failed" : taken ? "slot-choice-taken" : undefined
                }
                onClick={() => toggle(s.key)}
              >
                {s.label}
                {hint ? <small className="slot-choice-hint">{hint}</small> : null}
              </Button>
            );
          })}
        </div>
        {failedKeys.length > 0 ? (
          <p className="form-error">
            {failedKeys.length} slot{failedKeys.length > 1 ? "s" : ""} couldn't be saved. The
            affected slot{failedKeys.length > 1 ? "s remain" : " remains"} selected — try again.
          </p>
        ) : null}
        <label>
          Note (optional)
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Available for emergency callouts"
            maxLength={120}
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="modal-actions">
          <Button variant="outline" type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy || selected.length === 0}>
            {busy
              ? "Saving…"
              : `Save ${selected.length || ""} slot${selected.length === 1 ? "" : "s"}`.trim()}
          </Button>
        </div>
      </form>
    </div>
  );
}

// Calendar page — month view with availability, plus assigned/available slots for the selected day.
export function CalendarScreen() {
  const { user, notify } = useField();
  const technicianId = user?.technicianId;
  const [selected, setSelected] = useState<Date>(() => new Date());
  // Selected-day slots, split by status, from GET /availability.
  const [assignedSlots, setAssignedSlots] = useState<AvailabilitySlot[]>([]);
  const [availableSlots, setAvailableSlots] = useState<AvailabilitySlot[]>([]);
  const [slotsLoading, setSlotsLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [addBusy, setAddBusy] = useState(false);
  // Slot keys that failed to save on the last Add attempt (for retry highlight).
  const [addFailedKeys, setAddFailedKeys] = useState<string[]>([]);
  // The month currently shown in the calendar (first day of that month).
  const [month, setMonth] = useState<Date>(() => startOfMonth(new Date()));
  // Per-day slot counts for the viewed month, keyed by "YYYY-MM-DD".
  const [slotCounts, setSlotCounts] = useState<Record<string, SlotCountDay>>({});

  // Fetch per-day slot counts for the viewed month. Start from today for the
  // current month, or the 1st for future months. Re-runs when the month or
  // technician changes.
  const loadSlotCounts = () => {
    if (!technicianId) {
      setSlotCounts({});
      return;
    }
    const today = startOfDay(new Date());
    const monthStart = startOfMonth(month);
    const fetchFrom = monthStart < today ? today : monthStart;
    getSlotCounts(technicianId, dayKey(fetchFrom))
      .then((days) => {
        const map: Record<string, SlotCountDay> = {};
        for (const d of days) if (d && d.date) map[d.date] = d;
        setSlotCounts(map);
      })
      .catch(() => setSlotCounts({}));
  };
  useEffect(() => {
    loadSlotCounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [technicianId, month]);

  // Load the selected day's slots: AVAILABLE (available) + BOOKED (assigned).
  const loadSlots = () => {
    if (!technicianId) {
      setAssignedSlots([]);
      setAvailableSlots([]);
      setSlotsLoading(false);
      return;
    }
    const date = dayKey(selected);
    setSlotsLoading(true);
    Promise.all([
      getAvailability(technicianId, date, "AVAILABLE"),
      getAvailability(technicianId, date, "BOOKED"),
    ])
      .then(([available, booked]) => {
        setAvailableSlots(Array.isArray(available) ? available : []);
        setAssignedSlots(Array.isArray(booked) ? booked : []);
      })
      .catch(() => {
        setAvailableSlots([]);
        setAssignedSlots([]);
      })
      .finally(() => setSlotsLoading(false));
  };
  useEffect(() => {
    loadSlots();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [technicianId, selected]);

  const selectedKey = dayKey(selected);
  // Calendar markers come from the slot-count API: a day with available slots
  // gets the green "available" dot, a day with booked slots gets the blue dot.
  const toDate = (k: string) => new Date(`${k}T00:00:00`);
  const availableDates = Object.values(slotCounts)
    .filter((d) => (d.available_slot_count ?? 0) > 0)
    .map((d) => toDate(d.date));
  const bookedDates = Object.values(slotCounts)
    .filter((d) => (d.booked_slot_count ?? 0) > 0)
    .map((d) => toDate(d.date));

  // Slot keys ("HH:mm-HH:mm") already present for the selected day, mapped to
  // how they're taken, so the Add dialog can disable them. BOOKED wins over
  // AVAILABLE when both somehow exist for the same slot.
  const takenSlotKeys = useMemo(() => {
    const map: Record<string, "booked" | "available"> = {};
    for (const s of availableSlots) {
      if (s?.start && s?.end) map[`${s.start}-${s.end}`] = "available";
    }
    for (const s of assignedSlots) {
      if (s?.start && s?.end) map[`${s.start}-${s.end}`] = "booked";
    }
    return map;
  }, [availableSlots, assignedSlots]);

  const handleAdd = async (
    slots: { key: string; start: string; end: string; note: string | null }[],
  ) => {
    if (!technicianId || slots.length === 0) return;
    setAddBusy(true);
    setAddFailedKeys([]);
    // One POST per selected slot; allSettled so one failure doesn't block others.
    const results = await Promise.allSettled(
      slots.map((slot) =>
        addAvailability(technicianId, {
          date: selectedKey,
          start: slot.start,
          end: slot.end,
          note: slot.note,
        }),
      ),
    );
    const failedKeys = slots
      .filter((_, i) => results[i]?.status === "rejected")
      .map((s) => s.key);
    const savedCount = slots.length - failedKeys.length;

    // Refresh once after all calls so any successful slots show immediately.
    if (savedCount > 0) {
      loadSlots();
      loadSlotCounts();
    }

    if (failedKeys.length === 0) {
      notify(
        savedCount === 1 ? "Slot added successfully" : `${savedCount} slots added successfully`,
      );
      setAddFailedKeys([]);
      setAdding(false);
    } else {
      
      setAddFailedKeys(failedKeys);
      if (savedCount > 0) {
        notify(`${savedCount} saved, ${failedKeys.length} failed. Please retry.`);
      } else {
        notify("Couldn't add slots. Please try again.");
      }
    }
    setAddBusy(false);
  };
  const handleDelete = (slotId: string) => {
    if (!technicianId) return;
    deleteAvailability(technicianId, slotId)
      .then(() => {
        loadSlots();
        loadSlotCounts();
        notify("Slot deleted successfully");
      })
      .catch((err) => {
        if (err instanceof AvailabilityError && err.status === 409) {
          notify("This slot is booked and can't be deleted.");
        } else if (err instanceof AvailabilityError && err.status === 404) {
          setAvailableSlots((prev) => prev.filter((s) => s.slotId !== slotId));
          notify("Slot not found. It may have already been removed.");
        } else {
          notify("Failed to delete slot. Please try again.");
        }
      });
  };

  return (
    <Shell title="Calendar">
      <div className="p-4">

        <div className="calendar-page">
        <div className="subtitle">Your schedule and availability.</div>
        {/* Month calendar — dots mark days with available/booked slots */}
        <div className="calendar-wrap">
          <Calendar
            mode="single"
            month={month}
            onMonthChange={setMonth}
            selected={selected}
            onSelect={(d) => d && setSelected(d)}
            disabled={{ before: startOfDay(new Date()) }}
            showOutsideDays
            modifiers={{ hasSlot: availableDates, hasBooked: bookedDates }}
            modifiersClassNames={{ hasSlot: "rdp-has-slot", hasBooked: "rdp-has-job" }}
          />
        </div>
        <div className="calendar-legend">
          <span>
            <i className="dot dot-slot" /> Available
          </span>
          <span>
            <i className="dot dot-job" /> Booked
          </span>
        </div>

        {/* Selected day header with the "Add slot" button */}
        <div className="day-detail-head">
          <h2>{longDateLabel(selected)}</h2>
          <Button
            variant="outline"
            onClick={() => {
              setAddFailedKeys([]);
              setAdding(true);
            }}
            disabled={!technicianId}
          >
            <CalendarPlus size={16} /> Add slot
          </Button>
        </div>

        {/* Booked slots for the selected day (read-only) */}
        <section className="day-section">
          <h3 className="section-label">
            <CalendarDays size={16} /> Assigned slots
          </h3>
          {slotsLoading ? (
            <div className="page-loader">
              <Loader size={28} label="Loading…" />
            </div>
          ) : assignedSlots.length ? (
            <ul className="slot-list">
              {assignedSlots.map((s) => (
                <li key={s.slotId} className="slot-row">
                  <span className="slot-time">
                    <Clock3 size={15} />
                    {s.start} – {s.end}
                  </span>
                  {s.note ? <span className="slot-note">{s.note}</span> : null}
                  {s.appointmentId ? (
                    <span className="slot-appointment">#{s.appointmentId}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty">No assigned slots for this day.</p>
          )}
        </section>

        {/* Technician-set available slots for the day, each removable */}
        <section className="day-section">
          <h3 className="section-label">
            <CalendarClock size={16} /> Available slots
          </h3>
          {slotsLoading ? (
            <div className="page-loader">
              <Loader size={28} label="Loading…" />
            </div>
          ) : availableSlots.length ? (
            <ul className="slot-list">
              {availableSlots.map((s) => (
                <li key={s.slotId} className="slot-row">
                  <span className="slot-time">
                    <Clock3 size={15} />
                    {s.start} – {s.end}
                  </span>
                  {s.note ? <span className="slot-note">{s.note}</span> : null}
                  <Button
                    variant="icon"
                    aria-label="Remove availability"
                    onClick={() => handleDelete(s.slotId)}
                  >
                    <Trash2 size={16} />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="empty">No available slots for this day.</p>
          )}
        </section>
        </div>
      </div>

      {/* Add-availability popup for the selected day */}
      {adding && (
        <AddAvailabilityDialog
          dateKey={selectedKey}
          busy={addBusy}
          failedKeys={addFailedKeys}
          takenKeys={takenSlotKeys}
          onCancel={() => {
            setAddFailedKeys([]);
            setAdding(false);
          }}
          onSave={handleAdd}
        />
      )}
    </Shell>
  );
}
