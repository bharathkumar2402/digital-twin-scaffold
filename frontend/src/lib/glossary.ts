// Plain explanations for the model jargon shown in headers and panels.
export const GLOSSARY = {
  risk: "How likely this asset is to fail soon, from 0 (safe) to 100 (about to fail). 67 and above is high.",
  rul: "Remaining useful life: how many more operating cycles the asset is expected to last. Lower is more urgent; 30 or fewer is flagged.",
  vibration:
    "Whether the asset's vibration pattern has drifted from normal for a sustained period, an early sign of wear.",
  status: "A single verdict combining risk, remaining life and vibration.",
} as const;

export type GlossaryTerm = keyof typeof GLOSSARY;
