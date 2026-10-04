/** Terrestrial mark: the Earth's limb crossed by a tilted orbit; the gold point is where the orbit
 * meets the horizon ("what is overhead, and where"). Works at 16 px and on any dark surface. */
export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="tl-earth" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#2a3442" />
          <stop offset="1" stopColor="#0d1117" />
        </linearGradient>
      </defs>
      <circle cx="16" cy="16" r="10.5" fill="url(#tl-earth)" stroke="#e9eef5" strokeOpacity="0.9" strokeWidth="1.4" />
      <path d="M8.2 19.6 C 12 17.4, 20 17.4, 23.8 19.6" fill="none" stroke="#e9eef5" strokeOpacity="0.35" strokeWidth="1" />
      <ellipse cx="16" cy="16" rx="14.2" ry="5.2" transform="rotate(-24 16 16)" fill="none" stroke="#f5c518" strokeWidth="1.4" />
      <circle cx="27.1" cy="10.9" r="2.3" fill="#f5c518" />
    </svg>
  )
}
