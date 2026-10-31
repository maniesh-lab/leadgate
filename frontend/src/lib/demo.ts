/**
 * The public demo's reviewer login, shown on the login page. Both values are set at build
 * time (VITE_DEMO_EMAIL / VITE_DEMO_PASSWORD); without them there's no demo login. The
 * password is public on purpose: the API never lets this account change it.
 */
export type DemoLogin = { email: string; password: string };

export function demoLogin(env: ImportMetaEnv = import.meta.env): DemoLogin | null {
  const email = env.VITE_DEMO_EMAIL?.trim();
  const password = env.VITE_DEMO_PASSWORD;
  return email && password ? { email, password } : null;
}
