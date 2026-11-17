# 诊断与修正

系统返回TableError的HTTP正文为 `{"detail":{"saved":false,"errors":[...],"error_count":...,"truncated":false}}`。下载的清单为detail内对象。位置字段包含工作表、原始物理行号、列字母、字段、错误码、说明。CSV多行引号内容不改变原始起始行定位。任务级错误不虚构行号。

- UNKNOWN_COLUMN / EXTRA_CELL / DUPLICATE_FIELD：恢复原模板表头和列数。不可删除有业务含义的数据后假装校验通过，应先向教师说明。
- FORMULA_NOT_ALLOWED：请在获准来源中取得确定值，再以文本填入。不能把公式计算或宏当成系统执行的一部分。
- IDENTITY_MISMATCH / OUT_OF_TASK / DUPLICATE_ID：从原始材料索引复制身份；任务外材料另申请新任务，不能伪造固定列。
- INVALID_CATEGORY：只使用当前参考工程叶子ID，不按名称相似猜测分类。
- INVALID_VALUE / INVALID_PERIOD：核对枚举、日期、分母、系数、十进制字符串和非有效0分约束；不能虚构日期以落在参评期内。
- ANNOTATION_CONFLICT / PERMISSION：人工结论或版本已经变化时退出旧任务，交教师处理或重新导出；不可改版本数字越过检查。
- 任务签名、期限、来源版本错误：保留原文件，由教师重新生成；不要让模型重写凭据。

每次最多显示200项具体错误并给出总量／截断提示。修正后再运行全表校验；存在一条错误则整批零写入。通过预览仍非实际保存，必须由教师确认。缺失内容留为待判断，不能为了消除报错而编造事实。
