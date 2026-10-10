// What each model is, what it was trained on, how it did, and where it should not be trusted.
// Hand-copied from docs/DATASETS.md (the source of truth) as of AS_OF; update both together.
// Deliberately static: showing the numbers needs no extra endpoint, but they are not live.

export const MODEL_CARDS_AS_OF = "2026-10-10";

export interface ModelCard {
  id: string;
  title: string;
  task: string;
  dataset: string;
  results: string[];
  limitations: string[];
}

export const MODEL_CARDS: ModelCard[] = [
  {
    id: "risk",
    title: "Failure risk",
    task: "Probability that a machine fails, from one simultaneous snapshot of five sensors (air and process temperature, rotational speed, torque, tool wear).",
    dataset: "AI4I 2020 Predictive Maintenance Dataset (UCI #601, CC BY 4.0)",
    results: [
      "Held-out ROC-AUC 0.974, PR-AUC 0.859 (gradient-boosted trees).",
      "Scoring uses exactly the input form it was trained on; assets without the five sensors are not scored.",
    ],
    limitations: [
      "A published machine dataset, not data from this facility's equipment.",
      "Not validated on real equipment.",
    ],
  },
  {
    id: "rul",
    title: "Remaining useful life",
    task: "Estimated cycles until failure, from a window of 24 engine channels.",
    dataset: "NASA C-MAPSS turbofan degradation simulation, subset FD001",
    results: [
      "Test RMSE 13.1 cycles, NASA score 236 (engine-level split, never by row).",
      "Multi-condition subsets (FD002/FD004) reach only about 25 cycles RMSE and are not deployed.",
    ],
    limitations: [
      "Simulated turbofan engines, one operating condition; needs exactly the 24 C-MAPSS channels.",
      "Assets with other sensors get no RUL rather than a guess.",
    ],
  },
  {
    id: "vibration",
    title: "Vibration anomaly",
    task: "Flags a bearing whose vibration departs from its own healthy baseline (alarm after 5 consecutive anomalous snapshots).",
    dataset: "NASA/IMS bearing run-to-failure dataset (three tests)",
    results: [
      "Leave-one-test-out: all 4 failing bearings flagged; 2 of 16 channels with an early false alarm; median lead about 115 h before the end of the recording.",
    ],
    limitations: [
      "Does not clearly beat a plain RMS threshold, and the comparison rests on four failure events.",
      "Lead times vary with the random seed; one lab rig at fixed speed and load.",
    ],
  },
];

export const PIPELINE_NOTES: string[] = [
  "The five agents are deterministic tools (scheduling, an OR-Tools routing solver, a NetworkX cascade); none is an LLM.",
  "Estimated downtime is a rule-of-thumb heuristic over the cascade, not a prediction.",
  "The live telemetry in the demo is simulated, calibrated from the datasets above.",
];
