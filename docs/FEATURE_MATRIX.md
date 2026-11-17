# 需求与实现对照

完整阶段清单见《逐项核查与验收记录.html》。以下是rc4新增及保留能力；实现、当前验证、生产验收分别记录。

| 要求 | 主要实现 | 验证记录／入口 |
|---|---|---|
| 桌面截屏与反馈图片 | capture.js → workflow-ui.js待上传File队列 | ui_interactions：帧提取、停止、裁剪、取消、拒绝、粘贴 |
| 框选／多选／右键菜单 | collections.js通用组件 | 鼠标、Shift、Esc、菜单键、60/10分页、双轴滚动 |
| 连续阅卷／一键判定 | review-ui.js固定队列与幂等提交 | 快捷键、草稿保护、失败重试、最后一项 |
| 便利批量与原子提交 | workflow_routes.py、workflow-ui.js | test_interchange_rc4：身份版本、权限、自审与整批零写 |
| 成果定位 | achievement-records、结构化记录、精确ID打开来源 | 总表→成果→同一材料，绑定公共版本定位 |
| 编译式表格校验 | table_reader.py → import_compiler.py | CSV物理行号、XLSX坐标、公式／额外列、类型、版本、全表诊断 |
| 网页AI任务包及回填 | offline_tasks.py，共用domain.annotation_version | 840行末行错误零写、预览零写、原子提交、幂等复试 |
| 外部接口Agent | agent/clearledger，版本上下文 | --context强制、权限及来源变化拒绝，逐阶段references |
| 学生隐私字段一致 | privacy.personal_projection | API／XLSX／PDF白名单，内部备注隐藏，student_note公开给本人 |
| 多级总表／一图一页PDF | reporting.py、pdf_export.py | 独立Decimal、700人13300成果、27页图文边界 |
| 盖章及不可变历史 | StampInfo、domain、版本与档案 | bool严格、继承、快照、工程往返 |
| 导入模板 | import_templates.py、相应页面下载 | 三类XLSX／CSV空白输入，示例与参考分表 |
| 多方式部署与管理CLI | manage.py、scripts/console.py、deploy/ | 本机8项实际进程／HTTP／备份恢复；其余现场验收 |
| 五份说明文档 | docs/HTML与static副本 | 目录、图片、2视口排版与HTTP字节一致性 |

不可变图片原件、旧标注、旧名单、已发布快照、审计记录不提供覆盖删除；未提交图片／编辑草稿可移出，工程可回收恢复。总表只有导出，没有反向手填分数导入。权限不足不显示或不执行批量动作；教师私有名单与系统后台不因右键菜单而越权。

数量及方法以docs/validation/*.json、regression.xml为准；不把当前Linux/SQLite及桥接浏览器检查冒称全部生产环境验收。
