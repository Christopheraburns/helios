import {
  BrowserRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { lazy, Suspense, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "./components/AsyncState";
import PrimaryNavigation from "./components/PrimaryNavigation";
import TopNavigation from "./components/TopNavigation";
import { HeliosApi } from "./api/client";
import { useApplicationContext } from "./hooks/useApplicationContext";
import ModelsPage from "./pages/ModelsPage";
import OverviewPage from "./pages/OverviewPage";
import PlaceholderPage from "./pages/PlaceholderPage";
import RunDetailPage, {
  HistoricalTableProfilePage,
} from "./pages/RunDetailPage";

interface AppProps {
  client?: HeliosApi;
}

const GlossaryPage = lazy(() => import("./pages/GlossaryPage"));
const CanvasPage = lazy(() => import("./pages/CanvasPage"));
const TalkPage = lazy(() => import("./pages/TalkPage"));
const ActivityLogsPage = lazy(() => import("./pages/ActivityLogsPage"));
const GlossaryTermPage = lazy(() =>
  import("./pages/GlossaryPage").then((module) => ({
    default: module.GlossaryTermPage,
  })),
);
const GlossaryProposalsPage = lazy(() =>
  import("./pages/GlossaryPage").then((module) => ({
    default: module.GlossaryProposalsPage,
  })),
);

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

  useEffect(() => {
    if (context.status !== "ready") return;
    const parameters = new URLSearchParams(location.search);
    void context.recordClientActivity({
      action: "navigation.view",
      path: location.pathname,
      resource_type: context.selectedModelId ? "model" : "application",
      resource_id: context.selectedModelId || undefined,
      model_id: context.selectedModelId || undefined,
    }).catch(() => undefined);
    if (location.pathname === "/canvas") {
      const lens = parameters.get("lens");
      const focus = parameters.get("focus_node_id");
      const selected = parameters.get("element_id");
      if (lens) {
        void context.recordClientActivity({
          action: "canvas.lens_change",
          resource_type: "canvas",
          resource_id: lens,
          model_id: context.selectedModelId || undefined,
        }).catch(() => undefined);
      }
      if (focus) {
        void context.recordClientActivity({
          action: "canvas.node_focus",
          resource_type: "node",
          resource_id: focus,
          model_id: context.selectedModelId || undefined,
        }).catch(() => undefined);
      }
      if (selected) {
        void context.recordClientActivity({
          action: "canvas.node_select",
          resource_type: "node",
          resource_id: selected,
          model_id: context.selectedModelId || undefined,
        }).catch(() => undefined);
      }
    }
    if (location.pathname === "/activity") {
      void context.recordClientActivity({
        action: "activity.filter_change",
        path: location.pathname,
        resource_type: "activity",
        model_id: context.selectedModelId || undefined,
      }).catch(() => undefined);
    }
  }, [
    context.recordClientActivity,
    context.selectedModelId,
    context.status,
    location.pathname,
    location.search,
  ]);

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
          onToggleCollapsed={() => {
            const collapsed = !workspaceCollapsed;
            setWorkspaceCollapsed(collapsed);
            void context.recordClientActivity({
              action: collapsed
                ? "workspace.collapse"
                : "workspace.expand",
              resource_type: "workspace",
              model_id: context.selectedModelId || undefined,
            }).catch(() => undefined);
          }}
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
            <Suspense fallback={<LoadingState label="Loading workspace…" />}>
            <Routes>
              <Route path="/" element={<OverviewPage context={context} />} />
              <Route
                path="/canvas"
                element={<CanvasPage context={context} />}
              />
              <Route
                path="/talk"
                element={<TalkPage context={context} />}
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
                element={<GlossaryPage context={context} />}
              />
              <Route
                path="/governance/glossary/terms/:termId"
                element={<GlossaryTermPage context={context} />}
              />
              <Route
                path="/governance/proposals"
                element={<GlossaryProposalsPage context={context} />}
              />
              <Route
                path="/activity"
                element={<ActivityLogsPage context={context} />}
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
            </Suspense>
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
