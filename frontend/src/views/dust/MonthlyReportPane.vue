<template>
  <div class="report-pane">
    <div class="filter-bar">
      <label class="filter-item">
        <span>报表月份</span>
        <select v-model="month" @change="reload">
          <option v-for="item in months" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="button" @click="reload">刷新报表</button>
      <span class="filter-tip">结论与「治理前后对比」视图逐条一致（同一分段口径）</span>
    </div>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">治理分段数</span>
        <strong class="stat-value">{{ summary.治理分段数 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">涉及测点</span>
        <strong class="stat-value">{{ summary.涉及测点数 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">月平均降尘效率</span>
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

    <section v-if="reasonEntries.length" class="reason-box">
      <h4>效率留空原因</h4>
      <p v-for="[reason, count] in reasonEntries" :key="reason" class="reason-line">
        <span class="reason-count">{{ count }}</span> 段 — {{ reason }}
      </p>
    </section>

    <section v-if="measureEntries.length" class="reason-box">
      <h4>本月降尘措施分布</h4>
      <p v-for="[name, count] in measureEntries" :key="name" class="reason-line">
        <span class="tag measure">{{ name }}</span> {{ count }} 段
      </p>
    </section>

    <h3 class="block-title">治理前后对比明细（{{ reportMonth }}）</h3>
    <table class="data-table report-table">
      <thead>
        <tr>
          <th>测点编号</th>
          <th>所在区域</th>
          <th>降尘措施</th>
          <th class="num">治理前</th>
          <th class="num">治理后</th>
          <th class="num">降尘效率</th>
          <th class="num">游离SiO₂</th>
          <th>对比结论（与视图一致）</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in items" :key="`${row.测点编号}-${row.生效日期 ?? 'none'}`"
            :class="{ 'silica-row': row.二氧化硅超限 }">
          <td>
            {{ row.测点编号 }}
            <span v-if="row.数据来源 === '存量回填'" class="tag legacy">存量回填</span>
          </td>
          <td>{{ row.所在区域 }}</td>
          <td>{{ row.降尘措施 ?? '—' }}<div class="muted-text">生效：{{ row.生效日期 ?? '—' }}</div></td>
          <td class="num">{{ row.治理前浓度 ?? '—' }}</td>
          <td class="num">{{ row.治理后浓度 ?? '—' }}</td>
          <td class="num">
            <strong v-if="row.降尘效率 != null">{{ row.降尘效率 }}%</strong>
            <span v-else class="blank-cell" :title="row.效率留空原因">留空</span>
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
    <p v-if="!items.length" class="empty-block">{{ reportMonth }} 暂无生效措施或复测记录</p>

    <p class="caliber-note">口径：{{ caliber }}</p>
    <footer class="page-foot">
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
  治理后浓度: number | null
  降尘效率: number | null
  效率留空原因: string
  数据来源: string
  对比结论: string
}

type ReportResponse = {
  月份: string | null
  可选月份: string[]
  口径说明: string
  items: Segment[]
  summary: {
    治理分段数: number
    涉及测点数: number
    可计算效率: number
    效率留空: number
    留空原因: Record<string, number>
    二氧化硅超限: number
    平均降尘效率: number | null
    措施分布: Record<string, number>
  }
}

const month = ref('')
const months = ref<string[]>([])
const items = ref<Segment[]>([])
const caliber = ref('')
const errorMessage = ref('')
const summary = ref<Partial<ReportResponse['summary']>>({})

const reportMonth = computed(() => month.value || '—')
const reasonEntries = computed(() => Object.entries(summary.value.留空原因 ?? {}))
const measureEntries = computed(() => Object.entries(summary.value.措施分布 ?? {}))

async function reload() {
  errorMessage.value = ''
  const query = month.value ? `?month=${encodeURIComponent(month.value)}` : ''
  try {
    const response = await request(`/api/dust/monthly-report${query}`)
    if (!response.ok) throw new Error('粉尘防治月度报表读取失败')
    const data: ReportResponse = await response.json()
    month.value = data.月份 ?? ''
    months.value = data.可选月份 ?? []
    items.value = data.items ?? []
    caliber.value = data.口径说明 ?? ''
    summary.value = data.summary ?? {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '粉尘防治月度报表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.filter-tip { margin-left: auto; color: var(--muted); font-size: 12px; }
.filter-item select { padding: 4px 8px; min-width: 130px; }
.block-title { font-size: 14px; margin: 14px 0 8px; }
.report-table th.num, .report-table td.num { text-align: right; white-space: nowrap; }
.muted-text { color: var(--muted); font-size: 11px; font-weight: 400; }
.blank-cell { color: #b0894f; }
.silica-flag { color: #b42318; font-weight: 600; }
.silica-row { background: #fef3f2; }
.silica-row td { border-color: #f3c2bd; }
.tag {
  display: inline-block;
  border-radius: 4px;
  padding: 0 6px;
  font-size: 11px;
  line-height: 18px;
}
.tag.danger { background: #fee4e2; color: #b42318; }
.tag.legacy { background: #e8f0fe; color: #1f6feb; }
.tag.measure { background: #e8f0fe; color: #1f6feb; margin-right: 4px; }
.stat-card.warn .stat-value { color: #b42318; }
.reason-box {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 12px;
}
.reason-box h4 { margin: 0 0 6px; font-size: 13px; }
.reason-line { margin: 4px 0; font-size: 13px; }
.reason-count { font-weight: 600; color: #b25e09; }
.conclusion { min-width: 320px; }
.empty-block {
  background: #fff;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 24px;
  text-align: center;
  color: var(--muted);
}
.caliber-note { color: var(--muted); font-size: 12px; margin-top: 10px; }
</style>
