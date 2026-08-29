<template>
  <div>
    <!-- 顶栏 -->
    <div class="topbar">
      <div class="logo"><div class="ic">售</div>AutoWork 售后</div>
      <div class="crumb">工作台 / 售后面板</div>
      <div style="flex:1"></div>
      <el-tag size="small" :type="health ? 'success' : 'danger'" effect="light">
        {{ health ? 'MySQL 已连接' : '数据库未连接' }}
      </el-tag>
      <span style="font-size:11px;color:#a6abb3">{{ version }}</span>
    </div>

    <div class="app">
      <!-- 侧边导航 -->
      <aside class="sidebar">
        <div class="grp">售后</div>
        <div class="item">填写录入</div>
        <div class="item on">记录与统计</div>
        <div class="item">设置</div>
        <div class="side-foot">周期模式 tue<br>只读，填写请使用autowork</div>
      </aside>

      <!-- 主内容 -->
      <main class="main">
        <div class="page-head">
          <h3>记录与统计</h3>
          <span class="sub">售后问题报告</span>
          <div style="flex:1"></div>
          <el-button size="small" @click="load()">刷新</el-button>
        </div>

        <!-- KPI -->
        <div class="kpi-row">
          <div class="kpi a" @click="setKpi('all')">
            <div class="lab">售后总数</div>
            <div class="num a">{{ stats.total ?? '-' }}</div>
            <div class="sub">全库记录</div>
          </div>
          <div class="kpi g" @click="setKpi('unresolved')">
            <div class="lab">未解决</div>
            <div class="num r">{{ stats.unresolved ?? '-' }}</div>
            <div class="sub">待跟进:点击筛选</div>
          </div>
          <div class="kpi b" @click="setKpi('initiative')">
            <div class="lab">我们主动发起</div>
            <div class="num b">{{ stats.initiative ?? '-' }}</div>
            <div class="sub">点击筛选</div>
          </div>
          <div class="kpi y" @click="setKpi('our')">
            <div class="lab">我方问题</div>
            <div class="num y">{{ stats.our_problem ?? '-' }}</div>
            <div class="sub">点击筛选</div>
          </div>
        </div>

        <!-- 筛选栏 -->
        <div class="filter-bar">
          <span class="lbl">周期</span>
          <el-select v-model="f.cycle_start" style="width:170px" clearable placeholder="全部周期" @change="reload">
            <el-option v-for="c in cycles" :key="c" :label="c" :value="c" />
          </el-select>
          <span class="lbl">类型</span>
          <el-select v-model="f.issue_type" style="width:140px" clearable placeholder="全部类型" @change="reload">
            <el-option v-for="t in issueTypes" :key="t" :label="t" :value="t" />
          </el-select>
          <span class="lbl">是否解决</span>
          <el-select v-model="f.resolved" style="width:100px" clearable placeholder="全部" @change="reload">
            <el-option label="是" value="是" /><el-option label="否" value="否" />
          </el-select>
          <span class="lbl">主动发起</span>
          <el-select v-model="f.is_initiative" style="width:100px" clearable placeholder="全部" @change="reload">
            <el-option label="是" value="是" /><el-option label="否" value="否" />
          </el-select>
          <span class="lbl">我们问题</span>
          <el-select v-model="f.is_our_problem" style="width:100px" clearable placeholder="全部" @change="reload">
            <el-option label="是" value="是" /><el-option label="否" value="否" />
          </el-select>
          <el-input v-model="f.keyword" placeholder="搜索" clearable
                    style="width:210px" @keyup.enter="reload" @clear="reload">
            <template #prefix>🔍</template>
          </el-input>
        </div>

        <!-- 图表统计（ECharts，跟随当前筛选口径） -->
        <div class="charts-panel">
          <div class="charts-head">
            <b>📊 图表统计</b>
            <span class="hint">跟随上方筛选条件</span>
            <div style="flex:1"></div>
            <el-switch v-model="chartsVisible" size="small" style="--el-switch-on-color: var(--accent)" />
          </div>
          <div v-show="chartsVisible" class="charts-grid">
            <div class="chart-card"><div class="c-title">地区分布</div><div ref="chartRegion" class="chart-box"></div></div>
            <div class="chart-card"><div class="c-title">每日售后量</div><div ref="chartDaily" class="chart-box"></div></div>
            <div class="chart-card"><div class="c-title">我方问题占比</div><div ref="chartOur" class="chart-box"></div></div>
            <div class="chart-card"><div class="c-title">问题类型分布</div><div ref="chartIssue" class="chart-box"></div></div>
            <!-- 自定义图表（二期 A） -->
            <div class="chart-card" style="grid-column:1 / -1">
              <div class="c-title">自定义图表
                <span style="font-weight:400;color:#a6abb3;margin-left:8px;font-size:11px">自由组合维度与度量</span>
              </div>
              <div class="cust-toolbar">
                <span class="lbl">维度</span>
                <el-select v-model="custom.dimension" size="small" style="width:120px">
                  <el-option v-for="(lbl, k) in DIMS" :key="k" :label="lbl" :value="k" />
                </el-select>
                <span class="lbl">度量</span>
                <el-select v-model="custom.measure" size="small" style="width:86px">
                  <el-option label="数量" value="count" /><el-option label="占比%" value="percent" />
                </el-select>
                <span class="lbl">图表</span>
                <el-select v-model="custom.chart" size="small" style="width:86px">
                  <el-option label="柱状" value="bar" /><el-option label="折线" value="line" />
                  <el-option label="饼图" value="pie" /><el-option label="环形" value="ring" />
                  <el-option label="横向" value="hbar" />
                </el-select>
                <span class="lbl">排序</span>
                <el-select v-model="custom.sort" size="small" style="width:90px">
                  <el-option label="值降序" value="value_desc" /><el-option label="值升序" value="value_asc" />
                </el-select>
                <span class="lbl">TOP</span>
                <el-input-number v-model="custom.limit" size="small" :min="5" :max="50" :step="5" style="width:100px" />
                <el-button size="small" type="primary" @click="saveView">💾 保存视图</el-button>
                <el-select v-model="viewSel" size="small" placeholder="我的视图" style="width:150px" clearable @change="applyView">
                  <el-option v-for="(v, i) in views" :key="i" :label="v.name" :value="i" />
                </el-select>
                <el-button size="small" @click="delView">删除</el-button>
              </div>
              <div ref="chartCustom" class="chart-box"></div>
            </div>
          </div>
        </div>

        <!-- 批量条 -->
        <div class="batch-bar" v-if="selected.length">
          <el-checkbox v-model="allChecked" @change="toggleAll">全选本页</el-checkbox>
          <span>已选 <b>{{ selected.length }}</b> 项</span>
          <div style="flex:1"></div>
          <el-tag size="small" type="warning">批量操作请使用桌面端</el-tag>
          <el-button size="small" @click="selected = []">取消选择</el-button>
        </div>

        <!-- 表格 -->
        <div class="tbl-wrap">
          <el-table :data="rows" v-loading="loading" row-key="id" height="560"
                    :row-class-name="rowClass" @selection-change="onSel"
                    @row-dblclick="openDetail" style="width:100%">
            <el-table-column type="selection" width="36" />
            <el-table-column label="填写时间" min-width="120">
              <template #default="{ row }">
                <div class="cell-t2">{{ row.created_at?.slice(5, 16) }}<span class="s">{{ row.creator }}</span></div>
              </template>
            </el-table-column>
            <el-table-column prop="occurred_at" label="发生时间" width="125" :formatter="fmtShort" />
            <el-table-column prop="issue_type" label="类型" width="90" />
            <el-table-column label="位置" min-width="190">
              <template #default="{ row }">
                <div class="cell-t2">{{ row.room_name }}<span class="s">{{ row.region }} · {{ row.table_no }}</span></div>
              </template>
            </el-table-column>
            <el-table-column prop="problem" label="问题" min-width="180" show-overflow-tooltip />
            <el-table-column prop="cause" label="发生原因" min-width="150" show-overflow-tooltip />
            <el-table-column prop="solution" label="解决方案" min-width="150" show-overflow-tooltip />
            <el-table-column label="解决" width="70" align="center">
              <template #default="{ row }">
                <span class="badge" :class="row.resolved === '是' ? 'ok' : 'no'">{{ row.resolved }}</span>
              </template>
            </el-table-column>
            <el-table-column label="我们问题" width="76" align="center">
              <template #default="{ row }">
                <span class="badge" :class="row.is_our_problem === '是' ? 'warn' : 'no'">{{ row.is_our_problem || '否' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="主动发起" width="76" align="center">
              <template #default="{ row }">
                <span class="badge" :class="row.is_initiative === '是' ? 'info' : 'no'">{{ row.is_initiative || '否' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="响应" min-width="120">
              <template #default="{ row }">
                <div class="cell-t2">{{ row.response_time }}<span class="s">{{ row.resolver }}</span></div>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click.stop="openDetail(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div style="display:flex;align-items:center;padding:10px 14px;border-top:1px solid var(--border);font-size:12px;color:#646a73;">
            <span>共 {{ total }} 条</span>
            <div style="flex:1"></div>
            <el-pagination background layout="prev, pager, next, sizes" :total="total"
                           v-model:current-page="f.page" :page-size="f.page_size"
                           :page-sizes="[50, 100, 200]" @current-change="load" @size-change="onSize" />
          </div>
        </div>
      </main>
    </div>

    <!-- 详情弹窗（只读） -->
    <el-dialog v-model="dlg.show" :title="`记录详情 #${dlg.row?.id ?? ''}`" width="720px">
      <div class="detail-grid" v-if="dlg.row">
        <div class="it"><span class="k">填写时间 / 填写人</span><span class="v">{{ dlg.row.created_at }} · {{ dlg.row.creator }}</span></div>
        <div class="it"><span class="k">发生时间</span><span class="v">{{ dlg.row.occurred_at || '—' }}</span></div>
        <div class="it"><span class="k">类型</span><span class="v">{{ dlg.row.issue_type || '—' }}</span></div>
        <div class="it"><span class="k">位置</span><span class="v">{{ dlg.row.room_name }} · {{ dlg.row.region }} · {{ dlg.row.table_no }}</span></div>
        <div class="it full"><span class="k">问题</span><span class="v">{{ dlg.row.problem || '—' }}</span></div>
        <div class="it full"><span class="k">发生原因</span><span class="v">{{ dlg.row.cause || '—' }}</span></div>
        <div class="it"><span class="k">是否解决</span><span class="v">{{ dlg.row.resolved || '—' }}</span></div>
        <div class="it"><span class="k">解决人</span><span class="v">{{ dlg.row.resolver || '—' }}</span></div>
        <div class="it"><span class="k">响应时间</span><span class="v">{{ dlg.row.response_time || '—' }}</span></div>
        <div class="it"><span class="k">我们主动发起</span><span class="v">{{ dlg.row.is_initiative || '否' }}</span></div>
        <div class="it"><span class="k">是否我们的问题</span><span class="v">{{ dlg.row.is_our_problem || '否' }}</span></div>
        <div class="it full"><span class="k">解决方案</span><span class="v">{{ dlg.row.solution || '—' }}</span></div>
      </div>
      <div class="readonly-tip">ℹ️ 本版为只读查询站点；新增 / 编辑 / 删除 / 批量操作请在桌面端完成。</div>
      <template #footer>
        <el-button @click="dlg.show = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import { fetchRecords, fetchCycleOptions, fetchHealth, fetchCharts, fetchQuery } from './api'

const version = 'v0.2 图表'
const health = ref(false)
const loading = ref(false)
const rows = ref([])
const total = ref(0)
const stats = reactive({ total: null, unresolved: null, initiative: null, our_problem: null })
const cycles = ref([])
const issueTypes = ['单杆视频', '重启主机', '重新调整相机标定', '遥控器没反应', '程序没了', '不能扫码', '识别不了', '记分牌显示不出来', '其他问题']

const f = reactive({ page: 1, page_size: 50, keyword: '', cycle_start: '', issue_type: '', resolved: '', is_initiative: '', is_our_problem: '' })
const selected = ref([])
const allChecked = ref(false)
const dlg = reactive({ show: false, row: null })

// ---- ECharts ----
const chartsVisible = ref(true)
const chartRegion = ref(null)
const chartDaily = ref(null)
const chartOur = ref(null)
const chartIssue = ref(null)
const chartCustom = ref(null)
let charts = []
const PALETTE = ['#00bcd4', '#1a9e6c', '#c98a2d', '#0078d4', '#8f4fd4', '#e05d8a', '#5c6675', '#4a9e5c']
const DIMS = { region: '地区', issue_type: '问题类型', resolved: '是否解决', is_initiative: '主动发起', is_our_problem: '我方问题', table_no: '桌号', creator: '填写人', resolver: '解决人', day: '每天', week: '每周' }

// 自定义图表配置 + 视图持久化
const custom = reactive({ dimension: 'issue_type', measure: 'count', chart: 'bar', sort: 'value_desc', limit: 20 })
const views = ref([])
const viewSel = ref(null)
const VIEWS_KEY = 'aftersale_chart_views'
let queryTimer = null

function loadViews() {
  try { views.value = JSON.parse(localStorage.getItem(VIEWS_KEY) || '[]') } catch { views.value = [] }
}
function persistViews() { localStorage.setItem(VIEWS_KEY, JSON.stringify(views.value)) }
async function saveView() {
  const { value } = await ElMessageBox.prompt('为该自定义视图命名', '保存视图', { inputValue: `自定义 ${views.value.length + 1}` })
  views.value.push({ name: value || '未命名', config: { ...custom } })
  persistViews()
  viewSel.value = views.value.length - 1
  ElMessage.success('视图已保存')
}
function applyView(i) {
  if (i === null || i === undefined || !views.value[i]) return
  Object.assign(custom, views.value[i].config)
  loadCustom()
}
async function delView() {
  if (viewSel.value === null || viewSel.value === undefined) return
  views.value.splice(viewSel.value, 1)
  viewSel.value = null
  persistViews()
}

function initCharts() {
  charts = [
    { el: chartRegion.value, type: 'pie' },
    { el: chartDaily.value, type: 'bar' },
    { el: chartOur.value, type: 'ring' },
    { el: chartIssue.value, type: 'hbar' },
    { el: chartCustom.value, type: 'custom' },
  ].map(cfg => ({ ...cfg, inst: cfg.el ? echarts.init(cfg.el) : null })).filter(c => c.inst)
}
function disposeCharts() {
  charts.forEach(c => c.inst.dispose())
  charts = []
}
function resizeCharts() { charts.forEach(c => c.inst.resize()) }
watch(chartsVisible, v => nextTick(() => { if (v) resizeCharts() }))
watch(custom, () => {
  clearTimeout(queryTimer)
  queryTimer = setTimeout(loadCustom, 250) // 防抖
}, { deep: true })

function renderCharts(d) {
  if (!charts.length) initCharts()
  // 1) 地区分布：top8 + 其他
  const top = d.region_dist.slice(0, 8)
  const rest = d.region_dist.slice(8).reduce((s, x) => s + x.value, 0)
  if (rest > 0) top.push({ name: '其他', value: rest })
  charts[0].inst.setOption({
    tooltip: { trigger: 'item' },
    color: PALETTE,
    legend: { bottom: 0, type: 'scroll', itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
    series: [{ type: 'pie', radius: ['38%', '62%'], center: ['50%', '44%'], avoidLabelOverlap: true,
      itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 1 },
      label: { show: false }, emphasis: { label: { show: true, fontSize: 12, fontWeight: 600 } },
      data: top }],
  })
  // 2) 每日售后量
  charts[1].inst.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 34, right: 10, top: 18, bottom: 24 },
    xAxis: { type: 'category', data: d.daily.map(x => x.date), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } },
    series: [{ type: 'bar', data: d.daily.map(x => x.count), barMaxWidth: 22,
      itemStyle: { color: '#00bcd4', borderRadius: [3, 3, 0, 0] } }],
  })
  // 3) 我方问题占比（环形）
  const y = d.our_problem.yes ?? 0, n = d.our_problem.no ?? 0
  charts[2].inst.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    color: ['#c98a2d', '#e4e7ed'],
    graphic: { type: 'text', left: 'center', top: '38%', style: { text: `${y}`, fontSize: 22, fontWeight: 700, fill: '#c98a2d', textAlign: 'center' } },
    series: [{ type: 'pie', radius: ['58%', '78%'], center: ['50%', '44%'], silent: true,
      label: { show: false }, data: [
        { name: '我方问题', value: y },
        { name: '非我方问题', value: n },
      ] }],
  })
  // 4) 问题类型分布（横向柱状）
  const it = d.issue_type_dist.slice(0, 10)
  charts[3].inst.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 80, right: 24, top: 10, bottom: 20 },
    xAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'category', data: it.map(x => x.name).reverse(), axisLabel: { fontSize: 10, width: 66, overflow: 'truncate' } },
    series: [{ type: 'bar', data: it.map(x => x.value).reverse(), barMaxWidth: 16,
      itemStyle: { color: '#0078d4', borderRadius: [0, 3, 3, 0] } }],
  })
}
async function loadCharts() {
  try {
    const d = await fetchCharts({
      cycle_start: f.cycle_start, issue_type: f.issue_type, resolved: f.resolved,
      is_initiative: f.is_initiative, is_our_problem: f.is_our_problem,
    })
    renderCharts(d)
  } catch { /* 图表失败不阻断列表 */ }
}

