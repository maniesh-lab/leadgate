/**
 * Where the login token lives: localStorage, so a page reload keeps you logged in.
 * The tradeoff (any script on the page could read it) is accepted for this project
 * (PROGRESS Decision #11). The API client reads it; the auth provider writes it.
 */
const KEY = "leadgate.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null; // storage blocked (e.g. some private windows): act as logged out
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(KEY, token);
    else localStorage.removeItem(KEY);
  } catch {
    // storage blocked: the login only lasts until the page reloads
  }
}
