export function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="results-section" aria-label={title}>
      <h2 style={{ marginBottom: 10 }}>{title}</h2>
      {children}
    </section>
  );
}
