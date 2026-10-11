export function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="results-section" aria-label={title}>
      <h3 style={{ marginBottom: 10 }}>{title}</h3>
      {children}
    </section>
  );
}