// ---- 自定义图表渲染（通用配置 → ECharts option） ----
function customOption(d) {
  const isPie = d.chart === 'pie' || d.chart === 'ring'
  const isHbar = d.chart === 'hbar'
  const fmt = d.measure === 'percent' ? '{b}: {c}%' : '{b}: {c}'
  if (isPie) {
    return {
      tooltip: { trigger: 'item', formatter: d.measure === 'percent' ? '{b}: {c}% ({d}%)' : '{b}: {c} ({d}%)' },
      color: PALETTE, legend: { bottom: 0, type: 'scroll', itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
      series: [{
        type: 'pie', radius: d.chart === 'ring' ? ['40%', '64%'] : '66%', center: ['50%', '44%'],
        itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 1 },
        label: { show: false }, data: d.columns.map(x => ({ name: x.name, value: d.measure === 'percent' ? x.percent : x.value })),
      }],
    }
  }
  const cats = d.columns.map(x => x.name)
  const vals = d.columns.map(x => d.measure === 'percent' ? x.percent : x.value)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: isHbar ? 'shadow' : 'shadow' }, formatter: p => {
      const it = p[0]; const raw = d.columns[it.dataIndex]
      return `${it.name}<br/>${raw.value}${d.measure === 'percent' ? `（${raw.percent}%）` : ''}`
    } },
    grid: isHbar ? { left: 86, right: 20, top: 12, bottom: 20 } : { left: 36, right: 16, top: 16, bottom: 26 },
    xAxis: isHbar
      ? { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } }
      : { type: 'category', data: cats, axisLabel: { fontSize: 10, interval: 0, rotate: cats.length > 8 ? 24 : 0 } },
    yAxis: isHbar
      ? { type: 'category', data: [...cats].reverse(), axisLabel: { fontSize: 10, width: 72, overflow: 'truncate' } }
      : { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } },
    series: [{
      type: d.chart === 'line' ? 'line' : 'bar',
      data: isHbar ? [...vals].reverse() : vals,
      barMaxWidth: 18, smooth: d.chart === 'line',
      itemStyle: { color: PALETTE[0], borderRadius: d.chart === 'line' ? 0 : [3, 3, 0, 0] },
      areaStyle: d.chart === 'line' ? { opacity: 0.08 } : undefined,
    }],
  }
}
async function loadCustom() {
  if (!charts.length) initCharts()
  const c = charts.find(x => x.type === 'custom')
  if (!c) return
  try {
    const d = await fetchQuery({
      ...custom,
      filter: { cycle_start: f.cycle_start, issue_type: f.issue_type, resolved: f.resolved,
                is_initiative: f.is_initiative, is_our_problem: f.is_our_problem },
    })
    c.inst.setOption(customOption(d), true)
  } catch { /* 配置错误不阻断 */ }
}

