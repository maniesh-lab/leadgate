/** The browser entry point: mounts the app into `#root` in index.html. */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <main className="p-8 text-slate-800">leadgate</main>
  </StrictMode>,
);
