import { Link, Outlet } from "react-router-dom";

export default function DocsLayout() {
  return (
    <div className="app app--docs">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="docs-topbar">
        <Link className="brand brand--docs" to="/docs" aria-label="Helios documentation home">
          <span className="brand__mark" aria-hidden="true">
            H
          </span>
          <span className="brand__identity">
            <span className="brand__wordmark">Helios</span>
            <span className="brand__build">Documentation</span>
          </span>
        </Link>
        <Link className="docs-topbar__back" to="/talk">
          Back to console
        </Link>
      </header>
      <main className="docs-shell-main" id="main-content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
