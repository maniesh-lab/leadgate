// Apply the saved theme before the page draws (dark unless "light" was chosen). A file,
// not an inline script, so the content security policy can allow only our own scripts.
try {
  if (localStorage.getItem("leadgate.theme") === "light") {
    document.documentElement.dataset.theme = "light";
  }
} catch {}
