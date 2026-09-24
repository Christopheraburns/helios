import {
  BrowserRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "./components/AsyncState";
import PrimaryNavigation from "./components/PrimaryNavigation";
import TopNavigation from "./components/TopNavigation";
import { HeliosApi } from "./api/client";
import { useApplicationContext } from "./hooks/useApplicationContext";
import CanvasPage from "./pages/CanvasPage";
import ModelsPage from "./pages/ModelsPage";
import OverviewPage from "./pages/OverviewPage";
import PlaceholderPage from "./pages/PlaceholderPage";
import RunDetailPage, {
  HistoricalTableProfilePage,
} from "./pages/RunDetailPage";

interface AppProps {
  client?: HeliosApi;
}

const errorTitles = {
  authentication: "Authentication required",
  authorization: "Access is not configured",
  unavailable: "Helios API unavailable",
} as const;

function ApplicationShell({ client }: AppProps) {
  const context = useApplicationContext(client);
  const location = useLocation();
  const canvasActive = location.pathname === "/canvas";
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem("helios.workspace.collapsed") === "true";
    } catch {
      return false;
    }
  });
  const errorTitle = context.errorKind
    ? errorTitles[context.errorKind]
    : "Helios could not be loaded";

  useEffect(() => {
    try {
      window.localStorage.setItem(
        "helios.workspace.collapsed",
        String(workspaceCollapsed),
      );
    } catch {
      // The shell remains usable when browser storage is unavailable.
    }
  }, [workspaceCollapsed]);

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <TopNavigation context={context} />

      <div
        className={`app__body${
          workspaceCollapsed ? " app__body--workspace-collapsed" : ""
        }`}
      >
        <PrimaryNavigation
          context={context}
          collapsed={workspaceCollapsed}
          onToggleCollapsed={() =>
            setWorkspaceCollapsed((collapsed) => !collapsed)
          }
        />
        <main
          className={`app__main${canvasActive ? " app__main--canvas" : ""}`}
          id="main-content"
        >
          {context.status === "loading" ? (
            <LoadingState />
          ) : context.status === "error" ? (
            <ErrorState
              title={errorTitle}
              message={context.errorMessage ?? "An unexpected error occurred."}
              onRetry={context.retry}
            />
          ) : (
            <Routes>
              <Route path="/" element={<OverviewPage context={context} />} />
              <Route
                path="/canvas"
                element={<CanvasPage context={context} />}
              />
              <Route
                path="/models"
                element={<ModelsPage context={context} />}
              />
              <Route
                path="/models/runs/:runId"
                element={<RunDetailPage context={context} />}
              />
              <Route
                path="/models/runs/:runId/profile/*"
                element={<HistoricalTableProfilePage context={context} />}
              />
              <Route
                path="/data-sources"
                element={
                  <PlaceholderPage
                    title="Data Sources"
                    description="Review the governed data sources available to Helios."
                  />
                }
              />
              <Route
                path="/governance"
                element={
                  <PlaceholderPage
                    title="Governance"
                    description="Review permissions, glossary context, and governance state."
                  />
                }
              />
              <Route
                path="*"
                element={
                  <EmptyState
                    title="Page not found"
                    message="The requested Helios workspace does not exist."
                  />
                }
              />
            </Routes>
          )}
        </main>
      </div>
    </div>
  );
}

export default function App(props: AppProps) {
  return (
    <BrowserRouter>
      <ApplicationShell {...props} />
    </BrowserRouter>
  );
}
