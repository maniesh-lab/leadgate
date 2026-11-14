/**
 * Links to outside sites, built from data leadgate collected (a lead's website, the social
 * profiles its site links to). Only http(s) gets a link, so a stored `javascript:` or
 * `data:` address is never clickable, whatever React would do with it. A website without
 * a scheme ("www.smile.com", common in CSV files and OpenStreetMap) gets https.
 */
export function webLink(raw: string | null | undefined): string | null {
  const text = raw?.trim();
  if (!text) return null;
  // "host:8080" has a colon but no scheme.
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(text) && !/^[^:/]+:\d+(\/|$)/.test(text);
  try {
    const url = new URL(hasScheme ? text : `https://${text}`);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null; // not an address at all
  }
}
