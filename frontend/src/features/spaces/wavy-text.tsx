import type { CSSProperties } from "react";
import { cx } from "@/utils/cx";

const AMPLITUDE_PX = 3;

export interface WavyTextProps {
  text: string;
  className?: string;
}

/**
 * Italic lettering offset along a sine wave. Inside a `group` (e.g. a Button with
 * `className="group"`), hovering sets the letters rippling; reduced motion keeps it still.
 */
export function WavyText({ text, className }: WavyTextProps) {
  return (
    <span className={cx("inline-flex ms-1", className)}>
      <span className="sr-only">{text}</span>
      <span aria-hidden className="inline-flex">
        {Array.from(text).map((char, i) => (
          <span
            key={i}
            className="inline-block italic group-hover:animate-[vibe-wave_0.9s_ease-in-out_infinite] motion-reduce:animate-none"
            style={
              {
                translate: `0 ${(Math.sin(i * 1.2) * AMPLITUDE_PX).toFixed(2)}px`,
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
