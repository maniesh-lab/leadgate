/**
 * Who is logged in. Holds the token, loads the user behind it (`GET /api/auth/me`), and
 * offers `login()` / `logout()`, plus `swapToken()` for the fresh token a password change
 * (or turning two-factor on or off) hands back. Every page reads it through `useAuth()`.
 *
 * With two-factor login on, `login()` doesn't log in yet: it returns the 5-minute token
 * the API gave for the second step, and `completeTwoFactor(token, code)` finishes.
 *
 * A token is only good for JWT_EXPIRE_MINUTES (no refresh tokens), so when any API call
 * answers 401 the client calls `logout()` and the protected routes send the user to
 * the login page.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "../api/client";
import type { LoginAnswer, Me, Token } from "../api/types";
import { getToken, setToken } from "./token";

type AuthValue = {
  user: Me | null;
  /** True while the user behind a saved token is being loaded. */
  loading: boolean;
  /** Set when the API couldn't be asked who the user is (e.g. it's down). */
  error: Error | null;
  /** Logs in, or returns the token for the two-factor step when a code is needed. */
  login: (email: string, password: string) => Promise<{ twoFactorToken: string } | null>;
  completeTwoFactor: (twoFactorToken: string, code: string) => Promise<void>;
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
    queryFn: () => api.get<Me>("/api/auth/me"),
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

  const applyToken = useCallback((next: string) => {
    setToken(next);
    setTokenState(next);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      // OAuth2's password form calls the email field "username".
      const answer = await api.postForm<LoginAnswer>("/api/auth/login", {
        username: email,
        password,
      });
      if (answer.two_factor_required && answer.two_factor_token) {
        return { twoFactorToken: answer.two_factor_token };
      }
      if (answer.access_token) applyToken(answer.access_token);
      return null;
    },
    [applyToken],
  );

  const completeTwoFactor = useCallback(
    async (twoFactorToken: string, code: string) => {
      const answer = await api.post<Token>("/api/auth/login/two-factor", {
        two_factor_token: twoFactorToken,
        code,
      });
      applyToken(answer.access_token);
    },
    [applyToken],
  );

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
    completeTwoFactor,
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
