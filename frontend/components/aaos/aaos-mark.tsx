import { useId } from "react";

type AaosMarkProps = Readonly<{
  className?: string;
  /** "gradient" is the brand mark; "mono" follows currentColor for small or single-colour use. */
  variant?: "gradient" | "mono";
  title?: string;
}>;

/**
 * The AAOS mark: an "A" drawn as connected nodes — the agent linking
 * Moodle, course files, the model and Calendar. Blue to teal follows the
 * dashboard's primary and teal tokens.
 */
export function AaosMark({ className, variant = "gradient", title }: AaosMarkProps) {
  const gradientId = `aaos-mark-${useId().replace(/:/g, "")}`;
  const stroke = variant === "gradient" ? `url(#${gradientId})` : "currentColor";
  const labelled = title !== undefined;

  return (
    <svg
      viewBox="0 0 48 48"
      fill="none"
      className={className}
      role={labelled ? "img" : undefined}
      aria-hidden={labelled ? undefined : true}
      aria-label={title}
    >
      {variant === "gradient" ? (
        <defs>
          <linearGradient id={gradientId} x1="6" y1="42" x2="42" y2="6" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#1677F2" />
            <stop offset="1" stopColor="#14B8A6" />
          </linearGradient>
        </defs>
      ) : null}
      <g stroke={stroke} strokeWidth="4.2" strokeLinecap="round" strokeLinejoin="round">
        {/* legs of the A, then the inner chevron through the middle node */}
        <path d="M24 8.5 8.5 39M24 8.5 39.5 39M8.5 39 24 26l15.5 13" />
        <circle cx="24" cy="8.5" r="4.4" fill="var(--aaos-mark-node, #ffffff)" />
        <circle cx="24" cy="26" r="4.4" fill="var(--aaos-mark-node, #ffffff)" />
        <circle cx="8.5" cy="39" r="4.4" fill="var(--aaos-mark-node, #ffffff)" />
        <circle cx="39.5" cy="39" r="4.4" fill="var(--aaos-mark-node, #ffffff)" />
      </g>
    </svg>
  );
}
