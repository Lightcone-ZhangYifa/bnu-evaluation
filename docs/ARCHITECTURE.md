# 架构与数据合同

## 权威边界

数据库是身份、权限、工程、名单类奖项版本和审计的权威；私有对象目录是不可变文件原件。JSON 工程只保存对象哈希引用。图片预览可重建，不是第二份评分事实。结果XLSX/PDF是只读派生输出。个人局部标注另有严格限定字段与身份版本的CSV/XLSX回填，不开放总分反向导入。

业务对象：College、Student、Account、Folder、Project、Resource、PublicList/PublicVersion、AgentToken、Feedback、Audit。项目独立于学生主身份，可跨年度重复参评。同院教师仍须负责/被授权才能访问工程。班级审核者不能处理自身材料。固定管理员不属于评分权限链。

## 局部材料

原始 record：id / resource_sha / student_id / submitted_at / submitted_by / parent_id / request_id / incremental / idempotency_key / annotations[]。原件与提交信息不可编辑、删除。每个 annotation version 存操作人、时间、规则合同哈希、解释版本、reviewed 及严格类型 data。

data 含八类状态、材料日期（年/月/日允许不同粒度）、图像关键内容、叶子 category_id、细则原话项目与情形、出处、局部分、基础分/系数/分母、AI/人工备注、向学生说明student_note、has_stamp / stamp_text、outcome_key、support_for / duplicate_of / replaces。分类路径由叶子和父指针推导，不另存重复路径。关系仅允许同一学生；检测循环。明确替代需要教师确认新有效记录，原记录仍在。

原件字节去重与成果语义去重是两回事。不同截图可以指向同一成果；系统通过成果键发现重复，通过人工确认关系决定唯一计分项，不能保证单凭图像自动识别所有重复或伪造。

## 算分

每节点包含稳定 ID、parent、name、aggregate(sum/max)、cap、floor、weight、negative_policy、required、source；flat adjacency 而非固定深度嵌套。校验唯一根、无环、连通、合法叶子、合法十进制。10,000 节点规模保护，不限制固定层数；回归已验证 6,000 层。

后序迭代计算。signed：带符号求和/取最高，乘权重，再裁上下限。separate：正分聚合乘权重裁限，之后加独立负分；它是一种明确可选语义，不是对未知细则的擅自解释。没有默认零分下限。中间值 Decimal 高精度，展示时才按 display_places 四舍五入。每个节点保留原始输入、raw_sum、selected_ids、selected_before_weight、weighted_before_cap、cap_delta、value 和独立扣分。

分类名称、层级、来源含义或学院解释变化使已有局部标注失效待确认；单纯更改上限、权重、sum/max 重算。名单类奖项绑定记录合同/解释版本，语义变化需要重新确认绑定。已有公示快照不受影响。

## 一致性和并发

同一个工程作为带 revision 的聚合，SQL UPDATE WHERE id AND revision 比较交换；名单类奖项同样采用乐观版本控制。冲突 HTTP409，拒绝静默覆盖。操作与 HMAC 审计在同一数据库事务提交。文件先持久化后入库，数据库失败可能留下不引用对象；不会因此丢失已引用原件。只读资源/公共版本/审计有数据库不可变触发器。业务账号无 DDL 权限；离线数据库所有者仍是可信运维边界，不宣称能抵御数据库超级用户篡改。

## 公示与增量

阶段 setup→scheduled/submission→review→publication→closed→archived。scheduled/review 可由服务端时间从 submission 派生。每个公示是完整不可变结果快照，带 input_hash、规则版本和名单。普通学生 API 只返回本人材料/申请/结果，公示前无分数；公共原件不向学生开放。

阶段公示可以带待办，明确不是完整成绩。最终公示要求必填基线齐全、无待定/重复/失效项、无未处理或尚在允许期内的申请，并且申诉期已结束。结项还验证输入指纹与最终快照相符。补传只追加；API 无覆盖删除旧材料入口。

## .zcp v1

标准 ZIP 容器：manifest.json（格式名/版本/每个成员大小与SHA256）、project.json（工程、名册、历史公共版本、审计出处）、objects/<sha256>。名单类奖项只封装当前班级行；原始来源文件保留完整仅供有权限教师。公共版本历史全部保留。

