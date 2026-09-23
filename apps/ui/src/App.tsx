import { BrowserRouter, Route, Routes } from "react-router-dom";

import { EmptyState, ErrorState, LoadingState } from "./components/AsyncState";
import PrimaryNavigation from "./components/PrimaryNavigation";
import TopNavigation from "./components/TopNavigation";
import { HeliosApi } from "./api/client";
import { useApplicationContext } from "./hooks/useApplicationContext";
import OverviewPage from "./pages/OverviewPage";
import PlaceholderPage from "./pages/PlaceholderPage";

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
  const errorTitle = context.errorKind
    ? errorTitles[context.errorKind]
    : "Helios could not be loaded";

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <TopNavigation context={context} />

      <div className="app__body">
        <PrimaryNavigation context={context} />
        <main className="app__main" id="main-content">
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
                element={
                  <PlaceholderPage
                    title="Canvas"
                    description="Explore the authorized semantic model visually."
                  />
                }
              />
              <Route
                path="/models"
                element={
                  <PlaceholderPage
                    title="Models"
                    description="Manage semantic model definitions and versions."
                  />
                }
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
