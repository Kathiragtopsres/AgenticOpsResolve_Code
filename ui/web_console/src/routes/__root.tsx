import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Outlet, Link, createRootRouteWithContext, useRouter, useRouterState, HeadContent } from '@tanstack/react-router';
import { OpsShell } from '@/components/ops-shell';

// Routes that render their own full-page layout (no console shell layout included).
const BARE_ROUTES = ['/', '/login'];

function NotFoundComponent() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-4xl font-bold text-sidebar">Page not found</h1>
        <Link to="/cases" className="text-primary mt-4 inline-block">Back to Case Board</Link>
      </div>
    </div>
  );
}

function ErrorComponent({ reset }: { error: Error; reset: () => void }) {
  const router = useRouter();
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-xl font-bold">This page didn’t load</h1>
        <button className="text-primary mt-4" onClick={() => { router.invalidate(); reset(); }}>Try again</button>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: 'utf-8' },
      { name: 'viewport', content: 'width=device-width, initial-scale=1' },
    ],
    links: [
      { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
      { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossOrigin: 'anonymous' },
      { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap' },
    ],
  }),
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  // Bare routes (landing/login) render standalone; everything else gets the console shell.
  const isBare = BARE_ROUTES.includes(pathname);
  return (
    <QueryClientProvider client={queryClient}>
      <HeadContent />
      {isBare ? (
        <Outlet />
      ) : (
        <OpsShell>
          <Outlet />
        </OpsShell>
      )}
    </QueryClientProvider>
  );
}
