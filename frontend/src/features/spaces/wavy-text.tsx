import type { CSSProperties } from "react";
import { cx } from "@/utils/cx";

const AMPLITUDE_PX = 3;

export interface WavyTextProps {
  text: string;
  className?: string;
}

/**
 * Upright lettering that, inside a `group` (e.g. a Button with `className="group"`),
 * turns italic and rides a sine-wave offset with motion on hover. Reduced motion keeps it still.
 */
export function WavyText({ text, className }: WavyTextProps) {
  return (
    <span className={cx("inline-flex", className)}>
      <span className="sr-only">{text}</span>
      <span aria-hidden className="inline-flex">
        {Array.from(text).map((char, i) => (
          <span
            key={i}
            className={cx(
              "inline-block whitespace-pre group-hover:italic group-hover:[translate:0_var(--wave-offset)] group-hover:animate-[vibe-wave_0.9s_ease-in-out_infinite] transition-[translate] duration-200 ease-out motion-reduce:animate-none motion-reduce:transition-none",
            )}
            style={
              {
                "--wave-offset": `${(Math.sin(i * 1.2) * AMPLITUDE_PX).toFixed(2)}px`,
                animationDelay: `${i * 90}ms`,
              } as CSSProperties
            }
          >
            {char}
          </span>
        ))}
      </span>
    </span>
  );
}
