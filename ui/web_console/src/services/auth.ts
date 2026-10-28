// =============================================================================
// OpsResolve Ops Console — Authentication (Cognito)
// -----------------------------------------------------------------------------
// All Cognito setup, login, token retrieval and logout live here, separate
// from the API service layer.
// =============================================================================

import { CognitoUserPool, CognitoUser, AuthenticationDetails } from "amazon-cognito-identity-js";

const COGNITO_USER_POOL_ID = "<YOUR_USER_POOL_ID>";
const COGNITO_CLIENT_ID = "<YOUR_CLIENT_ID>";

// localStorage key that gates access to every page (see route protection).
const SESSION_KEY = "ops-session";

const userPool = new CognitoUserPool({
  UserPoolId: COGNITO_USER_POOL_ID,
  ClientId: COGNITO_CLIENT_ID,
});

/**
 * Sign in with username + password. On success the username is stored in
 * localStorage under SESSION_KEY so the app's route guard allows access.
 * Rejects with the Cognito error (err.code === 'NotAuthorizedException' for a
 * wrong password) so the login screen can show a message.
 */
export function login(username: string, password: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: username, Pool: userPool });
    const details = new AuthenticationDetails({
      Username: username,
      Password: password,
    });
    user.setAuthenticationFlowType("USER_PASSWORD_AUTH");
    user.authenticateUser(details, {
      onSuccess: () => {
        localStorage.setItem(SESSION_KEY, username);
        resolve();
      },
      onFailure: (err) => reject(err),
      newPasswordRequired: () => reject(new Error("Password change required")),
    });
  });
}

/**
 * Returns a valid Cognito ID token. getSession() refreshes with the refresh
 * token automatically when the ID token has expired, and fails only when the
 * refresh token is gone/expired.
 * @param forceRefresh when true, forces a refresh-token exchange.
 */
export function getIdToken(forceRefresh = false): Promise<string> {
  return new Promise((resolve, reject) => {
    const user = userPool.getCurrentUser();
    if (!user) return reject(new Error("Not signed in"));
    user.getSession((err: Error | null, session: any) => {
      if (err || !session?.isValid()) {
        return reject(err ?? new Error("Session invalid"));
      }
      if (forceRefresh) {
        const refreshToken = session.getRefreshToken();
        user.refreshSession(refreshToken, (refreshErr: Error | null, newSession: any) => {
          if (refreshErr || !newSession?.isValid()) {
            return reject(refreshErr ?? new Error("Session invalid"));
          }
          resolve(newSession.getIdToken().getJwtToken());
        });
        return;
      }
      resolve(session.getIdToken().getJwtToken());
    });
  });
}

/** Discard the Cognito tokens and clear the local session marker. */
export function logout(): void {
  userPool.getCurrentUser()?.signOut();
  localStorage.removeItem(SESSION_KEY);
}

/** True only when the user details are present in local storage. */
export function isAuthenticated(): boolean {
  return !!localStorage.getItem(SESSION_KEY);
}

export interface CurrentUser {
  /** cognito:username (or the username typed at login as a fallback). */
  username: string;
  /** Display name derived from token claims, falls back to the username. */
  name: string;
  email: string | null;
  /** First Cognito group, if the token carries any. */
  role: string | null;
}

/** Decode a JWT payload without verifying it (claims are read-only display). */
function decodeJwt(token: string): Record<string, any> | null {
  try {
    const payload = token.split(".")[1];
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

/**
 * Turn a raw claim like "john_doe" into "John Doe": split on underscores,
 * dots, hyphens and spaces, then capitalise the first letter of each word.
 */
function toDisplayName(raw: string): string {
  return raw
    .split(/[\s._-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}


export function getCurrentUser(): CurrentUser | null {
  const stored = localStorage.getItem(SESSION_KEY);
  const user = userPool.getCurrentUser();
  if (!user && !stored) return null;

  let claims: Record<string, any> | null = null;
  try {
    user?.getSignInUserSession?.();
  } catch {
    /* no cached session */
  }
  const session = user?.getSignInUserSession?.();
  const idToken = session?.getIdToken?.()?.getJwtToken?.();
  if (idToken) claims = decodeJwt(idToken);

  const groups = claims?.["cognito:groups"];
  const username = claims?.["cognito:username"] ?? claims?.["username"] ?? stored ?? "";
  const rawName = claims?.["name"] ?? claims?.["given_name"] ?? claims?.["email"] ?? username;
  return {
    username,
    name: toDisplayName(rawName),
    email: claims?.["email"] ?? null,
    role: Array.isArray(groups) && groups.length ? groups[0] : (claims?.["custom:role"] ?? null),
  };
}

/** Two-letter initials for the avatar, derived from the display name. */
export function getUserInitials(name: string): string {
  const parts = name.trim().split(/[\s@._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[1]![0]!).toUpperCase();
}
