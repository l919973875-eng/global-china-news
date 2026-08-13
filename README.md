# 全球广义涉华新闻标题库 · 免安装云端版 v0.2

这是给**非技术用户**使用的 GitHub 云端版本。你的电脑不需要 Docker、WSL、Python，也不需要一直开机。

系统做的事情只有：

1. GitHub Actions 定时巡检全部配置来源；
2. 抓取国外新闻、智库、调查机构、政府/国际机构公开页面中的新标题；
3. 判断是否属于“广义涉华”：直接涉华 / 间接涉华 / 潜在涉华；
4. 不判断对中国有利还是不利；
5. 把原标题、来源、时间、国家/地区、命中原因、原文链接生成网页；
6. GitHub Pages 自动发布网页。

当前固定来源库：**311 个**，所有来源统一巡检，不设置“智库高优先级”或“新闻媒体高优先级”。

---

## 一、你需要准备什么

只需要：

- 一个 GitHub 账号；
- 一个浏览器；
- 建议准备一个 OpenAI API Key，用于更准确地判断“广义涉华”。没有 API Key 也能运行，但只使用规则筛选。

**不需要安装任何软件。**

---

## 二、第一次部署（按顺序点即可）

### 第 1 步：创建 GitHub 仓库

登录 GitHub 后：

1. 点击右上角 `+`；
2. 点击 `New repository`；
3. Repository name 填：`global-china-news`；
4. 如果你使用 GitHub Free 并希望使用 GitHub Pages，请选择 **Public**；
5. 点击 `Create repository`。

> 隐私提醒：GitHub Pages 网页默认面向互联网公开。虽然新闻标题本身来自公开来源，但“你筛选出的广义涉华新闻集合”可能反映研究关注点。如果你不希望结果公开，不要启用 Pages，联系我把系统改成“私有仓库浏览版”。

### 第 2 步：上传本项目

1. 在 Windows 中右键本压缩包 → `全部解压`；
2. 进入解压后的 `global_china_news_cloud_v0.2` 文件夹；
3. 回到刚建立的 GitHub 仓库；
4. 点击 `Add file` → `Upload files`；
5. 把解压文件夹里的**全部文件和文件夹**拖到网页上传区域，包括 `.github`、`config`、`data`；
6. 页面下方点击 `Commit changes`。

GitHub 网页支持直接拖入文件夹，因此不需要 Git、GitHub Desktop 或命令行。

### 第 3 步：启用 GitHub Pages

进入仓库：

`Settings` → 左侧 `Pages` → `Build and deployment` → `Source`

选择：

`GitHub Actions`

保存即可。

### 第 4 步：配置 OpenAI API Key（建议）

进入：

`Settings` → `Secrets and variables` → `Actions` → `Secrets` → `New repository secret`

Name 填：

`OPENAI_API_KEY`

Secret 填你的 API Key，保存。

不要把 API Key 写进任何代码、README、YAML 或公开页面。

如果暂时没有 API Key，可以跳过这一步。系统会显示“未配置AI密钥，当前使用规则筛选”。

### 第 5 步：第一次手动运行

进入仓库顶部：

`Actions`

左侧选择：

`全球广义涉华新闻云端抓取`

然后点击右侧：

`Run workflow` → `Run workflow`

通常等待几分钟到几十分钟。311 个来源中部分网站可能超时、封禁自动访问或临时不可用，这不会阻止其他来源继续运行。

### 第 6 步：打开新闻网页

运行成功后：

`Settings` → `Pages` → `Visit site`

网页地址通常类似：

`https://你的GitHub用户名.github.io/global-china-news/`

以后只打开这个网址即可。

---

## 三、系统什么时候自动抓？

当前设置为**每 6 小时完整巡检全部来源一次**，不是抽样。

北京时间大约：

- 02:17
- 08:17
- 14:17
- 20:17

GitHub 的定时任务可能有少量排队延迟，因此不是严格到秒执行。

你也可以随时进入 `Actions` 手动点击 `Run workflow`。

---

## 四、新闻网页能做什么

网页支持：

- 最近 24 小时 / 3 天 / 7 天 / 30 天；
- 直接涉华 / 间接涉华 / 潜在涉华；
- 国家/地区筛选；
- 新闻媒体 / 智库 / 官方机构筛选；
- 来源筛选；
- 标题、命中原因和实体关键词搜索；
- 点击跳转原文；
- 下载 CSV。

系统页面只显示**原标题**，不会替你改写标题。

---

## 五、“广义涉华”怎么判断

### direct：直接涉华

例如 China、Chinese、Beijing、Huawei、BYD、CATL、Taiwan 等直接出现在标题/摘要中。

### indirect：间接涉华

标题不一定出现 China，但直接命中中国企业、海外项目、港口、矿山、铁路等关联实体。

### potential：潜在涉华

标题和正文摘要可能完全不出现中国，但事件发生在中国有重要利益暴露的国家/地区，并涉及：

- 大选、政权更迭；
- 外交路线变化；
- 投资、贸易、关税、制裁；
- 产业、矿业、能源、港口、基础设施政策；
- 军事、战争、政变；
- 抗议、罢工、骚乱；
- 半导体、AI、网络、数据、核、太空等重大政策变化。

例如：

`Hungary's new government vows to restore relations with Brussels after election`

即使标题没有 China，也会因为“匈牙利 + 政权/外交路线变化 + 中国在当地存在重要投资和项目”进入潜在涉华池。

系统到此为止，不判断该事件对中国有利还是不利。

---

## 六、数据保存

当前版本默认：

- 保存最近 45 天；
- 最多保存 50,000 条广义涉华标题；
- 网页最多加载最近 20,000 条；
- 每次抓取后自动更新 `data/articles.json`；
- CSV 位于网页的“下载CSV”。

后续数据量明显增加后，可以把持久化改成云数据库，网页使用方式不变。

---

## 七、你以后最常修改的两个文件

### `config/sources.yaml`

全球抓取来源库。

增加新的国外新闻媒体、地方媒体、行业网站、智库、民调机构、政府部门，都加在这里。

### `config/china_interest_map.yaml`

中国海外利益关联库。

用于判断“标题没有 China，但实际上值得中国研究人员看到”的新闻。

以后应持续增加：

- 国家/地区；
- 中国企业；
- 海外港口；
- 矿山；
- 电站；
- 铁路；
- 工业园；
- 电信项目；
- 资源项目；
- 重点城市和当地别名。

---

## 八、如果 Actions 报错

优先查看：

`Actions` → 最近一次运行 → `crawl-and-deploy`

常见情况：

- `Pages` 未启用：去 `Settings → Pages → Source → GitHub Actions`；
- `403` / push 权限错误：去 `Settings → Actions → General → Workflow permissions`，允许工作流拥有读写权限；
- 某些来源超时：正常，程序会记录失败来源并继续其他站点；
- OpenAI API 报错：系统会自动退回规则模式，不会让整次抓取失败；
- 某网站长期抓不到：后续给该网站增加 RSS、RSSHub、官方 API 或专用适配器。

---

## 九、项目结构

```text
.github/workflows/cloud-crawl.yml   云端定时任务
cloud_runner.py                     抓取 + 广义涉华筛选 + 生成网页
requirements.txt                    云端自动安装的 Python 依赖
config/sources.yaml                 311 个每日巡检来源
config/china_interest_map.yaml      中国海外利益关联库
data/articles.json                  自动积累的新闻标题
site/                               每次运行自动生成的网页
```

你本人正常使用时，不需要修改 `cloud_runner.py`。
