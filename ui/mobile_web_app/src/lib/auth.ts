import { Amplify } from "aws-amplify";
import {
  signIn as amplifySignIn,
  signOut as amplifySignOut,
  fetchAuthSession,
} from "aws-amplify/auth";

// Cognito configuration for the OpsResolve Field technician app.
export const COGNITO_CONFIG = {
  UserPoolId: "<YOUR_USER_POOL_ID>",
  ClientId: "<YOUR_CLIENT_ID>",
};

// Configure Amplify once at module load.
Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: COGNITO_CONFIG.UserPoolId,
      userPoolClientId: COGNITO_CONFIG.ClientId,
    },
  },
});

// localStorage key where the authenticated technician's details are persisted.
export const AUTH_STORAGE_KEY = "opsresolve.field.auth";

// Shape of the user details we keep in localStorage after a successful login.
export type AuthUser = {
  // Cognito custom:id claim -> used as technicianId across all API calls.
  technicianId: string;
  username: string;
  email?: string;
  name?: string;
};

// Persist the authenticated user to localStorage.
export function saveAuthUser(user: AuthUser): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(user));
}

// Read the authenticated user from localStorage (null if not signed in).
export function getAuthUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthUser;
    if (!parsed || !parsed.technicianId) return null;
    return parsed;
  } catch {
    return null;
  }
}

// Convenience: the technicianId (custom:id) for query params / payloads.
export function getTechnicianId(): string | null {
  return getAuthUser()?.technicianId ?? null;
}

// Fetch the current Cognito id token (freshly resolved by Amplify, auto-refreshed).
export async function getIdToken(): Promise<string | null> {
  try {
    const session = await fetchAuthSession();
    return session.tokens?.idToken?.toString() ?? null;
  } catch {
    return null;
  }
}

// Fetch the current Cognito access token.
export async function getAccessToken(): Promise<string | null> {
  try {
    const session = await fetchAuthSession();
    return session.tokens?.accessToken?.toString() ?? null;
  } catch {
    return null;
  }
}

// Clear the stored session (localStorage + Amplify/Cognito).
export async function clearAuthUser(): Promise<void> {
  if (typeof window !== "undefined") {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  }
  try {
    await amplifySignOut();
  } catch {
    
  }
}

// Authenticate against Cognito with username + password via Amplify.
// On success, resolves with the AuthUser (also persisted to localStorage).
export async function signIn(username: string, password: string): Promise<AuthUser> {
  // Amplify throws if a user is already signed in; clear any stale session first.
  try {
    await amplifySignOut();
  } catch {
    // ignore
  }

  const result = await amplifySignIn({ username, password });
  if (!result.isSignedIn) {
    // e.g. a challenge such as NEW_PASSWORD_REQUIRED / MFA.
    throw new Error(
      `Additional sign-in step required: ${result.nextStep?.signInStep ?? "UNKNOWN"}.`,
    );
  }

  // Read the id token claims to derive the technicianId (custom:id) and profile.
  const session = await fetchAuthSession();
  const claims = session.tokens?.idToken?.payload ?? {};
  const technicianId = (claims["custom:id"] as string) || "";
  const user: AuthUser = {
    technicianId,
    username: (claims["cognito:username"] as string) || username,
    email: (claims["email"] as string) || undefined,
    name: (claims["name"] as string) || undefined,
  };
  saveAuthUser(user);
  return user;
}