function fmtShort(_r, _c, v) { return v ? String(v).slice(5, 16) : '—' }
function rowClass({ row }) { return row.is_important ? 'imp-row' : '' }

function setKpi(k) {
  f.resolved = k === 'unresolved' ? '否' : ''
  f.is_initiative = k === 'initiative' ? '是' : ''
  f.is_our_problem = k === 'our' ? '是' : ''
  if (k === 'all') { f.resolved = ''; f.is_initiative = ''; f.is_our_problem = '' }
  reload()
}

function reload() { f.page = 1; load(); loadCharts(); loadCustom() }
function onSize() { f.page = 1; load() }

async function load() {
  loading.value = true
  try {
    const d = await fetchRecords({ ...f })
    rows.value = d.rows
    total.value = d.total
    Object.assign(stats, d.stats)
  } catch (e) {
    ElMessage.error('加载失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

function onSel(sel) { selected.value = sel }
function toggleAll(v) {
  rows.value.forEach(r => { r._checked = v })
}
function openDetail(row) { dlg.row = row; dlg.show = true }

onMounted(async () => {
  try { health.value = !!(await fetchHealth())?.ok } catch { health.value = false }
  try { cycles.value = (await fetchCycleOptions())?.options || [] } catch { /* ignore */ }
  loadViews()
  await nextTick()
  initCharts()
  window.addEventListener('resize', resizeCharts)
  load()
  loadCharts()
  loadCustom()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  disposeCharts()
})
</script>
