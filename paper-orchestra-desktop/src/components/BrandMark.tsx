/**
 * The PaperOrchestra mark ("the conductor's pilcrow"), single colour via
 * currentColor. Geometry is brand/mark.svg.
 *
 * The viewBox starts at 128 (not mark.svg's 144) so that, at multiples of
 * 22 px, the mark's stems, bar and baseline (all on a 64-unit grid) land on
 * whole pixels: 22 px = 2 px stems at 1x, 44 px = 2 px at 1x / 4 px at 2x.
 * The mark sits 16 units (0.5 px at 22 px) low in the box; badges centre it.
 */
interface BrandMarkProps {
  /** Rendered width and height in px. Prefer 22 or 44 for crisp edges. */
  size?: number;
  className?: string;
  /** Accessible name; omit for a decorative mark (aria-hidden). */
  title?: string;
}

export function BrandMark({ size = 22, className, title }: BrandMarkProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="128 128 704 704"
      width={size}
      height={size}
      fill="currentColor"
      className={className}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      focusable="false"
    >
      {title && <title>{title}</title>}
      <path d="M448 256 H710 V320 H512 V832 H448 V640 C330 640 240 557 240 448 C240 339 330 256 448 256 Z" />
      <path
        transform="translate(672 832) rotate(4)"
        d="M36 -240 L32 -640 A32 32 0 0 0 -32 -640 L-36 -240 C-36 -170 -50 -140 -50 -50 A50 50 0 0 0 50 -50 C50 -140 36 -170 36 -240 Z"
      />
    </svg>
  );
}
