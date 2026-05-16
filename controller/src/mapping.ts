import type { Curve } from './types'

const EPS = 1e-6

function shape(norm: number, curve: Curve): number {
  const n = Math.min(1, Math.max(0, norm))
  switch (curve) {
    case 'exp':
      return n * n
    case 'log':
      return Math.log(1 + 9 * n) / Math.log(10)
    case 'linear':
    default:
      return n
  }
}

export function normToValue(
  norm: number,
  min: number,
  max: number,
  curve: Curve = 'linear',
  paramType: 'float' | 'int' = 'float',
): number {
  const t = shape(norm, curve)
  const v = min + (max - min) * t
  if (paramType === 'int') return Math.round(v)
  return Number(v.toFixed(4))
}

export { EPS }
