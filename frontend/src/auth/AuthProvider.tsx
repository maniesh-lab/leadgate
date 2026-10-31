/**
 * Who is logged in. Holds the token, loads the user behind it (`GET /api/auth/me`), and
 * offers `login()` / `logout()`, plus `swapToken()` for the fresh token a password change
 * hands back. Every page reads it through `useAuth()`.
 *
 * A token is only good for JWT_EXPIRE_MINUTES (no refresh tokens), so when any API call
 * answers 401 the client calls `logout()` and the protected routes send the user to
 * the login page.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "../api/client";
import type { Token, User } from "../api/types";
import { getToken, setToken } from "./token";

type AuthValue = {
  user: User | null;
  /** True while the user behind a saved token is being loaded. */
  loading: boolean;
  /** Set when the API couldn't be asked who the user is (e.g. it's down). */
  error: Error | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  /** Swap in a new token for the same user (after a password change ends the old one). */
  swapToken: (token: string) => void;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [token, setTokenState] = useState(getToken);

  const me = useQuery({
    queryKey: ["me", token],
    queryFn: () => api.get<User>("/api/auth/me"),
    enabled: token !== null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY, // the user doesn't change while the token lives
  });

  const logout = useCallback(() => {
    setToken(null);
    setTokenState(null);
    queryClient.clear(); // the next user must not see this user's cached answers
  }, [queryClient]);

  useEffect(() => setUnauthorizedHandler(logout), [logout]);

  const login = useCallback(async (email: string, password: string) => {
    // OAuth2's password form calls the email field "username".
    const answer = await api.postForm<Token>("/api/auth/login", { username: email, password });
    setToken(answer.access_token);
    setTokenState(answer.access_token);
  }, []);

  const swapToken = useCallback(
    (next: string) => {
      // Same user: seed the cache, so the page doesn't blink through "Loading…".
      queryClient.setQueryData(["me", next], me.data);
      setToken(next);
      setTokenState(next);
    },
    [queryClient, me.data],
  );

  const value: AuthValue = {
    user: token ? (me.data ?? null) : null,
    loading: token !== null && me.isPending,
    error: token ? me.error : null,
    login,
    logout,
    swapToken,
  };
  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth() needs an <AuthProvider> around it");
  return value;
}
