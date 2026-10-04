<template>
  <section class="page" data-module="dust">
    <header class="page-head">
      <div>
        <h2>粉尘防治管理</h2>
        <p class="page-desc">维护粉尘测点，并按降尘措施生效日期统一分段：治理前后浓度并排、降尘效率排序、二氧化硅超限标出，结论同步到月度报表。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记粉尘测点</button>
        <button class="btn" type="button" @click="exportRows">导出粉尘防治清单</button>
      </div>
    </header>

    <nav class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab-item"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <LedgerPane v-if="activeTab === 'ledger'" />
    <ComparisonPane v-else-if="activeTab === 'comparison'" />
    <MonthlyReportPane v-else />

    <p v-if="actionMessage" class="page-foot error-text">{{ actionMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import ComparisonPane from '@/views/dust/ComparisonPane.vue'
import LedgerPane from '@/views/dust/LedgerPane.vue'
import MonthlyReportPane from '@/views/dust/MonthlyReportPane.vue'

const tabs = [
  { key: 'ledger', label: '测点台账' },
  { key: 'comparison', label: '治理前后对比' },
  { key: 'report', label: '月度报表' },
] as const

const activeTab = ref<(typeof tabs)[number]['key']>('comparison')
const actionMessage = ref('')

function exportRows() {
  window.open('/api/dust/export', '_blank')
}

function openCreate() {
  actionMessage.value = '粉尘测点登记入口尚未接入审批流'
}
</script>

<style scoped>
.tab-bar {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}
.tab-item {
  border: none;
  background: none;
  padding: 8px 16px;
  cursor: pointer;
  font-size: 13px;
  color: var(--muted);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.tab-item.active {
  color: var(--brand);
  border-bottom-color: var(--brand);
  font-weight: 600;
}
</style>
