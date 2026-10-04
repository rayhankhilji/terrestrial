import { Bomb, Crosshair, Drone, type LucideIcon, Rocket, Zap } from 'lucide-react'

export type RGBA = [number, number, number, number]

/** Weapon classes parsed from Air Force / monitor reports (live/sources/telegram.py). */
export const WEAPONS: Record<string, { label: string; short: string; color: RGBA; icon: LucideIcon }> = {
  ballistic: { label: 'Ballistic missile', short: 'Ballistic', color: [240, 72, 48, 255], icon: Rocket },
  cruise_missile: { label: 'Cruise missile', short: 'Cruise', color: [232, 98, 66, 255], icon: Rocket },
  missile: { label: 'Missile', short: 'Missile', color: [232, 98, 66, 255], icon: Rocket },
  glide_bomb: { label: 'Guided glide bombs (KAB)', short: 'KAB', color: [226, 128, 70, 255], icon: Bomb },
  jet_uav: { label: 'Jet-powered attack drone', short: 'Jet drone', color: [226, 162, 74, 255], icon: Zap },
  uav: { label: 'Attack drone (Shahed-type)', short: 'Drone', color: [228, 190, 112, 255], icon: Drone },
  unknown: { label: 'Aerial target', short: 'Target', color: [204, 199, 186, 255], icon: Crosshair },
}

export function weapon(cls: string | null | undefined) {
  return WEAPONS[cls ?? 'unknown'] ?? WEAPONS.unknown
}

export function css([r, g, b]: RGBA, a = 1): string {
  return `rgba(${r},${g},${b},${a})`
}
