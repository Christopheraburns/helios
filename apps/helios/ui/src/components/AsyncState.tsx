import { ReactNode } from "react";

export function LoadingState({ label = "Loading Helios" }: { label?: string }) {
  return (
    <div className="state-panel state-panel--loading" role="status">
      <span className="spinner" aria-hidden="true" />
      <div>
        <h1>{label}</h1>
        <p>Connecting to your governed semantic workspace.</p>
      </div>
    </div>
  );
}

interface ErrorStateProps {
  title: string;
  message: string;
  onRetry?: () => void;
}

export function ErrorState({ title, message, onRetry }: ErrorStateProps) {
  return (
    <div className="state-panel state-panel--error" role="alert">
      <span className="state-panel__icon" aria-hidden="true">
        !
      </span>
      <div>
        <h1>{title}</h1>
        <p>{message}</p>
        {onRetry ? (
          <button className="button button--primary" type="button" onClick={onRetry}>
            Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}

interface EmptyStateProps {
  title: string;
  message: string;
  icon?: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
}

export function EmptyState({
  title,
  message,
  icon,
  actionLabel,
  onAction,
}: EmptyStateProps) {
  return (
    <div className="empty-state">
      {icon ? (
        <span className="empty-state__icon" aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <h2>{title}</h2>
      <p>{message}</p>
      {actionLabel && onAction ? (
        <button
          className="button button--primary"
          type="button"
          onClick={onAction}
        >
          {actionLabel}
        </button>
      ) : null}
    </div>
  );
}
