<template>
  <section class="page" data-module="dust-compare">
    <header class="page-head">
      <div>
        <h2>粉尘治理前后对比</h2>
        <p class="page-desc">
          同一测点治理前后读数并排对比，按措施生效日期分段计算降尘效率，组内按效率高低排列；
          游离二氧化硅超过 {{ silicaLimit }}% 限值单独标出。治理前未采样、治理后未复测时效率留空并注明原因，不计为零。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="showReport = !showReport">
          {{ showReport ? '收起月度报表' : '查看月度报表' }}
        </button>
        <a class="btn" href="/dust">返回粉尘防治台账</a>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">纳入测点</span>
        <strong class="stat-value">{{ summary['纳入测点数'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">对比段数（有效/留空）</span>
        <strong class="stat-value">{{ summary['对比段数'] ?? 0 }} / {{ summary['有效段数'] ?? 0 }} / {{ summary['留空段数'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">平均降尘效率</span>
        <strong class="stat-value">{{ fmtPct(summary['平均降尘效率']) }}</strong>
      </article>
      <article class="stat-card stat-alert">
        <span class="stat-label">游离二氧化硅超限测点</span>
        <strong class="stat-value">{{ summary['超限测点数'] ?? 0 }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>降尘措施</span>
        <select v-model="filters.measure">
          <option value="">全部措施</option>
          <option v-for="m in options.measures" :key="m" :value="m">{{ m }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>所在区域</span>
        <select v-model="filters.region">
          <option value="">全部区域</option>
          <option v-for="r in options.regions" :key="r" :value="r">{{ r }}</option>
        </select>
      </label>
      <button class="btn primary" type="submit">应用筛选</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置</button>
    </form>

    <p v-if="overLimit.length" class="alert-strip">
      游离二氧化硅超过 {{ silicaLimit }}% 限值：
      <span v-for="item in overLimit" :key="String(item['测点编号'])" class="alert-tag">
        {{ item['测点编号'] }}（{{ item['所在区域'] }}，{{ item['游离二氧化硅'] }}%）
      </span>
    </p>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <div v-for="group in groups" :key="group['区域']" class="region-group">
      <h3 class="region-title">
        {{ group['区域'] }}
        <small>
          对比段 {{ group['对比段数'] }}（有效 {{ group['有效段数'] }}）·
          区域平均效率 {{ fmtPct(group['区域平均效率']) }}
          <em v-if="group['超限测点数']" class="alert-inline">· 超限测点 {{ group['超限测点数'] }}</em>
        </small>
      </h3>
      <table class="data-table compare-table">
        <thead>
          <tr>
            <th>测点编号</th>
            <th>粉尘类型</th>
            <th>游离二氧化硅</th>
            <th>措施分段</th>
            <th>降尘措施</th>
            <th>治理前浓度</th>
            <th>治理后浓度</th>
            <th>降尘效率</th>
            <th>说明 / 留空原因</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in group.rows"
            :key="`${row.point_id}-${row['段序号']}`"
            :class="{ 'row-over': row['超限'], 'row-blank': row['降尘效率'] === null }"
          >
            <td>
              {{ row['测点编号'] }}
              <span v-if="row['存量回填']" class="badge badge-legacy" title="存量测点按治理日期回填">存量回填</span>
            </td>
            <td>{{ row['粉尘类型'] }}</td>
            <td :class="{ 'cell-alert': row['超限'] }">
              {{ row['游离二氧化硅'] }}%
              <span v-if="row['超限']" class="badge badge-over">超限</span>
            </td>
            <td>
              第{{ row['段序号'] }}/{{ row['段数'] }}段
              <div class="cell-sub">{{ row['区间起'] }} ~ {{ row['区间止'] ?? '至今' }}</div>
            </td>
            <td>{{ row['降尘措施'] ?? '未登记措施' }}</td>
            <td>{{ fmtReading(row['前读数']) }}</td>
            <td>{{ fmtReading(row['后读数']) }}</td>
            <td class="cell-eff">
              <span v-if="row['降尘效率'] !== null" :class="effClass(row['降尘效率'])">{{ row['降尘效率'] }}%</span>
              <span v-else class="eff-blank">—</span>
            </td>
            <td class="cell-note">
              <span v-if="row['效率留空原因']" class="blank-reason">{{ row['效率留空原因'] }}</span>
              <ul v-if="row['备注'].length" class="note-list">
                <li v-for="(note, i) in row['备注']" :key="i">{{ note }}</li>
              </ul>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="!groups.length" class="empty-state" style="padding: 24px">当前筛选条件下没有可比测点。</p>

    <section v-if="showReport" class="report-panel">
      <header class="report-head">
        <h3>粉尘防治月度报表</h3>
        <form class="report-form" @submit.prevent="loadReport">
          <input v-model="reportMonth" type="month" aria-label="报表月份" />
          <button class="btn primary" type="submit">生成报表</button>
        </form>
      </header>
      <p v-if="reportError" class="error-text">{{ reportError }}</p>
      <template v-else-if="report">
        <p class="report-range">
          统计区间 {{ report['区间'][0] }} ~ {{ report['区间'][1] }} ·
          对比段 {{ report.summary['对比段数'] }}（有效 {{ report.summary['有效段数'] }}、留空 {{ report.summary['留空段数'] }}）·
          平均降尘效率 {{ fmtPct(report.summary['平均降尘效率']) }} ·
          超限测点 {{ report.summary['超限测点数'] }} · 未治理测点 {{ report.summary['未治理测点数'] }}
        </p>
        <ol class="conclusion-list">
          <li v-for="(line, i) in report.conclusions" :key="i" :class="{ 'conclusion-over': line.includes('超限') }">{{ line }}</li>
        </ol>
        <p class="report-tip">报表结论与上方对比视图同一份分段口径生成，逐段读数和效率保持一致。</p>
      </template>
    </section>

    <footer class="page-foot">
      <span>对比时点：{{ asOf }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Reading = { value: number | null; date: string | null; unit?: string | null }
type CompareRow = {
  point_id: number
  测点编号: string
  所在区域: string
  粉尘类型: string
  游离二氧化硅: number
  超限: boolean
  存量回填: boolean
  未治理: boolean
  段序号: number
  段数: number
  区间起: string | null
  区间止: string | null
  降尘措施: string | null
  前读数: Reading
  后读数: Reading
  降尘效率: number | null
  效率留空原因: string
  备注: string[]
}
type Group = {
  区域: string
  对比段数: number
  有效段数: number
  超限测点数: number
  区域平均效率: number | null
  rows: CompareRow[]
}
type Comparison = {
  as_of: string
  silica_limit: number
  summary: Record<string, number | null>
  options: { measures: string[]; regions: string[] }
  groups: Group[]
  over_limit: Array<Record<string, string | number | null>>
}
type Report = {
  month: string
  区间: [string, string]
  summary: Record<string, number | null>
  conclusions: string[]
}

const groups = ref<Group[]>([])
const overLimit = ref<Comparison['over_limit']>([])
const summary = ref<Record<string, number | null>>({})
const options = ref<Comparison['options']>({ measures: [], regions: [] })
const silicaLimit = ref(10)
const asOf = ref('')
const errorMessage = ref('')
const filters = reactive({ measure: '', region: '' })

const showReport = ref(false)
const reportMonth = ref('2026-09')
const report = ref<Report | null>(null)
const reportError = ref('')

function fmtPct(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value}%`
}

function fmtReading(reading: Reading): string {
  if (reading.value === null) return '—（无数）'
  return `${reading.value} ${reading.unit ?? ''}（${reading.date ?? '日期缺失'}）`
}

function effClass(value: number): string {
  if (value >= 60) return 'eff-good'
  if (value >= 30) return 'eff-mid'
  if (value < 0) return 'eff-bad'
  return 'eff-low'
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.measure) query.set('measure', filters.measure)
  if (filters.region) query.set('region', filters.region)
  try {
    const response = await request(`/api/dust/compare?${query.toString()}`)
    if (!response.ok) throw new Error('对比数据读取失败')
    const payload: Comparison = await response.json()
    groups.value = payload.groups
    overLimit.value = payload.over_limit
    summary.value = payload.summary
    options.value = payload.options
    silicaLimit.value = payload.silica_limit
    asOf.value = payload.as_of
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '对比数据读取失败'
  }
}

function resetFilters() {
  filters.measure = ''
  filters.region = ''
  void reload()
}

async function loadReport() {
  report.value = null
  reportError.value = ''
  try {
    const response = await request(`/api/dust/monthly-report?month=${encodeURIComponent(reportMonth.value)}`)
    if (!response.ok) {
      const detail = await response.json().catch(() => null)
      throw new Error(detail?.detail ?? '月度报表生成失败')
    }
    report.value = await response.json()
  } catch (error) {
    reportError.value = error instanceof Error ? error.message : '月度报表生成失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.stat-alert .stat-value { color: #b42318; }
.alert-strip {
  margin: 0 0 12px; padding: 8px 12px; border: 1px solid #f0a8a0;
  background: #fef3f2; border-radius: 6px; font-size: 13px; color: #7a271a;
}
.alert-tag { display: inline-block; margin: 0 8px 0 4px; font-weight: 600; }
.region-group { margin-bottom: 18px; }
.region-title { font-size: 15px; margin: 0 0 6px; display: flex; align-items: baseline; gap: 10px; }
.region-title small { color: var(--muted); font-weight: 400; font-size: 12px; }
.alert-inline { color: #b42318; font-style: normal; font-weight: 600; }
.compare-table td, .compare-table th { vertical-align: top; }
.row-over { background: #fff7f6; }
.row-over > td { border-color: #f3c3bd; }
.cell-alert { color: #b42318; font-weight: 600; }
.cell-sub { color: var(--muted); font-size: 12px; margin-top: 2px; }
.cell-eff { white-space: nowrap; font-weight: 600; }
.eff-good { color: #067647; }
.eff-mid { color: #b54708; }
.eff-low { color: #b42318; }
.eff-bad { color: #b42318; }
.eff-blank { color: var(--muted); font-weight: 400; }
.badge {
  display: inline-block; margin-left: 4px; padding: 0 6px; border-radius: 10px;
  font-size: 11px; font-weight: 400; line-height: 16px; vertical-align: middle;
}
.badge-over { background: #fee4e2; color: #b42318; }
.badge-legacy { background: #e0eaff; color: #1849a9; }
.cell-note { font-size: 12px; color: #344054; max-width: 340px; }
.blank-reason { color: #b42318; }
.note-list { margin: 4px 0 0; padding-left: 16px; color: var(--muted); }
.report-panel {
  margin-top: 20px; background: #fff; border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px;
}
.report-head { display: flex; justify-content: space-between; align-items: center; }
.report-form { display: flex; gap: 8px; }
.report-range { color: #344054; font-size: 13px; }
.conclusion-list { margin: 8px 0; padding-left: 20px; font-size: 13px; line-height: 1.9; }
.conclusion-over::marker { color: #b42318; }
.conclusion-over { color: #7a271a; font-weight: 600; }
.report-tip { color: var(--muted); font-size: 12px; margin: 6px 0 0; }
</style>
