# 北京师范大学学生综合测评管理系统

版本：1.0.0rc4（发布候选版）。正式上线前须完成目标环境验收，不把源码交付或本机测试当作生产验收结论。

## 文档

直接用浏览器打开下列文件；无需先启动系统。

- `docs/图文操作教程.html`：首次打开网址、登录、学生提交、教师各阶段操作、审核、公示、导出、管理员操作。
- `docs/使用手册.html`：角色、字段、规则、导入格式、权限与工作流程。
- `docs/部署与维护说明.html`：本机、Docker Compose、Linux systemd，备份恢复与升级。
- `docs/逐项核查与验收记录.html`：需求／页面／实现／测试对照及未完成的基础设施验收。
- `docs/外部AI接入与表格回填指南.html`：接口skill、网页AI任务、逐阶段操作及错误修正。

运行后，界面“使用手册”和“首次使用图文教程”分别打开 `/manual`、`/tutorial`。

## 部署与管理入口

在当前代码目录运行：

```sh
python manage.py
```

这是交互菜单，包含配置、依赖安装或构建、初始化、启动、状态、日志、停止、重启、备份、校验、空目录恢复、管理员密码恢复与原生服务配置生成。

Windows也可双击 `管理工具.cmd`；Linux／macOS可运行 `sh manage.sh`。已运行系统保留原配置与数据，不重新生成密钥。

### 本机演练

```sh
python manage.py configure --mode local
python manage.py install
python manage.py init-db
python manage.py start
```

默认访问 `http://localhost:8000`。管理员账号为 `admin`，初始密码与独立入口取本机 `.env`；无通用默认密码。开发模式不作为公网正式部署。安装依赖需要软件源或事先准备的匹配平台wheel目录，不含离线镜像。

### Docker服务器

```sh
python manage.py configure --mode docker --domain 实际域名
python manage.py install
python manage.py init-db
python manage.py start
```

域名、HTTPS、管理员TOTP、ClamAV、数据库角色和灾备都须在目标主机验证。原生Linux使用 `configure --mode native` 与 `native-files`；详细先决条件、服务用户、数据库、网关见部署说明。

## 验证

```sh
python -m pip install -r requirements-dev.txt
python -m pytest --junitxml=artifacts/test-results.xml
python tests/ui_walkthrough.py --output artifacts/ui
python tests/cli_lifecycle.py --output artifacts/cli-report.json
python tests/export_scale.py --output artifacts/export-scale
python tests/docs_visual.py --output artifacts/docs
```

界面脚本需要本机Chromium、Playwright和中文字体；可分别用 `CHROMIUM` 与 `CJK_FONT` 指定本机路径，字体不随代码包提供。运行方式为离线Chromium DOM与真实ASGI API桥接，不冒称浏览器HTTPS端到端。CLI生命周期验证在Linux、预装依赖环境执行真实本机进程和HTTP。Docker／PostgreSQL／systemd／Windows／macOS／真实HTTPS／TOTP／ClamAV／真机、并发容量和实际Excel／WPS打印验收尚需部署方完成。700人规模测试是Schema校验后批量构建、串行运行真实计算及导出，不是校园峰值或并发上传验收。

## 兼容性与数据

界面、手册和新导出使用正式系统名称。保留 `clearledger` 技术目录、Compose项目名／卷名、数据库名和 `.zcp` 格式标识，以免改名导致旧系统使用空卷或旧档案失效。保留rc1／rc2／rc3档案读取兼容逻辑；本轮执行合成工程往返。不承诺旧程序能读取新版本可空字段；回滚须恢复与旧代码对应的备份。

源码包不包含真实学生资料、数据库、初始密码、服务令牌或部署密钥。本轮示例包、测试工程、截图与模板全部使用合成账号与材料；本轮没有重新评审或重新分发真实学生材料。不得公开 `.env`、系统备份、真实 `.zcp` 或导出材料。

`app/`为服务及前端，`agent/`为外部Agent技能和无模型客户端，`scripts/`为部署管理，`tests/`为验证，`docs/`为文档。系统不包含内置LLM、后台Agent、CDN或第三方遥测。PDF输出不分发字体文件。

## 本版导出与模板

最终XLSX总表按全部规则层级生成合并表头，叶子分数／描述成对，各级有小计。每条描述为“日期 描述【分数】”，取最大值舍弃保留原值与原因，包括上级舍弃。上限不分摊到单个成果。总表包含结果、全部分类数值、所有成果、计算轨迹、规则、课程和导出说明七张工作表。界面可按分类、个人或固定公示版本导出；所有导出遵守教师授权或学生本人公示范围。

个人PDF每页一张图片，说明在图片上方；长说明续页重复展示同一图并标明不是新增成果。未提交个人图片时使用个人成果表查看名单类奖项和课程，不生成空白／假图片PDF。材料包同时包含多级汇总XLSX与结构化HTML。

个人材料、公共来源及公共行增加 `has_stamp: bool | null`、`stamp_text: str | null`。未知不推断为无章，盖章事实不自动决定有效性；公共行null继承来源，明确是／否覆盖。接口、页面、版本、快照、导出、归档均保留。

学生名册、名单类奖项、课程长表均有空白XLSX／CSV模板。第一张XLSX只含表头与空行，示例独立存放；工程下载可带实际分类及名册参考。模板见界面或 `docs/templates/`，只对结构化教师输入开放，不作为学生自评输入。

本轮记录以 docs/validation/summary.json 和各独立报告为准。包含169项回归，以及连续阅卷、框选、右键、截屏、跨页选择和网页AI回填测试；不能视为生产验收证书。

PDF可选视觉检查（运行应用不需要此依赖）：

```sh
python -m pip install -r requirements-visual.txt
python tests/export_visual.py --input artifacts/export-scale --output artifacts/pdf
```

报表从已保存版本和唯一计分引擎读取，不生成第二套Excel可编辑计分公式。Excel数值精度与物理行列限制客观存在；超过明确限额返回错误，可缩小分类／工程范围后导出，不静默删掉数据。

## rc4操作与互操作

见 `docs/外部AI接入与表格回填指南.html`，运行时 `/ai-guide`。接口Agent提交必须带判断时保存的 `--context context.json`。网页AI使用签名任务包，先校验预览，再整批确认；计算总表不能导入。

桌面截图在用户授权后仅截一帧并立即停止共享，预览裁剪确认后才加入待上传列表；正式使用需HTTPS及原生授权验收。大表支持列筛选、排序、框选与右键，阅卷队列跨页选择保存于本轮队列。

新增可重跑脚本：`tests/ui_interactions.py`（原生HTTP，限制环境可显式 `--bridge`）、`tests/task_scale.py`（840张回填）、`tests/test_interchange_rc4.py`（严格诊断、事务及权限）。
