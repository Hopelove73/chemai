/* d3-force-3d 无官方类型；这里只声明星图用到的三个定位力。
 * 返回 any 以允许 .strength() 链式调用（真实 API 与 d3-force 同形）。 */
declare module 'd3-force-3d' {
  export function forceX(x?: unknown): any
  export function forceY(y?: unknown): any
  export function forceZ(z?: unknown): any
}
