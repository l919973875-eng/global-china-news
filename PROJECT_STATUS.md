# 项目状态 · 云端版 v0.2

- 运行方式：GitHub Actions + GitHub Pages
- 本地安装：不需要
- 固定来源：311
- 来源类型：247 新闻媒体 / 38 智库研究机构 / 26 政府及国际机构
- 调度：每 6 小时完整巡检一次全部来源 + 支持手动运行
- 全球补充：GDELT DOC API
- 保存：45 天，最多 50,000 条
- 网页展示：最多 20,000 条
- 分类：direct / indirect / potential
- 明确不做：有利/不利判断、政策研判、自动改写原标题
- AI：OpenAI API Key 可选；配置后用于广义涉华判定，没有 Key 自动退回规则模式

## 已保留的回归逻辑

1. `EU announces new restrictions on Chinese chip firms` → direct
2. `Hungary's new government vows to restore relations with Brussels after election` → potential
3. `Hungary wins dramatic football match in extra time` → unrelated

## 下一阶段

1. 311 来源逐站真实运行后建立成功率榜与失效源清单；
2. 将可用 RSS 明确写入 sources.yaml，减少首页扫描；
3. 扩源到 1,000—3,000 个国外来源；
4. 扩大中国海外利益关联库；
5. 数据量上升后迁移到云数据库，避免 Git 仓库长期膨胀。
