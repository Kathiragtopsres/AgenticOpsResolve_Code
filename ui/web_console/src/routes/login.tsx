import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Eye, EyeOff, LockKeyhole, Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import { login as cognitoLogin, isAuthenticated } from "@/services/auth";
export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [
      { title: "Sign in | OpsResolve" },
      { name: "description", content: "Sign in to the OpsResolve demo console." },
      { property: "og:title", content: "Sign in | OpsResolve" },
      { property: "og:description", content: "Sign in to the OpsResolve demo console." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Login,
});
function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (isAuthenticated()) navigate({ to: "/cases" });
  }, [navigate]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await cognitoLogin(email.trim(), password);
      navigate({ to: "/cases" });
    } catch {
      setError("Incorrect username or password.");
    } finally {
      setLoading(false);
    }
  }
  return (
    <div className="login-wrap">
      <div className="login-stack">
        <div className="login-brand">
          <div className="login-logo" aria-hidden="true">
            <span />
          </div>
          <h1>OpsResolve</h1>
          <p>Operations Control Center</p>
        </div>
        <div className="login-card">
          <div className="login-card-header">
            <span>Authentication required</span>
            <div className="login-signals" aria-hidden="true">
              <i />
              <i />
            </div>
          </div>
          <form onSubmit={submit} className="login-form">
            <div>
              <label className="login-label" htmlFor="email">
                User identifier
              </label>
              <div className="login-input-wrap">
                <Mail size={16} />
                <input
                  className="field"
                  id="email"
                  type="text"
                  autoComplete="username"
                  placeholder="Enter your username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label className="login-label" htmlFor="password">
                  Security key
                </label>
                <span className="login-reset">Protected access</span>
              </div>
              <div className="login-input-wrap">
                <LockKeyhole size={16} />
                <input
                  className="field"
                  id="password"
                  type={show ? "text" : "password"}
                  placeholder="Enter your security key"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={4}
                  required
                />
                <Button
                  variant="ghost"
                  size="icon"
                  type="button"
                  title={show ? "Hide password" : "Show password"}
                  className="absolute right-0 top-0"
                  onClick={() => setShow(!show)}
                >
                  {show ? <EyeOff size={16} /> : <Eye size={16} />}
                </Button>
              </div>
              {error && (
                <p role="alert" className="text-xs text-critical mt-2">
                  {error}
                </p>
              )}
            </div>
            <Button className="login-submit" disabled={loading}>
              {loading ? (
                "Initializing..."
              ) : (
                <>
                  Initialize session <ArrowRight size={16} />
                </>
              )}
            </Button>
          </form>
        </div>
        <div className="login-confidential">TCS Agentic Service Resolution · Confidential</div>
      </div>
    </div>
  );
}
