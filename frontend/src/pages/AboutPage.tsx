import { AppShell } from "../components/AppShell";
import { ModelCards } from "../components/ModelCards";

export function AboutPage(): React.JSX.Element {
  return (
    <AppShell>
      <div className="page">
        <div className="page-head">
          <h1>About the models</h1>
          <p>Which real datasets the risk, remaining-life and vibration models learned from, and their limits.</p>
        </div>
        <ModelCards />
      </div>
    </AppShell>
  );
}
