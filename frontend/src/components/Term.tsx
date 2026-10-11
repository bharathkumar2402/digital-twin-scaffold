import { GLOSSARY, type GlossaryTerm } from "../lib/glossary";

// A word with a dotted underline that explains itself on hover and on keyboard focus.
// Not a button on purpose: it is a note, not an action.
export function Term({
  term,
  children,
}: {
  term: GlossaryTerm;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <span className="term" tabIndex={0} role="note" data-tip={GLOSSARY[term]} aria-label={`${children}: ${GLOSSARY[term]}`}>
      {children}
    </span>
  );
}
