/**
 * Procedural low-poly meshes for deck.gl SimpleMeshLayer, in metres, nose/bow toward +Y.
 * Built from extruded convex footprints with flat normals, so no external 3D assets are needed.
 */

type Vec3 = [number, number, number]

class MeshBuilder {
  positions: number[] = []
  normals: number[] = []

  tri(a: Vec3, b: Vec3, c: Vec3) {
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
    const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]]
    const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    const len = Math.hypot(n[0], n[1], n[2]) || 1
    for (const p of [a, b, c]) {
      this.positions.push(...p)
      this.normals.push(n[0] / len, n[1] / len, n[2] / len)
    }
  }

  quad(a: Vec3, b: Vec3, c: Vec3, d: Vec3) {
    this.tri(a, b, c)
    this.tri(a, c, d)
  }

  /** Extrude a convex counter-clockwise footprint [x, y][] between heights z0 and z1. */
  prism(footprint: [number, number][], z0: number, z1: number) {
    const n = footprint.length
    const top = footprint.map(([x, y]) => [x, y, z1] as Vec3)
    const bottom = footprint.map(([x, y]) => [x, y, z0] as Vec3)
    for (let i = 1; i < n - 1; i++) {
      this.tri(top[0], top[i], top[i + 1])
      this.tri(bottom[0], bottom[i + 1], bottom[i])
    }
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n
      this.quad(bottom[i], bottom[j], top[j], top[i])
    }
  }

  box(cx: number, cy: number, cz: number, sx: number, sy: number, sz: number) {
    const x0 = cx - sx / 2
    const x1 = cx + sx / 2
    const y0 = cy - sy / 2
    const y1 = cy + sy / 2
    this.prism(
      [
        [x0, y0],
        [x1, y0],
        [x1, y1],
        [x0, y1],
      ],
      cz - sz / 2,
      cz + sz / 2,
    )
  }

  build() {
    return {
      positions: { value: new Float32Array(this.positions), size: 3 },
      normals: { value: new Float32Array(this.normals), size: 3 },
    }
  }
}

/** Swept-wing airliner/ISR silhouette, ~40 m long. */
export function aircraftMesh() {
  const m = new MeshBuilder()
  // fuselage with a tapered nose and tail cone
  m.prism(
    [
      [-2, -18],
      [2, -18],
      [2.2, 14],
      [0.8, 20],
      [-0.8, 20],
      [-2.2, 14],
    ],
    -2,
    2,
  )
  // swept main wing, one convex panel per side
  m.prism(
    [
      [2, -3],
      [19, -9],
      [19, -6],
      [2, 2],
    ],
    -0.6,
    0.4,
  )
  m.prism(
    [
      [-2, 2],
      [-19, -6],
      [-19, -9],
      [-2, -3],
    ],
    -0.6,
    0.4,
  )
  // tailplane
  m.prism(
    [
      [-7, -19],
      [-1, -14],
      [1, -14],
      [7, -19],
      [7, -21],
      [-7, -21],
    ].reverse() as [number, number][],
    0.2,
    0.9,
  )
  // vertical fin
  m.prism(
    [
      [-0.5, -21],
      [0.5, -21],
      [0.5, -14],
      [-0.5, -14],
    ],
    2,
    9,
  )
  return m.build()
}

/** Merchant hull with a pointed bow and an aft superstructure, ~180 m long. */
export function shipMesh() {
  const m = new MeshBuilder()
  m.prism(
    [
      [-14, -90],
      [14, -90],
      [15, 50],
      [9, 78],
      [0, 92],
      [-9, 78],
      [-15, 50],
    ],
    0,
    12,
  )
  m.box(0, -68, 22, 24, 18, 20) // accommodation block
  m.box(0, -74, 36, 8, 6, 10) // funnel
  return m.build()
}
