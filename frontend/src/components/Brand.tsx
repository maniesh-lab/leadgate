/**
 * The leadgate mark (a gate: an arch with bars, in the accent gradient), the wordmark,
 * and the moon/sun switch between the dark and light themes.
 */
import { useId, useState } from "react";
import { applyTheme, currentTheme, type Theme } from "../lib/theme";
import { MoonIcon, SunIcon } from "./icons";

export function Logo({ size = 28 }: { size?: number }) {
  const gradient = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent)" />
          <stop offset="1" stopColor="var(--accent-2)" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="9" fill="var(--surface-2)" />
      <g fill="none" stroke={`url(#${gradient})`} strokeWidth="2.2" strokeLinecap="round">
        <path d="M9 24V15a7 7 0 0 1 14 0v9" />
        <path d="M13.7 24v-8.5M18.3 24v-8.5" />
      </g>
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <Logo />
      <span className="text-[15px] font-semibold tracking-tight">leadgate</span>
    </span>
  );
}

/** A pill switch: the knob sits on the moon (dark) or slides to the sun (light). */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const light = theme === "light";

  function toggle() {
    const next: Theme = light ? "dark" : "light";
    applyTheme(next);
    setTheme(next);
  }

  return (
    <button
      type="button"
      role="switch"
      aria-checked={light}
      aria-label="Light mode"
      title={light ? "Switch to dark mode" : "Switch to light mode"}
      onClick={toggle}
      className="relative flex h-8 w-[60px] shrink-0 items-center rounded-full border border-line bg-surface-2 px-1"
    >
      <span
        className={`flex size-6 items-center justify-center rounded-full bg-button text-button-text shadow transition-transform duration-200 ${light ? "translate-x-[26px]" : ""}`}
      >
        {light ? <SunIcon className="size-3.5" /> : <MoonIcon className="size-3.5" />}
      </span>
      <span
        className={`absolute size-1 rounded-full bg-faint ${light ? "left-3.5" : "right-3.5"}`}
      />
    </button>
  );
}
