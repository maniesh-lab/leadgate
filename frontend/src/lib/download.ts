/** Saves a Blob as a file: a temporary link with `download`, clicked, then let go. */
export function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  // Let go a moment later: some browsers still read the link right after the click.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
