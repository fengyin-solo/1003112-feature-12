<template>
  <div class="compare-pane">
    <div class="filter-bar">
      <label class="filter-item">
        <span>所在区域</span>
        <select v-model="area" @change="reload">
          <option value="">全部区域</option>
          <option v-for="item in areas" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>降尘措施</span>
        <select v-model="measure" @change="reload">
          <option value="">全部措施</option>
          <option v-for="item in measures" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>测点编号</span>
        <input v-model="keyword" placeholder="按测点编号检索" @keyup.enter="reload" />
      </label>
      <button class="btn" type="button" @click="reload">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      <span class="filter-tip">
        游离二氧化硅 ≥ {{ limit }}% 按矽尘重点防控；效率留空统一排在最后，不计为 0%
      </span>
    </div>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">对比分段</span>
        <strong class="stat-value">{{ summary.分段总数 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">可计算效率</span>
        <strong class="stat-value">{{ summary.可计算效率 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">平均降尘效率</span>
        <strong class="stat-value">{{ summary.平均降尘效率 == null ? '—' : `${summary.平均降尘效率}%` }}</strong>
      </article>
      <article class="stat-card warn">
        <span class="stat-label">效率留空</span>
        <strong class="stat-value">{{ summary.效率留空 ?? 0 }}</strong>
      </article>
      <article class="stat-card warn">
        <span class="stat-label">二氧化硅超限</span>
        <strong class="stat-value">{{ summary.二氧化硅超限 ?? 0 }}</strong>
      </article>
    </div>

    <div v-for="group in groups" :key="group.area" class="area-group">
      <h3 class="area-title">
        {{ group.area }}
        <span class="area-count">{{ group.items.length }} 段</span>
      </h3>
      <table class="data-table compare-table">
        <thead>
          <tr>
            <th>测点编号</th>
            <th>降尘措施 / 生效日期</th>
            <th class="num">治理前浓度<br /><small>mg/m³</small></th>
            <th class="num">治理后浓度<br /><small>mg/m³</small></th>
            <th class="num">降尘效率</th>
            <th class="num">游离SiO₂</th>
            <th>对比结论</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in group.items" :key="`${row.测点编号}-${row.生效日期 ?? 'none'}`"
              :class="{ 'silica-row': row.二氧化硅超限 }">
            <td>
              {{ row.测点编号 }}
              <span v-if="row.数据来源 === '存量回填'" class="tag legacy">存量回填</span>
            </td>
            <td>
              <template v-if="row.降尘措施">
                {{ row.降尘措施 }}
                <div class="muted-text">生效：{{ row.生效日期 }}</div>
              </template>
              <span v-else class="muted-text">未落实措施</span>
            </td>
            <td class="num">
              <template v-if="row.治理前浓度 != null">{{ row.治理前浓度 }}</template>
              <span v-else class="blank-cell">未采样</span>
              <div class="muted-text">{{ readingTip(row, 'pre') }}</div>
            </td>
            <td class="num">
              <template v-if="row.治理后浓度 != null">{{ row.治理后浓度 }}</template>
              <span v-else class="blank-cell">未复测</span>
              <div class="muted-text">{{ readingTip(row, 'post') }}</div>
            </td>
            <td class="num">
              <strong v-if="row.降尘效率 != null" :class="efficiencyClass(row.降尘效率)">
                {{ row.降尘效率 }}%
              </strong>
              <span v-else class="blank-cell" :title="row.效率留空原因">—</span>
            </td>
            <td class="num">
              <span :class="{ 'silica-flag': row.二氧化硅超限 }">
                {{ row.游离二氧化硅 }}%
                <span v-if="row.二氧化硅超限" class="tag danger">超限</span>
              </span>
            </td>
            <td class="conclusion">{{ row.对比结论 }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="!groups.length" class="empty-block">当前筛选条件下没有对比分段</p>
    <footer class="page-foot">
      <span>共 {{ summary.分段总数 ?? 0 }} 个对比分段；段内按降尘效率从高到低排列</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Segment = {
  测点编号: string
  所在区域: string
  游离二氧化硅: number
  二氧化硅超限: boolean
  降尘措施: string | null
  生效日期: string | null
  治理前浓度: number | null
  治理前读数条数: number
  治理后浓度: number | null
  治理后读数条数: number
  治理前补测条数: number
  治理后补测条数: number
  治理后中断次数: number
  降尘效率: number | null
  效率留空原因: string
  数据来源: string
  对比结论: string
}

type CompareResponse = {
  items: Segment[]
  measures: string[]
  areas: string[]
  游离二氧化硅限值: number
  summary: Record<string, number | null>
}

const area = ref('')
const measure = ref('')
const keyword = ref('')
const errorMessage = ref('')

const items = ref<Segment[]>([])
const measures = ref<string[]>([])
const areas = ref<string[]>([])
const limit = ref(10)
const summary = ref<Record<string, number | null>>({})

const groups = computed(() => {
  const map = new Map<string, Segment[]>()
  for (const row of items.value) {
    const list = map.get(row.所在区域) ?? []
    list.push(row)
    map.set(row.所在区域, list)
  }
  return [...map.entries()]
    .sort((a, b) => a[0].localeCompare(b[0], 'zh-Hans-CN'))
    .map(([groupArea, groupItems]) => ({ area: groupArea, items: groupItems }))
})

function readingTip(row: Segment, phase: 'pre' | 'post'): string {
  const count = phase === 'pre' ? row.治理前读数条数 : row.治理后读数条数
  const supplement = phase === 'pre' ? row.治理前补测条数 : row.治理后补测条数
  if (!count) return ''
  const parts = [`${count} 条读数均值`]
  if (supplement) parts.push(`含补测 ${supplement} 条续测`)
  if (phase === 'post' && row.治理后中断次数) parts.push(`采样中断 ${row.治理后中断次数} 次`)
  return parts.join('，')
}

function efficiencyClass(value: number): string {
  if (value >= 60) return 'eff-good'
  if (value >= 30) return 'eff-mid'
  return 'eff-low'
}

function resetFilters() {
  area.value = ''
  measure.value = ''
  keyword.value = ''
  void reload()
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (area.value) params.set('area', area.value)
  if (measure.value) params.set('measure', measure.value)
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  try {
    const response = await request(`/api/dust/comparison?${params.toString()}`)
    if (!response.ok) throw new Error('治理前后对比数据读取失败')
    const data: CompareResponse = await response.json()
    items.value = data.items ?? []
    measures.value = data.measures ?? []
    areas.value = data.areas ?? []
    limit.value = data.游离二氧化硅限值 ?? 10
    summary.value = data.summary ?? {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '治理前后对比数据读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.filter-tip { margin-left: auto; color: var(--muted); font-size: 12px; }
.filter-item select { padding: 4px 8px; min-width: 130px; }
.area-group { margin-bottom: 16px; }
.area-title {
  font-size: 14px;
  margin: 8px 0 6px;
  padding-left: 8px;
  border-left: 3px solid var(--brand);
}
.area-count { color: var(--muted); font-size: 12px; font-weight: 400; margin-left: 8px; }
.compare-table th.num, .compare-table td.num { text-align: right; white-space: nowrap; }
.compare-table small { color: var(--muted); font-weight: 400; }
.muted-text { color: var(--muted); font-size: 11px; font-weight: 400; }
.blank-cell { color: #b0894f; }
.eff-good { color: #087443; }
.eff-mid { color: #b25e09; }
.eff-low { color: #b42318; }
.silica-flag { color: #b42318; font-weight: 600; }
.silica-row { background: #fef3f2; }
.silica-row td { border-color: #f3c2bd; }
.tag {
  display: inline-block;
  border-radius: 4px;
  padding: 0 6px;
  font-size: 11px;
  line-height: 18px;
  margin-left: 4px;
}
.tag.danger { background: #fee4e2; color: #b42318; }
.tag.legacy { background: #e8f0fe; color: #1f6feb; }
.conclusion { min-width: 320px; }
.stat-card.warn .stat-value { color: #b42318; }
.empty-block {
  background: #fff;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 24px;
  text-align: center;
  color: var(--muted);
}
</style>
