import { MODEL_CARDS, MODEL_CARDS_AS_OF, PIPELINE_NOTES } from "../lib/modelCards";

export function ModelCards(): React.JSX.Element {
  return (
    <div>
      <p style={{ fontSize: 12 }}>
        Metrics as of {MODEL_CARDS_AS_OF}, from <code>docs/DATASETS.md</code>. They show how each
        model did on its own published dataset, not how it will do on this facility&rsquo;s
        equipment.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 }}>
        {MODEL_CARDS.map((card) => (
          <article key={card.id} className="stage-card" aria-label={card.title} style={{ margin: 0 }}>
            <h4>{card.title}</h4>
            <p style={{ fontSize: 13 }}>{card.task}</p>
            <p style={{ fontSize: 13 }}>
              <strong>Data:</strong> {card.dataset}
            </p>
            <ul style={{ margin: "0 0 8px 18px", padding: 0, fontSize: 13 }}>
              {card.results.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <p style={{ fontSize: 13, marginBottom: 2 }}>
              <strong>Limits:</strong>
            </p>
            <ul style={{ margin: "0 0 0 18px", padding: 0, fontSize: 13 }}>
              {card.limitations.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </article>
        ))}
      </div>
      <ul style={{ margin: "12px 0 0 18px", padding: 0, fontSize: 13 }}>
        {PIPELINE_NOTES.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}
