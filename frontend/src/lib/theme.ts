/**
 * Dark or light. Dark is the default; the choice is saved in localStorage and applied as
 * `<html data-theme="...">`, which switches every colour token in index.css.
 * `public/theme.js` (loaded first by index.html) applies the saved choice before the page
 * draws, so it never flashes the wrong theme.
 */
export type Theme = "dark" | "light";

const KEY = "leadgate.theme";

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // storage blocked: the theme lasts until the page reloads
  }
}
