import { createFileRoute, Link } from "@tanstack/react-router";
import type { MouseEvent as ReactMouseEvent } from "react";
import {
  Activity,
  ArrowRight,
  BadgeCheck,
  Boxes,
  CalendarCheck,
  CheckCircle2,
  Clock,
  Eye,
  HeartPulse,
  MessageSquare,
  Radio,
  Search,
  ShieldCheck,
  Snowflake,
  TrendingUp,
  UserCheck,
  Wifi,
  Wrench,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Brand } from "@/components/ops-shell";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Agentic OpsResolve by TCS | Intelligent Service Resolution" },
      {
        name: "description",
        content:
          "Agentic OpsResolve is an intelligent service resolution platform from TCS. It detects equipment problems early, resolves what it safely can remotely, and coordinates field visits when a technician is needed.",
      },
      { property: "og:title", content: "Agentic OpsResolve by TCS" },
      {
        property: "og:description",
        content:
          "From \"something is wrong\" to \"it's fixed\", without the chasing. Intelligent service resolution from TCS.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Landing,
});

function scrollToSection(e: ReactMouseEvent<HTMLAnchorElement>, id: string) {
  e.preventDefault();
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function LandingHeader() {
  return (
    <header className="lp-header">
      <div className="lp-container lp-header-inner">
        <Brand />
        <nav className="lp-nav" aria-label="Primary">
          <a href="#how-it-works" onClick={(e) => scrollToSection(e, "how-it-works")}>
            How it works
          </a>
          <a href="#benefits" onClick={(e) => scrollToSection(e, "benefits")}>
            Benefits
          </a>
          <a href="#industries" onClick={(e) => scrollToSection(e, "industries")}>
            Industries
          </a>
          <a href="#contact" onClick={(e) => scrollToSection(e, "contact")}>
            Contact
          </a>
        </nav>
        <Link to="/login" className="lp-signin">
          <Button className="lp-signin-btn">
            Sign in <ArrowRight size={16} />
          </Button>
        </Link>
      </div>
    </header>
  );
}

function Hero() {
  return (
    <section className="lp-hero">
      <div className="lp-container lp-hero-inner">
        <div className="lp-hero-copy">
          <span className="lp-eyebrow">Tata Consultancy Services</span>
          <h1 className="lp-hero-headline">
            From <span className="lp-quote lp-quote-problem">&ldquo;something is wrong&rdquo;</span>{" "}
            to <span className="lp-quote lp-quote-fixed">&ldquo;it&rsquo;s fixed&rdquo;</span>, without
            the chasing.
          </h1>
          <p>
            Agentic OpsResolve is an intelligent service resolution platform from TCS. It detects
            equipment problems early, fixes what it safely can remotely, and coordinates field
            visits when a technician is needed. It keeps customers updated at every step.
          </p>
          <div className="lp-hero-actions">
            <a href="#how-it-works" onClick={(e) => scrollToSection(e, "how-it-works")}>
              <Button className="lp-primary-btn">
                Learn how it works <ArrowRight size={16} />
              </Button>
            </a>
            <Link to="/login">
              <Button variant="outline" className="lp-outline-btn">
                Sign in
              </Button>
            </Link>
          </div>
          <p className="lp-tagline">
            Faster fixes. Fewer breakdowns. Customers who stay informed.
          </p>
        </div>
        <div className="lp-hero-visual">
          <img
            src="/landing-hero.svg"
            alt="A cold-storage unit showing an amber warning connects through intelligent monitoring to a technician resolving the issue on site, with a green confirmation tick."
            width={760}
            height={480}
            loading="eager"
          />
          <div className="lp-flow-caption">From alert to resolution</div>
        </div>
      </div>
    </section>
  );
}

const steps = [
  {
    icon: Radio,
    title: "Detect",
    body: "Connected equipment sends health readings. The platform recognizes unusual behavior before it turns into a breakdown.",
  },
  {
    icon: Search,
    title: "Understand",
    body: "It gathers the equipment history, customer details, service contract and past repairs, so nobody has to look them up.",
  },
  {
    icon: Wrench,
    title: "Resolve",
    body: "It applies approved remote fixes where safe, or arranges the right technician, parts and appointment.",
  },
  {
    icon: MessageSquare,
    title: "Communicate",
    body: "The customer receives clear updates and can reply, confirm a time or ask questions in one continuous conversation.",
  },
  {
    icon: CheckCircle2,
    title: "Verify and close",
    body: "The case is closed only after the fix is confirmed, with a complete service record.",
  },
] as const;

const benefits = [
  { icon: Clock, title: "Shorter downtime", body: "Problems are found and acted on earlier, often before customers notice." },
  { icon: CalendarCheck, title: "Fewer unnecessary visits", body: "Many issues are resolved remotely, which saves travel time and cost." },
  { icon: Wrench, title: "Better use of technicians", body: "The right person with the right part arrives the first time." },
  { icon: BadgeCheck, title: "Fewer missed commitments", body: "Delays and risks are flagged early, so service targets are protected." },
  { icon: HeartPulse, title: "Happier customers", body: "Proactive, transparent updates replace follow-up calls." },
  { icon: Eye, title: "Full visibility", body: "Every case has a clear record for audits, reviews and continuous improvement." },
] as const;

const industries = [
  { icon: Snowflake, title: "Cold chain & food retail", body: "Protecting refrigerated stock from temperature failures." },
  { icon: HeartPulse, title: "Healthcare & diagnostics", body: "Keeping imaging and lab equipment available for patients." },
  { icon: Wifi, title: "Telecom & broadband", body: "Getting customers back online with less waiting." },
] as const;

function Landing() {
  return (
    <div className="lp-root">
      <LandingHeader />
      <main>
        <Hero />

        <section className="lp-section lp-section-muted">
          <div className="lp-container">
            <span className="lp-section-eyebrow">The problem we solve</span>
            <h2>The real cost of a failure is rarely the part itself.</h2>
            <div className="lp-prose-grid">
              <p>
                When critical equipment fails, the real cost is rarely the part itself. It is the
                lost stock in a failed cold-storage unit, the clinic that cannot run scans, or the
                household waiting days for a broadband fix. Service teams juggle alerts,
                spreadsheets, phone calls and warranty checks across many systems. Customers are
                left asking the same question: "What is happening with my issue?"
              </p>
              <p>
                Delays build up at every handover. A fault is noticed late. The right technician is
                hard to find. The part is not in stock. The customer is never told the visit has
                been pushed back. Each gap adds cost, frustration and risk to the business.
              </p>
            </div>
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-container">
            <span className="lp-section-eyebrow">What it does</span>
            <h2>A digital service coordinator that works around the clock.</h2>
            <div className="lp-prose-grid">
              <p>
                Agentic OpsResolve acts as a digital service coordinator that works around the
                clock. It watches equipment health signals, spots early warning signs and opens a
                service case automatically. It then works through the case step by step: it
                understands the issue, checks warranty and service entitlements, and recommends the
                best next action.
              </p>
              <p>
                Where a problem can be fixed safely from a distance, it does so. Where a visit is
                needed, it finds a qualified technician, checks that the right part is available,
                and books a time that works for the customer. Every step is recorded, so teams
                always know what was done and why.
              </p>
            </div>
          </div>
        </section>

        <section id="how-it-works" className="lp-section lp-section-dark">
          <div className="lp-container">
            <span className="lp-section-eyebrow lp-eyebrow-light">How it works</span>
            <h2 className="lp-h2-light">Five steps, from first signal to confirmed fix.</h2>
            <div className="lp-steps">
              {steps.map(({ icon: Icon, title, body }, i) => (
                <div key={title} className="lp-step">
                  <div className="lp-step-top">
                    <span className="lp-step-num">{String(i + 1).padStart(2, "0")}</span>
                    <span className="lp-step-icon">
                      <Icon size={18} />
                    </span>
                  </div>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section lp-section-muted">
          <div className="lp-container lp-split">
            <div>
              <span className="lp-section-eyebrow">Keeping customers informed</span>
              <h2>Customers should not have to call to find out what is going on.</h2>
              <p>
                Agentic OpsResolve sends timely updates on the channel each customer prefers, such
                as WhatsApp, SMS, email or RCS messaging. Messages cover appointment options,
                technician arrival times, delays and completion confirmation. Customers can reply
                directly in the same conversation to confirm, reschedule or ask for help.
              </p>
              <p className="lp-note">
                Customers only receive service-related messages about their own equipment and open
                requests, after they have shared their contact details and agreed to receive service
                updates. They can opt out at any time by replying <strong>STOP</strong>.
              </p>
            </div>
            <div className="lp-image-frame">
              <img
                src="/landing-customer.svg"
                alt="A small-business owner smiles at a smartphone beside a chat window showing a technician-on-the-way update, a choice of two appointment times, and a green-tick service-complete confirmation."
                width={520}
                height={440}
                loading="lazy"
              />
            </div>
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-container lp-split lp-split-reverse">
            <div className="lp-control-card">
              <div className="lp-control-row">
                <UserCheck size={18} /> Approval required before sensitive actions
              </div>
              <div className="lp-control-row">
                <Eye size={18} /> Full visibility into every open case
              </div>
              <div className="lp-control-row">
                <ShieldCheck size={18} /> Clear rules keep actions within policy
              </div>
              <div className="lp-control-row">
                <Activity size={18} /> Step in and override at any time
              </div>
            </div>
            <div>
              <span className="lp-section-eyebrow">People stay in control</span>
              <h2>Automation does not mean giving up oversight.</h2>
              <p>
                Sensitive actions require approval from an authorized person before they go ahead.
                Service managers can review every open case, see what the system has done, approve
                or reject recommended actions, and step in whenever they choose. Clear rules and
                safeguards keep every action within policy.
              </p>
            </div>
          </div>
        </section>

        <section id="benefits" className="lp-section lp-section-muted">
          <div className="lp-container">
            <span className="lp-section-eyebrow">Business benefits</span>
            <h2>Outcomes that protect revenue and relationships.</h2>
            <div className="lp-cards">
              {benefits.map(({ icon: Icon, title, body }) => (
                <div key={title} className="lp-card panel">
                  <span className="lp-card-icon">
                    <Icon size={20} />
                  </span>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section id="industries" className="lp-section">
          <div className="lp-container">
            <span className="lp-section-eyebrow">Where it helps</span>
            <h2>Built for businesses that depend on uptime.</h2>
            <div className="lp-cards lp-cards-3">
              {industries.map(({ icon: Icon, title, body }) => (
                <div key={title} className="lp-card panel">
                  <span className="lp-card-icon">
                    <Icon size={20} />
                  </span>
                  <h3>{title}</h3>
                  <p>{body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section lp-section-dark">
          <div className="lp-container lp-about">
            <span className="lp-section-eyebrow lp-eyebrow-light">About TCS</span>
            <h2 className="lp-h2-light">
              Decades of enterprise operations experience, applied to a practical goal.
            </h2>
            <p className="lp-about-body">
              Tata Consultancy Services (TCS) is a global IT services, consulting and business
              solutions organization that has partnered with many of the world's largest businesses
              for over five decades. Agentic OpsResolve brings TCS experience in enterprise
              operations and applied AI to a practical goal: helping service organizations resolve
              issues faster and treat customers better.
            </p>
            <div className="lp-stats">
              <div className="lp-stat">
                <TrendingUp size={18} />
                <span>Earlier detection</span>
              </div>
              <div className="lp-stat">
                <Boxes size={18} />
                <span>Right part, first time</span>
              </div>
              <div className="lp-stat">
                <ShieldCheck size={18} />
                <span>Policy-safe automation</span>
              </div>
            </div>
          </div>
        </section>

        <section id="contact" className="lp-section lp-cta">
          <div className="lp-container lp-cta-inner">
            <h2>Ready to resolve issues faster?</h2>
            <p>Sign in to the OpsResolve console, or reach out to our team.</p>
            <div className="lp-cta-actions">
              <Link to="/login">
                <Button className="lp-primary-btn">
                  Sign in to console <ArrowRight size={16} />
                </Button>
              </Link>
              <a href="mailto:agentic.opsresolve@gmail.com">
                <Button variant="outline" className="lp-outline-btn">
                  agentic.opsresolve@gmail.com
                </Button>
              </a>
            </div>
            <div className="lp-contact-meta">
              <span>21 Raveline Street, Fort, Mumbai, 400001, India</span>
            </div>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-container lp-footer-inner">
          <Brand compact />
          <nav className="lp-footer-links" aria-label="Footer">
            <a href="#contact" onClick={(e) => scrollToSection(e, "contact")}>
              Privacy Policy
            </a>
            <a href="#contact" onClick={(e) => scrollToSection(e, "contact")}>
              Terms of Use
            </a>
            <a href="#contact" onClick={(e) => scrollToSection(e, "contact")}>
              Contact
            </a>
          </nav>
          <p className="lp-copyright">
            © Tata Consultancy Services Limited. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  );
}
