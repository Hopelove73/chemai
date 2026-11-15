import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, GraphChart, HeatmapChart, LineChart, PieChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart, GraphChart, HeatmapChart, LineChart, PieChart, ScatterChart,
  GridComponent, LegendComponent, TooltipComponent, VisualMapComponent, CanvasRenderer,
])

export interface EChartProps {
  option: echarts.EChartsCoreOption
  height?: number
  className?: string
  /** 图元点击回调（params.dataType: 'node'|'edge'|…），供图谱等交互图表使用 */
  onEvent?: (params: { dataType?: string; data?: Record<string, unknown> }) => void
}

/** ECharts 封装：按需引入（charts/components/renderers）+ 自适应容器尺寸 */
export function EChart({ option, height = 280, className = '', onEvent }: EChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    chartRef.current = chart
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option as echarts.EChartsCoreOption, { notMerge: true })
  }, [option])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !onEvent) return
    const handler = (params: { dataType?: string; data?: Record<string, unknown> }) => onEvent(params)
    chart.on('click', handler as never)
    return () => {
      chart.off('click', handler as never)
    }
  }, [onEvent])

  return <div ref={ref} className={className} style={{ height, width: '100%' }} />
}

/* 方向 A 图表色板（与 tokens 一致） */
export const CHART_COLORS = {
  primary: '#002147',
  teal: '#0d7377',
  success: '#2c6e49',
  warning: '#b45309',
  error: '#b43c28',
  muted: '#94a3b8',
  bar: ['#002147', '#0d7377', '#2c6e49', '#b45309', '#b43c28', '#64748b'],
  doughnut: ['#002147', '#0d7377', '#2c6e49'],
  line: '#002147',
}

export const CHART_AXIS_TEXT = { color: '#94a3b8', fontSize: 11 }
