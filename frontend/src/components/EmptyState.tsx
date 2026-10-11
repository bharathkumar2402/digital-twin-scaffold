export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="empty-state">
      <h4>{title}</h4>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
