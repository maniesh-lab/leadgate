/** The `VITE_*` keys from the repo-root `.env` that the browser code reads. */
interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_DEMO_EMAIL?: string;
  readonly VITE_DEMO_PASSWORD?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
