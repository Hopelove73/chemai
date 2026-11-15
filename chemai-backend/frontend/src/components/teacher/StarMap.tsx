/* StarMap — 教师端班级知识星图（star-map-teacher / REQ-CSM-002/003）。
 *
 * react-force-graph-3d 渲染：知识点为发光星（亮度=掌握度、大小=作答量、
 * 无可信样本=灰星），active 先修边为星座连线，6 册按锚点聚为星系团。
 * 深底画布 = 教师端浅色红线的唯一显式例外（DESIGN.md R-C9）：
 * 深色令牌 --star-canvas-* 只定义在本组件作用域，不进全局 theme。
 * three/force-graph 只进 /kmap 页 chunk（页面级 code-split 由路由承担）。 */

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Maximize2, Minimize2, Pause, Play, RotateCcw } from 'lucide-react'
import * as THREE from 'three'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'
import { CSS2DObject, CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js'
import { forceX, forceY, forceZ } from 'd3-force-3d'
import { edgeKey, isGhost, matchStars, moduleAnchor, prereqClosure, starBrightness, starKind, starSize } from '../../lib/starMap'
import { DUST_LAYERS, makeDustLayer, type DustLayerSpec } from '../../lib/starSky'

const ForceGraph3D = lazy(() => import('react-force-graph-3d'))

export interface StarGraphNode {
  kp_id: string
  name: string
  module: string
  theme: string
  description: string
  class_error_rate: number | null
  class_answers: number
  equations: string[]
  reference_notes: { id: number; text: string }[]
}

export interface StarGraphEdge {
  from_kp: string
  to_kp: string
  status: string
}

/** 发光辉光贴图：径向渐变 sprite（中心实、边缘散），免 postprocessing 的可靠辉光 */
function glowTexture(): THREE.Texture {
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')!
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  /* 官方 bloom 示例的构成 = 小亮点 + 纯黑背景 + 强 bloom：辉光由 bloom 造，
   * 贴图只提供紧凑星芒（核小晕短），60% 面积透明——bloom 输入面积最小化 */
  g.addColorStop(0, 'rgba(255,255,255,1)')      // 白核
  g.addColorStop(0.22, 'rgba(255,255,255,0.95)') // 核缘
  g.addColorStop(0.25, 'rgba(255,255,255,0.5)')  // 色环（sprite color 在此显色）
  g.addColorStop(0.45, 'rgba(255,255,255,0.12)') // 晕尾
  g.addColorStop(1, 'rgba(255,255,255,0)')      // 60% 面积透明
  ctx.fillStyle = g
  ctx.fillRect(0, 0, size, size)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

/** 暗星四芒贴图（REQ-SMA-004）：小亮核 + 两条正交光带，峰值 alpha 0.5。
 * 只有 kind === 'dim' 使用；lit/gray 仍用 glowTexture，形态不变。 */
function flareTexture(): THREE.Texture {
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')!
  const c = size / 2
  /* token-lint:disable-frame */
  /* R-C9：星图深空画布专用贴图色（白核 + 白色光带，实际颜色由 sprite color 决定） */
  const core = ctx.createRadialGradient(c, c, 0, c, c, size * 0.18)
  core.addColorStop(0, 'rgba(255,255,255,1)')
  core.addColorStop(0.35, 'rgba(255,255,255,0.9)')
  core.addColorStop(0.6, 'rgba(255,255,255,0.25)')
  core.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = core
  ctx.fillRect(0, 0, size, size)
  ctx.globalCompositeOperation = 'lighter'
  /* 光带只占画布 64%：峰值 0.30 在远/中景可见，
   * 又不会像选中高亮边那样被 bloom 拉成全屏光刃 */
  const inset = size * 0.18
  const streak = (horizontal: boolean) => {
    const g = horizontal
      ? ctx.createLinearGradient(inset, c, size - inset, c)
      : ctx.createLinearGradient(c, inset, c, size - inset)
    g.addColorStop(0, 'rgba(255,255,255,0)')
    g.addColorStop(0.35, 'rgba(255,255,255,0.15)')
    g.addColorStop(0.5, 'rgba(255,255,255,0.30)')
    g.addColorStop(0.65, 'rgba(255,255,255,0.15)')
    g.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = g
    if (horizontal) ctx.fillRect(inset, c - 1.25, size - inset * 2, 2.5)
    else ctx.fillRect(c - 1.25, inset, 2.5, size - inset * 2)
  }
  /* token-lint:enable-frame */
  streak(true)
  streak(false)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

/* ── 深空氛围层（star-map-atmosphere）──────────────────────────────
 * 星尘与天空球都是纯装饰：不参与 force 引擎、不承载数据语义。
 * 闪烁全在着色器里（per-point 相位），每帧 CPU 只更新一个 uTime。 */

const DUST_VERTEX_SHADER = `
attribute float aPhase;
attribute float aSpeed;
attribute vec3 aColor;
uniform float uTime;
uniform float uSize;
uniform float uScale;
varying vec3 vColor;
varying float vAlpha;
void main() {
  vColor = aColor;
  vAlpha = 0.70 + 0.30 * (0.5 + 0.5 * sin(uTime * aSpeed + aPhase));
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = uSize * (uScale / -mv.z);
  gl_Position = projectionMatrix * mv;
}`

const DUST_FRAGMENT_SHADER = `
uniform float uOpacity;
varying vec3 vColor;
varying float vAlpha;
void main() {
  float d = length(gl_PointCoord - vec2(0.5));
  if (d > 0.5) discard;
  float soft = 1.0 - smoothstep(0.1, 0.5, d);
  gl_FragColor = vec4(vColor, vAlpha * soft * uOpacity);
}`

const SKY_VERTEX_SHADER = `
varying vec3 vDir;
void main() {
  vDir = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`

const SKY_FRAGMENT_SHADER = `
uniform float uTime;
varying vec3 vDir;
float hash31(vec3 p) {
  p = fract(p * 0.1031);
  p += dot(p, p.yzx + 33.33);
  return fract((p.x + p.y) * p.z);
}
vec3 hash33(vec3 p) {
  vec3 q = vec3(
    dot(p, vec3(127.1, 311.7, 74.7)),
    dot(p, vec3(269.5, 183.3, 246.1)),
    dot(p, vec3(113.5, 271.9, 124.6))
  );
  return fract(sin(q) * 43758.5453123);
}
float vnoise(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float n000 = hash31(i + vec3(0.0, 0.0, 0.0));
  float n100 = hash31(i + vec3(1.0, 0.0, 0.0));
  float n010 = hash31(i + vec3(0.0, 1.0, 0.0));
  float n110 = hash31(i + vec3(1.0, 1.0, 0.0));
  float n001 = hash31(i + vec3(0.0, 0.0, 1.0));
  float n101 = hash31(i + vec3(1.0, 0.0, 1.0));
  float n011 = hash31(i + vec3(0.0, 1.0, 1.0));
  float n111 = hash31(i + vec3(1.0, 1.0, 1.0));
  float nx00 = mix(n000, n100, f.x);
  float nx10 = mix(n010, n110, f.x);
  float nx01 = mix(n001, n101, f.x);
  float nx11 = mix(n011, n111, f.x);
  return mix(mix(nx00, nx10, f.y), mix(nx01, nx11, f.y), f.z);
}
float fbm3(vec3 p) {
  float v = 0.0;
  float amp = 0.5;
  for (int i = 0; i < 3; i++) {
    v += amp * vnoise(p);
    p = p * 2.02 + 7.3;
    amp *= 0.5;
  }
  return v;
}
void main() {
  vec3 d = normalize(vDir);
  /* 星云：fbm 取册色暗变体，峰值分量 ≤ 0.045（低于 bloom 阈值，不泛光） */
  float n = fbm3(d * 2.6 + vec3(0.0, uTime * 0.006, 0.0));
  float n2 = fbm3(d * 5.1 + 11.0);
  vec3 nebA = vec3(0.010, 0.036, 0.030);
  vec3 nebB = vec3(0.028, 0.016, 0.030);
  vec3 nebC = vec3(0.040, 0.010, 0.024);
  vec3 neb = mix(nebA, nebB, smoothstep(0.40, 0.75, n2));
  neb = mix(neb, nebC, smoothstep(0.55, 0.90, n) * 0.7);
  /* 深空底色：纯黑会显“灭”，给一层极暗深蓝把整体亮度托起来
   *（峰值 0.018 < bloom 阈值，不泛光） */
  vec3 col = vec3(0.006, 0.010, 0.018);
  col += neb * smoothstep(0.45, 0.82, n);
  /* 微星：方向网格 + 每格随机点，单点小面积（泛光受控） */
  vec3 p = d * 260.0;
  vec3 id = floor(p);
  vec3 f = fract(p) - 0.5;
  vec3 rnd = hash33(id);
  float mask = step(0.978, hash31(id + 19.7));
  float dist = length(f - (rnd - 0.5) * 0.8);
  float star = mask * (1.0 - smoothstep(0.0, 0.14, dist));
  float tw = 0.75 + 0.25 * sin(uTime * (1.4 + rnd.x * 2.2) + rnd.y * 6.2831);
  vec3 starCol = mix(vec3(0.55, 0.65, 0.95), vec3(1.0, 0.92, 0.78), rnd.z);
  col += starCol * (0.25 + 0.75 * rnd.x) * star * tw * 0.7;
  gl_FragColor = vec4(col, 1.0);
}`

interface StarSkyCtl {
  sky: THREE.Mesh
  dusts: { points: THREE.Points; material: THREE.ShaderMaterial; spec: DustLayerSpec }[]
  time: { value: number }
}

/** 幂等挂载：天空球 + 三层星尘。重复 bindFg 不叠加（REQ-SMA-002/006）。 */
function addStarSky(
  scene: THREE.Scene,
  time: { value: number },
  pixelRatio: number,
  height: number,
): StarSkyCtl {
  const existing = scene.userData.starSky as StarSkyCtl | undefined
  if (existing) return existing

  const skyMat = new THREE.ShaderMaterial({
    uniforms: { uTime: time },
    vertexShader: SKY_VERTEX_SHADER,
    fragmentShader: SKY_FRAGMENT_SHADER,
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
  })
  const sky = new THREE.Mesh(new THREE.SphereGeometry(5200, 32, 16), skyMat)
  sky.renderOrder = -100
  sky.raycast = () => {}
  scene.add(sky)

  const dusts = DUST_LAYERS.map((spec, idx) => {
    const data = makeDustLayer(spec, 20261104 + idx * 7919)
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(data.positions, 3))
    geo.setAttribute('aPhase', new THREE.BufferAttribute(data.phases, 1))
    geo.setAttribute('aSpeed', new THREE.BufferAttribute(data.speeds, 1))
    geo.setAttribute('aColor', new THREE.BufferAttribute(data.colors, 3))
    const material = new THREE.ShaderMaterial({
      uniforms: {
        uTime: time,
        uSize: { value: spec.size * pixelRatio },
        uScale: { value: height * 0.5 },
        uOpacity: { value: spec.opacity },
      },
      vertexShader: DUST_VERTEX_SHADER,
      fragmentShader: DUST_FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
    })
    const points = new THREE.Points(geo, material)
    points.renderOrder = -1
    points.frustumCulled = false
    points.raycast = () => {}
    scene.add(points)
    return { points, material, spec }
  })

  const ctl = { sky, dusts, time }
  scene.userData.starSky = ctl
  return ctl
}

/* 6 册色相 = 分类维度编码（恢复旧知识地图的按册着色，换成深底天体色系）；
 * 亮度仍编码掌握度：颜色 x (0.35 + brightness * 0.65)，暗星依然暗。 */const MODULE_STAR_COLORS: Record<string, string> = {
  必修一: '#2dd4bf',
  必修二: '#60a5fa',
  选择性必修一: '#a78bfa',
  选择性必修二: '#f472b6',
  选择性必修三: '#fbbf24',
  专题方法: '#fb7185',
}
const FALLBACK_STAR = '#9fb0c6'
function hexToRgba(hex: string, alpha: number): string {
  const c = new THREE.Color(hex)
  return `rgba(${Math.round(c.r * 255)},${Math.round(c.g * 255)},${Math.round(c.b * 255)},${alpha})`
}

export function StarMap({ nodes, edges, onSelect, selectedKp, layout, onLayoutSettled, onReset, children }: {
  nodes: StarGraphNode[]
  edges: StarGraphEdge[]
  onSelect: (kpId: string) => void
  selectedKp?: string | null
  /** 全校共享坐标（REQ-SMI-006）：有坐标的星固定，缺的由册锚点力落位 */
  layout?: Record<string, [number, number, number]>
  /** 首次收敛后回报缺坐标星的位置（由父组件只插入不覆盖地存后端） */
  onLayoutSettled?: (points: { kp_id: string; x: number; y: number; z: number }[]) => void
  /** 重置：清除父组件选中（关闭下钻面板）；不传则不显示按钮 */
  onReset?: () => void
  children?: ReactNode
}) {
  const fgRef = useRef<{ controls: () => { autoRotate: boolean; autoRotateSpeed: number } } | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [dim, setDim] = useState<{ w: number; h: number }>({ w: 800, h: 620 })
  /* 尊重系统「减弱动效」（DESIGN.md R-M1 / REQ-SMI-001）：开启时巡航默认关，手动开关仍可用 */
  const [spinning, setSpinning] = useState(
    () => !(typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches))
  const [isFull, setIsFull] = useState(false)
  const tex = useMemo(glowTexture, [])
  const flareTex = useMemo(flareTexture, [])
  /* 氛围层：所有材质共享一个 uTime；reduced-motion 时冻结在 0 */
  const skyTimeRef = useRef({ value: 0 })
  const skyCtlRef = useRef<StarSkyCtl | null>(null)
  const reducedMotionRef = useRef(false)

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const apply = () => {
      reducedMotionRef.current = mq.matches
      if (mq.matches) skyTimeRef.current.value = 0
    }
    apply()
    mq.addEventListener?.('change', apply)
    return () => mq.removeEventListener?.('change', apply)
  }, [])

  useEffect(() => {
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      if (!reducedMotionRef.current) {
        const t = (now - t0) / 1000
        skyTimeRef.current.value = t
        /* 星尘缓慢漂移：三层不同角速度，静止看也有“漂浮”感；
         * 只转装饰层，数据星位置不动（空间记忆不能被破坏）。 */
        const ctl = skyCtlRef.current
        if (ctl) {
          ctl.dusts.forEach((d, i) => {
            d.points.rotation.y = t * (0.006 + i * 0.005)
            d.points.rotation.x = Math.sin(t * 0.03 + i * 1.7) * 0.03
          })
        }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setDim({ w: el.clientWidth, h: el.clientHeight })
    })
    ro.observe(el)
    const onFsChange = () => setIsFull(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', onFsChange)
    return () => {
      ro.disconnect()
      document.removeEventListener('fullscreenchange', onFsChange)
    }
  }, [])

  const toggleFullscreen = useCallback(() => {
    const el = wrapRef.current
    if (!el) return
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => { /* 用户 Esc 退出时忽略 */ })
    } else {
      el.requestFullscreen().catch(() => { /* 被拒绝时忽略 */ })
    }
  }, [])

  const graphData = useMemo(() => {
    const ids = new Set(nodes.map((n) => n.kp_id))
    /* 悬空边（历史边表存在以中文名当端点的行）必须丢弃：force-graph 的数据
     * 管道遇到 endpoint 不在节点集的 link 会静默中断引擎初始化（表现为全图
     * 叠在原点）；ECharts 容错不画，所以旧视图从未暴露这批脏数据。 */
    const links = edges
      .filter((e) => e.status === 'active' && ids.has(e.from_kp) && ids.has(e.to_kp))
      .map((e) => ({ source: e.from_kp, target: e.to_kp }))
    return {
      nodes: nodes.map((n) => {
        const pos = layout?.[n.kp_id]
        return pos
          ? { ...n, id: n.kp_id, val: starSize(n.class_answers), x: pos[0], y: pos[1], z: pos[2], fx: pos[0], fy: pos[1], fz: pos[2] }
          : { ...n, id: n.kp_id, val: starSize(n.class_answers) }
      }),
      links,
    }
  }, [nodes, edges, layout])

  /* 每份数据（含切换班级）只做一次初始取景，之后镜头完全交给用户 */
  const fittedRef = useRef(false)
  useEffect(() => { fittedRef.current = false }, [graphData])

  /* 星系团：每册一个锚点，forceX/Y/Z 把同册的星拉向各自的星域。
   * fgReady 由 ref callback 置位 —— lazy(Suspense) 解析前 effect 跑到的是
   * null ref，只依赖 [graphData] 会永远错过挂力窗口。 */
  const [fgReady, setFgReady] = useState(false)
  const bindFg = useCallback((inst: unknown) => {
    setFgReady(Boolean(inst))
    if (!inst) return
    /* 深空氛围层：天空球 + 三层星尘（纯装饰，不参与交互/数据）。
     * 相机 far 必须显式放大：球半径 5200 + maxDistance 2600 = 7800。 */
    try {
      const fg = inst as {
        scene?: () => THREE.Scene
        renderer?: () => THREE.WebGLRenderer
        camera?: () => THREE.PerspectiveCamera
      }
      const scene = fg.scene?.()
      const renderer = fg.renderer?.()
      const cam = fg.camera?.()
      if (cam) {
        cam.far = 12000
        cam.updateProjectionMatrix()
      }
      if (scene) {
        const h = renderer?.domElement?.clientHeight || 640
        const pr = renderer?.getPixelRatio?.() ?? 1
        skyCtlRef.current = addStarSky(scene, skyTimeRef.current, pr, h)
      }
    } catch { /* 氛围层失败不影响主图 */ }
    /* 辉光链路 = three.js 官方 webgl_postprocessing_unreal_bloom 的标准写法：
     * RenderPass → UnrealBloomPass → OutputPass，renderer 开色调映射。
     * 幂等守卫（同星尘）：曾因内联 ref 每次渲染重调 bindFg，每次点星/暂停/切班
     * 都往合成器再叠一个 bloom（实测 3→4→5 递增），N 个 bloom 串联 = 指数级
     * 泛光——历次「越点越爆」的真凶。现 ref 已稳定，守卫兜底任何重复调用。 */
    try {
      const fg = inst as {
        scene?: () => THREE.Scene
        postProcessingComposer?: () => { addPass: (p: unknown) => void }
        renderer?: () => THREE.WebGLRenderer
      }
      const scene = fg.scene?.()
      if (scene && !scene.userData.bloomed) {
        scene.userData.bloomed = true
        const renderer = fg.renderer?.()
        if (renderer) {
          renderer.toneMapping = THREE.ACESFilmicToneMapping
          renderer.toneMappingExposure = 1.55
        }
        const bloom = new UnrealBloomPass(new THREE.Vector2(928, 640), 0, 0, 0)
        bloom.threshold = 0.05
        bloom.strength = 1.5
        bloom.radius = 0.6
        const composer = fg.postProcessingComposer?.()
        composer?.addPass(bloom)
        composer?.addPass(new OutputPass())
      }
    } catch { /* 后处理不可用时退化为纯 sprite 渲染 */ }
  }, [])

  /* ref 必须稳定：内联箭头每次渲染都是新函数，React 会先 ref(null) 再 ref(inst)
   * 重调一遍——bindFg 里的副作用随之重放 */
  const fgRefCallback = useCallback((inst: unknown) => {
    fgRef.current = inst as never
    bindFg(inst)
  }, [bindFg])

  useEffect(() => {
    const fg = fgRef.current as unknown as {
      d3Force: (name: string, force?: unknown) => unknown
      d3ReheatSimulation: () => void
    } | null
    if (!fgReady || !fg) return
    fg.d3Force('cluster-x', forceX((n: { module: string }) => moduleAnchor(n.module).x).strength(0.35))
    fg.d3Force('cluster-y', forceY((n: { module: string }) => moduleAnchor(n.module).y).strength(0.35))
    fg.d3Force('cluster-z', forceZ((n: { module: string }) => moduleAnchor(n.module).z).strength(0.35))
    /* 团内撑开：默认斥力下密集团（必修一 44 星）彼此叠成白斑，加大互斥让星散开 */
    const charge = fg.d3Force('charge') as { strength?: (v: number) => unknown } | null
    charge?.strength?.(-260)
    const ctl = (fgRef.current as unknown as {
      controls?: () => { minDistance: number; maxDistance: number }
    } | null)?.controls?.()
    if (ctl) {
      /* 相机不许穿进星团：怼脸时 sprite 糊满全屏 + bloom = 全屏爆白 */
      ctl.minDistance = 120
      ctl.maxDistance = 2600
    }
  }, [fgReady, graphData])

  /* 巡航开关单独成 effect：选星会频繁切换 spinning，不能连带重设力导向 */
  useEffect(() => {
    const ctl = (fgRef.current as unknown as {
      controls?: () => { autoRotate: boolean; autoRotateSpeed: number }
    } | null)?.controls?.()
    if (!fgReady || !ctl) return
    ctl.autoRotate = spinning
    ctl.autoRotateSpeed = 0.8
  }, [fgReady, spinning])

  /* 容器尺寸/像素比变化：星尘点径与 three PointsMaterial 同口径
   *（size × pixelRatio，scale = 画布高 / 2） */
  useEffect(() => {
    const ctl = skyCtlRef.current
    if (!ctl) return
    const fg = fgRef.current as unknown as { renderer?: () => THREE.WebGLRenderer } | null
    const pr = fg?.renderer?.()?.getPixelRatio?.() ?? 1
    for (const d of ctl.dusts) {
      d.material.uniforms.uSize.value = d.spec.size * pr
      d.material.uniforms.uScale.value = dim.h * 0.5
    }
  }, [dim.h, fgReady])

  /* 卸载时释放氛围层资源（场景装饰不归 force-graph 管理） */
  useEffect(() => () => {
    const ctl = skyCtlRef.current
    if (!ctl) return
    ctl.sky.geometry.dispose()
    ;(ctl.sky.material as THREE.Material).dispose()
    for (const d of ctl.dusts) {
      d.points.geometry.dispose()
      d.material.dispose()
    }
    skyCtlRef.current = null
  }, [])

  /* 选中星时自动暂停巡航（被选星与白环停在视野里）；面板关闭时只恢复
   * 「由选星触发」的暂停——用户中途手动按过暂停/开启，就尊重手动选择 */
  const autoPausedRef = useRef(false)
  const selectStar = useCallback((kpId: string) => {
    if (spinning) {
      autoPausedRef.current = true
      setSpinning(false)
    }
    onSelect(kpId)
  }, [spinning, onSelect])
  const handleNodeClick = useCallback((n: object) => {
    selectStar((n as { kp_id: string }).kp_id)
  }, [selectStar])
  const handleNodeHover = useCallback((n: object | null) => {
    hoverRef.current = n ? (n as { kp_id: string }).kp_id : null
  }, [])

  useEffect(() => {
    if (!selectedKp && autoPausedRef.current) {
      autoPausedRef.current = false
      setSpinning(true)
    }
  }, [selectedKp])

  /* 一键回到「全景 + 巡航 + 无选中」：退出聚焦模式的最显式出口 */
  const resetView = useCallback(() => {
    autoPausedRef.current = false
    setSpinning(true)
    onReset?.()
    const inst = fgRef.current as unknown as {
      zoomToFit?: (ms?: number, px?: number) => void
    } | null
    try { inst?.zoomToFit?.(900, 60) } catch { /* 首帧 bbox 未就绪时忽略 */ }
  }, [onReset])

  /* ── 交互层视觉状态（star-map-interactions）────────────────────────
   * 高亮/筛选频繁变化，星体 sprite 绝不能随之重建（161 连重建会闪一帧）：
   * 状态写进 visRef，每颗星/标签的 onBeforeRender 每帧读它算透明度。 */
  const [hiddenModules, setHiddenModules] = useState<Set<string>>(() => new Set())
  const [minErrorPct, setMinErrorPct] = useState(0)
  const [query, setQuery] = useState('')
  const results = useMemo(() => matchStars(nodes, query), [nodes, query])
  /* 飞行定位（官方 click-to-focus 姿势）：相机飞到星前 d=180（> minDistance 120） */
  const flyTo = useCallback((kpId: string) => {
    const inst = fgRef.current as unknown as {
      cameraPosition?: (pos: { x: number; y: number; z: number }, look?: { x: number; y: number; z: number }, ms?: number) => void
    } | null
    const node = graphData.nodes.find((n) => n.kp_id === kpId) as
      | (typeof graphData.nodes[number] & { x?: number; y?: number; z?: number })
      | undefined
    if (!inst?.cameraPosition || !node) return
    const p = { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 }
    const len = Math.hypot(p.x, p.y, p.z) || 1
    const d = 180
    inst.cameraPosition({ x: p.x + (p.x / len) * d, y: p.y + (p.y / len) * d, z: p.z + (p.z / len) * d }, p, 1200)
  }, [graphData])
  const highlight = useMemo(
    () => (selectedKp ? prereqClosure(edges, selectedKp) : null),
    [selectedKp, edges])
  const ghostIds = useMemo(() => {
    const f = { hiddenModules, minErrorRate: minErrorPct / 100 }
    return new Set(nodes.filter((n) => isGhost(n, f)).map((n) => n.kp_id))
  }, [nodes, hiddenModules, minErrorPct])
  const visRef = useRef<{ hl: Set<string> | null; ghost: Set<string>; sel: string | null }>(
    { hl: null, ghost: new Set(), sel: null })
  visRef.current = { hl: highlight?.nodes ?? null, ghost: ghostIds, sel: selectedKp ?? null }
  /* 悬停星：只用来显示它的名字（不触发 React 重渲染，逐帧读 ref） */
  const hoverRef = useRef<string | null>(null)

  const toggleModule = useCallback((m: string) => {
    setHiddenModules((prev) => {
      const next = new Set(prev)
      if (next.has(m)) next.delete(m)
      else next.add(m)
      return next
    })
  }, [])

  /* 连线访问器：高亮链上的边加亮 + 粒子沿先修方向流动；幽灵端点的边淡出。
   * 访问器引用随状态更新，库只改材质，不重建几何。 */
  const linkEnds = (l: object) => {
    const { source, target } = l as { source?: unknown; target?: unknown }
    const id = (v: unknown) => (v && typeof v === 'object' ? (v as { kp_id?: string }).kp_id : v) as string | undefined
    return { from: id(source), to: id(target), src: source as { module?: string } | string | undefined }
  }
  const linkColor = useCallback((l: object) => {
    const { from, to, src } = linkEnds(l)
    const hex = src && typeof src === 'object'
      ? (MODULE_STAR_COLORS[src.module || ''] || FALLBACK_STAR)
      : FALLBACK_STAR
    if (from && to && (ghostIds.has(from) || ghostIds.has(to))) return hexToRgba(hex, 0.06)
    if (highlight) {
      return from && to && highlight.edges.has(edgeKey(from, to))
        ? hexToRgba(hex, 0.85)
        : hexToRgba(hex, 0.16)
    }
    return hexToRgba(hex, 0.45)
  }, [highlight, ghostIds])
  const linkWidth = useCallback((l: object) => {
    const { from, to } = linkEnds(l)
    return highlight && from && to && highlight.edges.has(edgeKey(from, to)) ? 1.8 : 0.95
  }, [highlight])
  const linkParticles = useCallback((l: object) => {
    const { from, to } = linkEnds(l)
    return highlight && from && to && highlight.edges.has(edgeKey(from, to)) ? 3 : 0
  }, [highlight])

  const nodeThreeObject = useMemo(() => (node: object) => {
    const n = node as StarGraphNode & { val?: number }
    const kind = starKind(n.class_error_rate)
    const brightness = starBrightness(n.class_error_rate)
    /* 色相 = 册（全程全饱和，灰样本也保持册色只是更淡）；
     * 明暗 = 掌握度，只走 opacity——不把颜色乘暗，保证「五颜六色」可辨 */
    const color = new THREE.Color(
      MODULE_STAR_COLORS[n.module] || FALLBACK_STAR)
    /* NormalBlending 治本：Additive 重叠无上限累积（视角转到密集团正对就爆白）。
     * 辉光已分层画进贴图，星重叠只遮挡、不累积亮度。
     * 选中高亮不用改星本体（白化/放大会冲爆 bloom）——见独立白环 highlight。 */
    const mat = new THREE.SpriteMaterial({
      /* 形态维度：暗星用四芒贴图（REQ-SMA-004），亮星/灰星保持圆形 */
      map: kind === 'dim' ? flareTex : tex,
      color,
      opacity: kind === 'gray'
        ? 0.62
        : Math.min(1, 0.58 + brightness * 0.42),
      // baseOpacity 供距离淡出（onBeforeRender 每帧改 mat.opacity，需基准值）
      transparent: true,
      depthWrite: false,
    })
    const sprite = new THREE.Sprite(mat)
    const group = new THREE.Group()
    const base = 16 * (n.val ?? starSize(n.class_answers)) * (kind === 'dim' ? 1 : 1.2)
    const scale = base
    const baseOpacity = mat.opacity
    sprite.scale.set(scale, scale, 1)
    /* 每帧：距离钳制（防怼脸穿模）+ 交互层透明度（幽灵 / 高亮链外淡出） */
    sprite.onBeforeRender = (_renderer, _scene, camera) => {
      const cam = camera as THREE.Camera
      const dist = cam.position.distanceTo(group.position)
      const k = 0.3 + 0.7 * Math.min(1, dist / 400)
      sprite.scale.set(scale * k, scale * k, 1)
      const v = visRef.current
      const factor = v.ghost.has(n.kp_id) ? 0.12 : (v.hl && !v.hl.has(n.kp_id) ? 0.35 : 1)
      mat.opacity = baseOpacity * factor
    }

    /* 常驻标签（REQ-SMI-005，官方 html-nodes 写法）：HTML 叠加层不进 WebGL
     * 后处理，bloom 不会糊字；pointer-events none 不拦截画布交互。
     * 文字用近白 + 小色点：色相留给星体，文字只负责可读性（对比度）。 */
    /* token-lint:disable-frame */
    /* R-C9：星图深空画布专用标签色（近白文字 + 深底），不进浅色主题 */
    const el = document.createElement('div')
    const dot = document.createElement('span')
    dot.style.cssText = 'display:inline-block;width:4px;height:4px;border-radius:50%;'
      + 'margin-right:5px;vertical-align:middle;'
    dot.style.background = MODULE_STAR_COLORS[n.module] || FALLBACK_STAR
    el.append(dot, document.createTextNode(n.name))
    el.style.cssText = 'font-size:11px;line-height:1;padding:2px 6px;border-radius:6px;'
      + 'background:rgba(3,8,18,0.72);white-space:nowrap;pointer-events:none;'
      + 'color:#e8eef8;font-weight:500;text-shadow:0 1px 2px rgba(0,0,0,.9);'
      + 'transition:opacity 0.15s'
    const label = new CSS2DObject(el)
    label.center.set(0.5, -0.6) // 标签在星的正上方
    label.onBeforeRender = (_renderer, _scene, camera) => {
      const v = visRef.current
      const hovered = hoverRef.current === n.kp_id
      /* 静止不挂字：平时画面保持干净；悬停显示这一颗；选中只显示选中的。
       * 先修链的名字在右侧面板里，不再浮在画布上。 */
      let op = 0
      if (!v.ghost.has(n.kp_id)) {
        if (v.sel) op = v.sel === n.kp_id ? 1 : (hovered ? 0.9 : 0)
        else if (hovered) op = 1
      }
      el.style.opacity = String(op)
      el.style.visibility = op < 0.02 ? 'hidden' : 'visible'
      /* 近处标签压住远处：避免远处文字盖在近处星上，破坏深度 */
      el.style.zIndex = String(Math.max(0, Math.round(10000 - (camera as THREE.Camera).position.distanceTo(group.position))))
      const selected = v.sel === n.kp_id
      el.style.fontWeight = selected ? '700' : hovered ? '600' : '500'
      el.style.color = selected || hovered ? '#ffffff' : '#e8eef8'
      el.style.background = selected ? 'rgba(3,8,18,0.9)' : hovered ? 'rgba(3,8,18,0.82)' : 'rgba(3,8,18,0.72)'
    }
    /* token-lint:enable-frame */

    group.add(sprite)
    group.add(label)
    return group
    /* 依赖 = 贴图 + 数据数组：交互状态（选中/高亮/筛选）全走 visRef 逐帧读取，
     * 不能进依赖（否则每次点星全图对象重建）。但换班必须换函数身份——库按
     * node id 复用旧对象，身份不变时不会重建，星星会停留在上一班的数据上。 */
  }, [tex, flareTex, nodes])

  /* 选中标记：白色圆环套在被选星外，rAF 每帧跟随（布局未稳时星会漂）。
   * 独立对象 = 星本体 sprite 不因选中重建（161 连重建会闪一帧）也不爆 bloom。 */
  useEffect(() => {
    const fg = fgRef.current as unknown as { scene?: () => THREE.Scene } | null
    const scene = fg?.scene?.()
    if (!scene || !selectedKp) return
    const node = graphData.nodes.find((n) => n.kp_id === selectedKp) as
      | (typeof graphData.nodes[number] & { x?: number; y?: number; z?: number })
      | undefined
    if (!node) return

    const size = 96
    const cv = document.createElement('canvas')
    cv.width = cv.height = size
    const ctx = cv.getContext('2d')!
    ctx.strokeStyle = 'rgba(255,255,255,0.9)'
    ctx.lineWidth = 5
    ctx.beginPath()
    ctx.arc(size / 2, size / 2, size / 2 - 6, 0, Math.PI * 2)
    ctx.stroke()
    const ringTex = new THREE.CanvasTexture(cv)
    ringTex.colorSpace = THREE.SRGBColorSpace

    /* 环亮度必须压在 bloom 阈值(0.32)下：白环 0.85 不透明曾被 bloom 糊成
     * 巨型白球——历次「点星爆白」的真凶就是它自己。 */
    const ring = new THREE.Sprite(new THREE.SpriteMaterial({
      map: ringTex, color: 0x8896aa, transparent: true,
      opacity: 0.5, depthWrite: false, depthTest: false,
    }))
    const starScale = 14 * (node.val ?? starSize(node.class_answers))
    ring.scale.set(starScale * 1.7, starScale * 1.7, 1)
    ring.renderOrder = 10
    scene.add(ring)

    let raf = 0
    const sync = () => {
      ring.position.set(node.x ?? 0, node.y ?? 0, node.z ?? 0)
      raf = requestAnimationFrame(sync)
    }
    sync()
    return () => {
      cancelAnimationFrame(raf)
      scene.remove(ring)
      ringTex.dispose()
    }
  }, [selectedKp, graphData, fgReady])

  return (
    <div
      ref={wrapRef}
      className="relative rounded-[var(--radius-lg)] overflow-hidden"
      style={{
        height: 640,
        /* R-C9 独立接缝：深空底只存在于星图画布，不进全局 data-theme */
        background:
          'radial-gradient(ellipse 90% 70% at 30% 20%, #08101f 0%, #020409 55%, #000103 100%)',
      }}
    >
      <Suspense fallback={
        <div className="absolute inset-0 flex items-center justify-center">
          <p className="text-sm text-slate-400">星图引擎加载中…</p>
        </div>
      }>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <ForceGraph3D
          ref={fgRefCallback as never}
          extraRenderers={[new CSS2DRenderer()]}
          width={dim.w}
          height={dim.h}
          graphData={graphData as never}
          backgroundColor="#000103"
          showNavInfo={false}
          nodeRelSize={3.2}
          nodeThreeObject={nodeThreeObject}
          nodeLabel={(n: object) => {
            const d = n as StarGraphNode
            const kind = starKind(d.class_error_rate)
            const stat = kind === 'gray'
              ? '无可信样本'
              : `班级错误率 ${(100 * (d.class_error_rate ?? 0)).toFixed(1)}% · ${d.class_answers} 次作答`
            return `<div style="background:rgba(7,13,24,.92);border:1px solid rgba(45,212,191,.35);border-radius:8px;padding:8px 10px;font-size:12px;color:#e2e8f0;max-width:260px">
              <b>${d.name}</b>（${d.module}）<br/>${stat}<br/>
              <span style="color:#5eead4">点击下钻 · 面板内可直接出变式题</span></div>`
          }}
          linkColor={linkColor}
          linkWidth={linkWidth}
          linkOpacity={1}
          linkDirectionalParticles={linkParticles}
          linkDirectionalParticleWidth={1.2}
          linkDirectionalParticleSpeed={0.006}
          linkDirectionalParticleColor={() => '#5eead4'}
          onNodeClick={handleNodeClick}
          onNodeHover={handleNodeHover}
          /* 关节点拖拽：按下即触发拖拽会重新加热引擎（扰动全班共用布局），
           * 冷却后再次 onEngineStop——曾因此在放大后点星时镜头被 zoomToFit 拉回全景 */
          enableNodeDrag={false}
          cooldownTicks={120}
          onEngineStop={() => {
            /* 初始取景只做一次（每份数据一次）：之后引擎任何原因再停都不许动镜头 */
            if (fittedRef.current) return
            fittedRef.current = true
            /* 布局持久化：缺坐标的星此刻已收敛，回报给父组件存后端（只插入不覆盖）。
             * 不回写本地状态——改 graphData 会重启引擎、重新取景 */
            const missing = graphData.nodes
              .filter((n) => !layout?.[n.kp_id])
              .map((n) => {
                const p = n as { kp_id: string; x?: number; y?: number; z?: number }
                return { kp_id: p.kp_id, x: p.x ?? 0, y: p.y ?? 0, z: p.z ?? 0 }
              })
            if (missing.length && onLayoutSettled) onLayoutSettled(missing)
            const inst = fgRef.current as unknown as {
              zoomToFit?: (ms?: number, px?: number) => void
            } | null
            try { inst?.zoomToFit?.(900, 60) } catch { /* 首帧 bbox 未就绪时忽略 */ }
          }}
        />
      </Suspense>

      <div className="absolute top-3 left-3 z-10 flex items-center gap-2">
        <div className="relative">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索知识点…"
            className="w-44 px-3 py-1.5 rounded-full text-xs bg-white/5 border border-white/10
                       text-slate-200 placeholder:text-slate-500 outline-none
                       focus:border-teal-300/40"
            aria-label="搜索知识点"
          />
          {results.length > 0 && (
            <ul className="absolute left-0 top-full mt-1.5 w-56 max-h-64 overflow-y-auto rounded-xl
                           border border-white/10 bg-[#0b1220]/95 backdrop-blur-sm shadow-xl z-20 py-1">
              {results.map((r) => (
                <li key={r.kp_id}>
                  <button
                    onClick={() => { setQuery(''); selectStar(r.kp_id); flyTo(r.kp_id) }}
                    className="w-full text-left px-3 py-1.5 text-xs text-slate-300
                               hover:bg-white/10 transition-colors cursor-pointer truncate"
                  >
                    <span style={{ color: MODULE_STAR_COLORS[r.module] || FALLBACK_STAR }}>●</span>
                    {' '}{r.name}
                    <span className="text-slate-500 ml-1">{r.module}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <button
          onClick={() => {
            autoPausedRef.current = false // 手动接管：面板关闭时不再自动恢复
            setSpinning((s) => !s)
          }}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs
                     bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10 transition-colors cursor-pointer"
          aria-label={spinning ? '暂停巡航' : '开启巡航'}
        >
          {spinning ? <Pause size={12} /> : <Play size={12} />}
          {spinning ? '巡航中' : '已暂停'}
        </button>
        {onReset && (
          <button
            onClick={resetView}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs
                       bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10 transition-colors cursor-pointer"
            aria-label="重置视图"
          >
            <RotateCcw size={12} />
            重置
          </button>
        )}
        <button
          onClick={toggleFullscreen}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs
                     bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10 transition-colors cursor-pointer"
          aria-label={isFull ? '退出全屏' : '全屏'}
        >
          {isFull ? <Minimize2 size={12} /> : <Maximize2 size={12} />}
          {isFull ? '退出全屏' : '全屏'}
        </button>
      </div>

      <div className="absolute bottom-3 left-3 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
        {Object.entries(MODULE_STAR_COLORS).map(([m, c]) => {
          const off = hiddenModules.has(m)
          return (
            <button
              key={m}
              onClick={() => toggleModule(m)}
              aria-pressed={!off}
              title={off ? `显示${m}` : `隐藏${m}`}
              className={`inline-flex items-center gap-1.5 cursor-pointer transition-opacity ${off ? 'opacity-30' : ''}`}
            >
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: c, boxShadow: off ? 'none' : `0 0 6px ${c}` }} />
              <span className={off ? 'line-through' : ''}>{m}</span>
            </button>
          )
        })}
        <span className="text-slate-500">·</span>
        <label className="inline-flex items-center gap-1.5 cursor-pointer">
          <span>只看错误率 ≥ {minErrorPct}%</span>
          <input
            type="range" min={0} max={90} step={10} value={minErrorPct}
            onChange={(e) => setMinErrorPct(Number(e.target.value))}
            className="w-24 accent-teal-300 cursor-pointer"
            aria-label="错误率下限"
          />
        </label>
        <span className="text-slate-500">·</span>
        <span>越暗 = 越薄弱 · 星越大作答越多 · 线色 = 所属册（先修关系）</span>
      </div>

      {/* 叠加层插槽（下钻面板）：放在画布容器内部，全屏时随画布一起进入全屏层 */}
      {children}
    </div>
  )
}