验证成员清单、SHA、大小、路径、符号链接、压缩率、数据类型、分类树、时间、身份和引用。重导入分配新的工程/名单 ID，按学院+学号匹配学生；姓名冲突拒绝。账户密钥、会话、权限不导入；旧公示不会自动恢复可见，来源确认后再公示。历史 HMAC 留作来源记录，新系统不凭未知旧密钥承诺原发行者真实性。可复制 `.zcp` 作为档案，不应作为任意可信输入执行。

## 部署边界

Python3.13 / FastAPI / SQLAlchemy；生产 PostgreSQL17，开发 SQLite WAL。单班项目 JSON 聚合适合预期班级规模，未开展校园并发负载验收；不是无限容量分布式存储。20MB/图、40MP、40MB/教师文档、每人200图、每工程12,000条材料、2,000人、512MB可移植工程、32MB工程JSON，另外归档成员数有保护。超过配额需要扩容设计与回归，而不是删除原件。原始附件可有法律规定的保留期，自动删除未实现，应由学校制定审批清理制度。

## 派生导出合同

`app/reporting.py`只读取compute结果。全层级表头来自同版规则树；每叶子输出原局部分描述与该节点计算分，每祖先保留自身小计。`selected_ids`与`tied_ids`决定最大值舍弃，沿父链追踪；`negative_policy=separate`的扣分不因正向max被丢弃。封顶不分配回单条输入。

`app/tabular.py`使用标准库写OOXML，无外部办公引擎和公式再评分。明细字符串为inlineStr，身份文本不丢0，分数为numeric；支持SheetSpec合并、格式、验证、打印和冻结。长汇总描述续行不截断；无法表示的单元格／行列明确拒绝。XLSX是人用只读派生文件，不是比Decimal档案更高精度的权威。

`app/pdf_export.py`每页仅一张等比未裁切预览，结构化字段在上方，无封面或纯文字附录；长字段续页重复同图。无图学生明确使用个人XLSX。源SHA始终指原件，不把预览当原件。

快照导出读取所选快照的规则、名册和结果，不取后来更改的材料评分、名单行或课程。旧字段缺失仍是未判定。学生输出移除内部AI／人工备注及后台操作账号，只含其本人当前公示。教师导出需要对应工程审核权，Agent和管理员不继承教师报表权；后台表独立管理员端点。成功下载在同事务记录审计，不修改项目revision。

`StampInfo`的has_stamp是StrictBool可空，stamp_text最多4000字符；非true不得含章面文字。公共行null表示使用来源，false/true明确覆盖，true空文字不猜来源文字。盖章不自动推导status或score；Agent只能描述可见事实，不能签发真实性认定。兼容旧annotation的幂等对比先按当前schema补可空默认值，不改变历史字节。

`import_templates.py`生成students/public/academic三类XLSX与CSV。第一表空数据、第二说明、第三独立合成示例，工程参考表不被读成导入数据。源数据的手工维护仍限教师结构化输入，学生只能传图片。

## rc4输入与交互边界

`table_reader`负责字节边界、工作表／物理行列与纯表格安全；`import_compiler`负责名册、名单行、课程的类型、身份和语义。预览只返回编译结果，确认时重读输入和目标身份指纹。HTTP与表格外部标注都调用`domain.annotation_version`，不复制规则计算。

`offline_tasks`使用现有密钥签名工程限定任务，无任务数据库副本。凭据固定原件哈希、身份、标注与规则／辅助说明／来源版本，期限7日。提交仍需教师登录、工程权限及乐观版本锁。同内容同任务重试按任务ID＋材料ID幂等；任一行冲突整批不写。材料图片文本不是系统指令。标注来源web_ai不等于人工核对，student_note等人类字段禁止外部写入。

`privacy.personal_projection`是学生公开结果的唯一白名单，学生API、XLSX、PDF共享。未来新增内部字段默认不公开，人工单独填写的student_note只有公示后本人可见，不借用内部备注作通知。

前端`review-ui`维护固定队列、草稿和保存跳转；`workflow-ui`承接授权批量、表格预览与来源钻取；`collections`只负责选择／菜单／筛选而不直接写服务端；`capture`只生成本地File，不认识账号或计分规则。所有批量操作在服务端再次逐项校验，菜单不是授权边界。新模块使用现有原生JavaScript及CSS，不引入前端框架迁移风险。

文件写入先校验已有哈希对象，不能静默覆盖损坏原件；SQLite备份连接显式关闭；原件与HMAC审计仍由可信系统管理员维护。原始材料永久清理、校园级并发容量与外部AI平台资料留存政策属于需学院另行确定的运维／制度边界，不假定软件已经自动解决。
