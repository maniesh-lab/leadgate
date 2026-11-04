/**
 * The "Google Maps" credit Google's policies ask for wherever Places content shows outside
 * a Google map: on one line, not translated, near the content, weight 400, 12-16 px, in
 * white, #1F1F1F or #5E5E5E with enough contrast (`--google-credit`, one per theme).
 * Shows only for a lead that came from Google.
 */
export function GoogleCredit({ source }: { source: string }) {
  if (source !== "google_places") return null;
  return (
    <span
      translate="no"
      className="block text-xs font-normal tracking-normal whitespace-nowrap text-(--google-credit)"
    >
      Google Maps
    </span>
  );
}
