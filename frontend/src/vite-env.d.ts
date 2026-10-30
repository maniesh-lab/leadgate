/** The `VITE_*` keys from the repo-root `.env` that the browser code reads. */
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
